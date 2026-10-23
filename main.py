import os
import re
import json
import time
import asyncio
import logging
import urllib.request
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
    video_prompt: str

# ==========================================
# 1. OpenMontage 템플릿 로더
# ==========================================
def load_openmontage_template(template_path: str) -> Dict[str, Any]:
    if not os.path.exists(template_path):
        logger.warning(f"템플릿 파일이 없습니다 ({template_path}). 기본 16:9 설정을 생성합니다.")
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
# 2. Whisper 기반 자막 싱크(타임코드) 자동 추출
# ==========================================
def extract_subtitles_with_whisper(audio_path: str, model_size: str = "base") -> List[Dict[str, Any]]:
    logger.info(f"Whisper 모델({model_size})로 자막 타임코드 추출 중: {audio_path}")
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, language="ko")
    
    subtitles = []
    for segment in result.get("segments", []):
        subtitles.append({
            "start": segment["start"],
            "end": segment["end"],
            "text": segment["text"].strip()
        })
    return subtitles

# ==========================================
# 3. TTS 음성 합성 (Edge-TTS / External TTS)
# ==========================================
async def generate_dialogue_audio(lines: List[ScriptLine]) -> List[str]:
    audio_files = []
    for idx, line in enumerate(lines):
        output_file = f"temp_dialogue_{idx}.mp3"
        voice = "ko-KR-InJoonNeural" if line.character == "Narrator" else "ko-KR-HyunsuNeural"
        communicate = edge_tts.Communicate(text=line.text_ko, voice=voice)
        await communicate.save(output_file)
        audio_files.append(output_file)
        logger.info(f"[{line.character}] 음성 생성 완료: {output_file}")
    return audio_files

# ==========================================
# 4. 멀티 AI 비디오 생성 Fallback (Kling -> Runway)
# ==========================================
def generate_scene_video(prompt: str, output_filepath: str):
    kling_key = os.environ.get("KLING_API_KEY")
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
                        logger.info(f"Kling 비디오 생성 완료: {output_filepath}")
                        return
        except Exception as e:
            logger.warning(f"Kling 생성 실패 ({e}), Runway로 전환합니다.")

    # 2순위: Runway AI
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
                        logger.info(f"Runway 비디오 생성 완료: {output_filepath}")
                        return
        except Exception as e:
            logger.error(f"Runway 생성 실패: {e}")

    raise RuntimeError("모든 비디오 API 생성에 실패하였습니다.")

# ==========================================
# 5. OpenMontage 규격 비디오/음성/자막 레이어 자동 합성
# ==========================================
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

        # 음성 길이에 맞춰 비디오 속도/길이 맞춤
        duration = audio_clip.duration + 0.5
        if video_clip.duration < duration:
            num_loops = int(duration // video_clip.duration) + 1
            video_clip = concatenate_videoclips([video_clip] * num_loops).subclipped(0, duration)
        else:
            video_clip = video_clip.subclipped(0, duration)

        video_clip = video_clip.with_audio(audio_clip)

        # Whisper로 해당 음성의 타임코드 추출 후 자막 오버레이
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
    final_video.write_videofile(
        output_path,
        fps=proj_cfg.get("fps", 30),
        codec='libx264',
        audio_codec='aac',
        remove_temp=True,
        threads=4
    )

    # 메모리 해제 및 임시 파일 정리
    final_video.close()
    for clip in scene_clips:
        clip.close()

# ==========================================
# 메인 실행 파이프라인
# ==========================================
async def async_main():
    logger.info("[1/4] OpenMontage 템플릿 로드 중...")
    template = load_openmontage_template("openmontage_config/template.json")

    # 샘플 예시 대본
    sample_lines = [
        ScriptLine(character="Narrator", text_ko="보관소 가장 깊은 곳, 누군가의 멈춰진 태엽이 빛나고 있습니다.", video_prompt="16:9 cinematic 3D animation, glowing clockwork inside gothic clocktower"),
        ScriptLine(character="Ein", text_ko="이 태엽을 다시 감는다면... 새로운 시간이 흐를 수 있을까요?", video_prompt="16:9 cinematic 3D animation, silver fox holding crystal key, warm light")
    ]

    logger.info("[2/4] 대사 음성 생성 중...")
    audio_files = await generate_dialogue_audio(sample_lines)

    logger.info("[3/4] OpenMontage 비디오/음성/Whisper 자막 레이어 자동 합성 중...")
    render_montage_episode(sample_lines, audio_files, template, "output_final.mp4")

    # 임시 음성 파일 삭제
    for af in audio_files:
        if os.path.exists(af):
            os.remove(af)

    logger.info("[4/4] 렌더링 완료: output_final.mp4")

if __name__ == "__main__":
    asyncio.run(async_main())
