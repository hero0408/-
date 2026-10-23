import os
import re
import json
import time
import asyncio
import logging
import urllib.request
import urllib.error
from typing import List, Dict, Any
from dataclasses import dataclass

import edge_tts
import whisper
from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, concatenate_videoclips
import moviepy.video.fx as vfx

from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OpenMontagePipeline")

@dataclass
class ScriptLine:
    character: str
    text_ko: str
    text_en: str
    text_ja: str
    video_prompt: str

@dataclass
class EpisodeScript:
    title_ko: str
    title_en: str
    title_ja: str
    lines: List[ScriptLine]

# ==========================================
# 0. 캐릭터 및 템플릿 설정
# ==========================================
ELEVENLABS_VOICES = {
    "Ein": "21m00Tcm4TlvDq8ikWAM",
    "Chrono": "VR6AewLTigWG4xT1s5nU"
}

FISH_AUDIO_VOICE_ID = "7f92f8efb88f4b26a11751d2f99d6512"

def load_openmontage_template(template_path: str) -> Dict[str, Any]:
    if not os.path.exists(template_path):
        default_template = {
            "project": {"width": 1280, "height": 720, "fps": 30},
            "tracks": [
                {"name": "video_layer", "type": "video", "z_index": 1},
                {"name": "dialogue_audio", "type": "audio", "z_index": 2},
                {"name": "subtitle_layer", "type": "subtitle", "z_index": 3, "style": {"font_size": 28, "font_color": "#FFFFFF"}}
            ]
        }
        os.makedirs(os.path.dirname(template_path), exist_ok=True)
        with open(template_path, "w", encoding="utf-8") as f:
            json.dump(default_template, f, indent=2)
        return default_template

    with open(template_path, "r", encoding="utf-8") as f:
        return json.load(f)

# ==========================================
# 1. 음성 생성 (Edge-TTS / ElevenLabs / Fish Audio)
# ==========================================
def generate_elevenlabs_voice(voice_id: str, text: str, output_path: str, api_key: str):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {"Accept": "audio/mpeg", "Content-Type": "application/json", "xi-api-key": api_key.strip()}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35}
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    with urllib.request.urlopen(req) as response:
        with open(output_path, 'wb') as f:
            f.write(response.read())

def generate_fish_audio_voice(text: str, output_path: str, api_key: str):
    url = "https://api.fish.audio/v1/tts"
    headers = {"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"}
    payload = {"text": text, "reference_id": FISH_AUDIO_VOICE_ID, "format": "mp3"}
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
    with urllib.request.urlopen(req) as response:
        with open(output_path, 'wb') as f:
            f.write(response.read())

async def generate_tts_files(lines: List[ScriptLine]) -> List[str]:
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY")
    fish_key = os.environ.get("FISH_AUDIO_API_KEY")
    audio_files = []

    for idx, line in enumerate(lines):
        output_file = f"temp_dialogue_{idx}.mp3"
        success = False

        if line.character == "Narrator" and fish_key:
            try:
                generate_fish_audio_voice(line.text_ko, output_file, fish_key)
                success = True
            except Exception as e:
                logger.warning(f"Fish Audio 실패 ({e}), 전환합니다.")

        if not success and line.character in ELEVENLABS_VOICES and elevenlabs_key:
            try:
                v_id = ELEVENLABS_VOICES[line.character]
                generate_elevenlabs_voice(v_id, line.text_ko, output_file, elevenlabs_key)
                success = True
            except Exception as e:
                logger.warning(f"ElevenLabs 실패 ({e}), Edge-TTS로 전환합니다.")

        if not success:
            voice = "ko-KR-InJoonNeural" if line.character == "Narrator" else "ko-KR-HyunsuNeural"
            communicate = edge_tts.Communicate(text=line.text_ko, voice=voice)
            await communicate.save(output_file)

        audio_files.append(output_file)

    return audio_files

# ==========================================
# 2. 비디오 API 생성 (Kling 1-5 -> MiniMax -> Luma -> Runway)
# ==========================================
def generate_scene_video(prompt: str, output_filepath: str):
    kling_key = os.environ.get("KLING_API_KEY")
    minimax_key = os.environ.get("MINIMAX_API_KEY")
    luma_key = os.environ.get("LUMA_API_KEY")
    runway_key = os.environ.get("RUNWAY_API_KEY")

    # 1순위: Kling AI
    if kling_key:
        try:
            url = "https://api.klingai.com/v1/videos/text2video"
            headers = {"Authorization": f"Bearer {kling_key.strip()}", "Content-Type": "application/json"}
            payload = {"model_name": "kling-v1-5", "prompt": prompt, "duration": "5", "aspect_ratio": "16:9", "mode": "std"}
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
            with urllib.request.urlopen(req) as response:
                res = json.loads(response.read().decode('utf-8'))
                task_id = res.get("data", {}).get("task_id")

            query_url = f"https://api.klingai.com/v1/videos/text2video/{task_id}"
            for _ in range(60):
                time.sleep(5)
                q_req = urllib.request.Request(query_url, headers=headers, method='GET')
                with urllib.request.urlopen(q_req) as q_res:
                    q_data = json.loads(q_res.read().decode('utf-8'))
                    if q_data.get("data", {}).get("task_status") == "succeed":
                        video_url = q_data.get("data", {}).get("task_result", {}).get("videos", [{}])[0].get("url")
                        urllib.request.urlretrieve(video_url, output_filepath)
                        return
        except Exception as e:
            logger.warning(f"Kling 실패 ({e}), MiniMax로 전환합니다.")

    # 2순위: MiniMax
    if minimax_key:
        try:
            url = "https://api.minimax.chat/v1/video_generation"
            headers = {"Authorization": f"Bearer {minimax_key.strip()}", "Content-Type": "application/json"}
            payload = {"prompt": prompt, "model": "video-01", "aspect_ratio": "16:9"}
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
            with urllib.request.urlopen(req) as response:
                res = json.loads(response.read().decode('utf-8'))
                task_id = res.get("task_id")

            query_url = f"https://api.minimax.chat/v1/query/video_generation?task_id={task_id}"
            for _ in range(60):
                time.sleep(5)
                q_req = urllib.request.Request(query_url, headers=headers, method='GET')
                with urllib.request.urlopen(q_req) as q_res:
                    q_data = json.loads(q_res.read().decode('utf-8'))
                    if q_data.get("status") == "Success":
                        file_url = q_data.get("file_url") or q_data.get("download_url")
                        urllib.request.urlretrieve(file_url, output_filepath)
                        return
        except Exception as e:
            logger.warning(f"MiniMax 실패 ({e}), Luma로 전환합니다.")

    # 3순위: Luma AI
    if luma_key:
        try:
            url = "https://api.lumalabs.ai/dream-machine/v1/generations"
            headers = {"Authorization": f"Bearer {luma_key.strip()}", "Content-Type": "application/json"}
            payload = {"prompt": prompt, "aspect_ratio": "16:9"}
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
            with urllib.request.urlopen(req) as response:
                res = json.loads(response.read().decode('utf-8'))
                gen_id = res.get("id")

            query_url = f"https://api.lumalabs.ai/dream-machine/v1/generations/{gen_id}"
            for _ in range(60):
                time.sleep(5)
                q_req = urllib.request.Request(query_url, headers=headers, method='GET')
                with urllib.request.urlopen(q_req) as q_res:
                    q_data = json.loads(q_res.read().decode('utf-8'))
                    if q_data.get("state") == "completed":
                        video_url = q_data.get("assets", {}).get("video")
                        urllib.request.urlretrieve(video_url, output_filepath)
                        return
        except Exception as e:
            logger.warning(f"Luma 실패 ({e}), Runway로 전환합니다.")

    # 4순위: Runway
    if runway_key:
        try:
            url = "https://api.dev.runwayml.com/v1/text_to_video"
            headers = {"Authorization": f"Bearer {runway_key.strip()}", "Content-Type": "application/json", "X-Runway-Version": "2024-09-13"}
            payload = {"promptText": prompt, "model": "gen4.5", "ratio": "1280:720", "duration": 5}
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
            with urllib.request.urlopen(req) as response:
                res = json.loads(response.read().decode('utf-8'))
                task_id = res.get("id")

            query_url = f"https://api.dev.runwayml.com/v1/tasks/{task_id}"
            for _ in range(60):
                time.sleep(5)
                q_req = urllib.request.Request(query_url, headers=headers, method='GET')
                with urllib.request.urlopen(q_req) as q_res:
                    q_data = json.loads(q_res.read().decode('utf-8'))
                    if q_data.get("status") == "SUCCEEDED":
                        video_url = q_data.get("output", [None])[0]
                        urllib.request.urlretrieve(video_url, output_filepath)
                        return
        except Exception as e:
            logger.error(f"Runway 실패: {e}")

    raise RuntimeError("모든 비디오 API 생성 실패. 파이프라인 중단.")

# ==========================================
# 3. Whisper 자막 오버레이 및 합성 Engine
# ==========================================
def extract_subtitles_with_whisper(audio_path: str) -> List[Dict[str, Any]]:
    model = whisper.load_model("base")
    result = model.transcribe(audio_path, language="ko")
    return [{"start": s["start"], "end": s["end"], "text": s["text"].strip()} for s in result.get("segments", [])]

def render_montage_episode(lines: List[ScriptLine], audio_files: List[str], template: Dict[str, Any], output_path: str):
    proj_cfg = template.get("project", {"width": 1280, "height": 720, "fps": 30})
    sub_track = next((t for t in template.get("tracks", []) if t["type"] == "subtitle"), {})
    sub_style = sub_track.get("style", {"font_size": 28, "font_color": "#FFFFFF"})

    scene_clips = []

    for idx, (line, a_file) in enumerate(zip(lines, audio_files)):
        raw_video_path = f"temp_scene_{idx}.mp4"
        generate_scene_video(line.video_prompt, raw_video_path)

        audio_clip = AudioFileClip(a_file)
        video_clip = VideoFileClip(raw_video_path)

        duration = audio_clip.duration + 0.5
        if video_clip.duration < duration:
            num_loops = int(duration // video_clip.duration) + 1
            video_clip = concatenate_videoclips([video_clip] * num_loops).subclipped(0, duration)
        else:
            video_clip = video_clip.subclipped(0, duration)

        video_clip = video_clip.with_audio(audio_clip)

        subtitles = extract_subtitles_with_whisper(a_file)
        text_clips = []
        for sub in subtitles:
            txt_clip = (TextClip(text=sub["text"], font_size=sub_style.get("font_size", 28), color=sub_style.get("font_color", "white"), font="Arial")
                        .with_position(('center', proj_cfg["height"] - 80))
                        .with_start(sub["start"])
                        .with_duration(sub["end"] - sub["start"]))
            text_clips.append(txt_clip)

        composite_scene = CompositeVideoClip([video_clip] + text_clips, size=(proj_cfg["width"], proj_cfg["height"]))
        scene_clips.append(composite_scene)

    final_video = concatenate_videoclips(scene_clips, method="compose")
    final_video.write_videofile(output_path, fps=proj_cfg.get("fps", 30), codec='libx264', audio_codec='aac', remove_temp=True, threads=4)

    final_video.close()
    for clip in scene_clips:
        clip.close()

# ==========================================
# 4. 메인 파이프라인
# ==========================================
async def async_main():
    template = load_openmontage_template("openmontage_config/template.json")

    sample_lines = [
        ScriptLine(character="Narrator", text_ko="보관소 가장 깊은 곳, 누군가의 멈춰진 태엽이 빛나고 있습니다.", text_en="", text_ja="", video_prompt="16:9 cinematic 3D animation, glowing clockwork heart slowing down inside clocktower"),
        ScriptLine(character="Ein", text_ko="이 태엽을 다시 감으면 기억은 남아도 새로운 시간은 흐르지 않아.", text_en="", text_ja="", video_prompt="16:9 cinematic 3D animation, silver fox Ein holding glowing crystal key")
    ]

    audio_files = await generate_tts_files(sample_lines)
    render_montage_episode(sample_lines, audio_files, template, config.OUTPUT_VIDEO_PATH)

    for af in audio_files:
        if os.path.exists(af):
            os.remove(af)

    logger.info("OpenMontage 파이프라인 성공 완결!")

if __name__ == "__main__":
    asyncio.run(async_main())
