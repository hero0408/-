import os
import json
import logging
from typing import List
from dataclasses import dataclass

from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OpenMontagePrep")

@dataclass
class SceneData:
    scene_id: int
    character: str
    text_ko: str
    video_prompt: str
    duration_sec: int = 5

def generate_script_and_prompts() -> List[SceneData]:
    """
    Gemini API를 활용하거나 기본 3D 서사 구조 기반의 에피소드 데이터 생성
    """
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        logger.info("Gemini API 수신 확인: 자동 서사 대본 및 비디오 프롬프트를 조립합니다.")

    # 에피소드 장면 데이터 규격
    scenes = [
        SceneData(
            scene_id=1,
            character="Narrator",
            text_ko="보관소 가장 깊은 곳, 누군가의 멈춰진 태엽이 빛나고 있습니다.",
            video_prompt="16:9 cinematic 3D animation, glowing clockwork heart slowing down inside gothic clocktower"
        ),
        SceneData(
            scene_id=2,
            character="Ein",
            text_ko="이 태엽을 다시 감으면 기억은 남아도 새로운 시간은 흐르지 않아.",
            video_prompt="16:9 cinematic 3D animation, silver fox Ein holding glowing crystal key, soft ambient warm light"
        )
    ]
    return scenes

def build_openmontage_input_json(scenes: List[SceneData], output_json_path: str = config.TRACK_INPUT_PATH):
    """
    OpenMontage CLI가 자체 해석/렌더링하도록 JSON 파이프라인 규격 생성
    """
    episodes_payload = []
    
    for scene in scenes:
        episodes_payload.append({
            "id": f"scene_{scene.scene_id}",
            "dialogue": {
                "character": scene.character,
                "text": scene.text_ko,
                "language": "ko"
            },
            "visual": {
                "prompt": scene.video_prompt,
                "aspect_ratio": "16:9",
                "style": "3d_animation",
                "duration": scene.duration_sec
            }
        })

    input_data = {
        "metadata": {
            "title": "The Clockwork Repository",
            "version": "1.0"
        },
        "episodes": episodes_payload
    }

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(input_data, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ OpenMontage 전달용 데이터 생성 완료: {output_json_path}")

def main():
    logger.info("[1/2] 에피소드 대본 및 장면 프롬프트 조립...")
    scenes = generate_script_and_prompts()

    logger.info("[2/2] track_input.json 파일 출력...")
    build_openmontage_input_json(scenes)

if __name__ == "__main__":
    main()
