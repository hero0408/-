import os
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def upload_private_video(video_file_path: str):
    client_id = os.environ.get("YOUTUBE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        print("YouTube API 인증 정보가 부족하여 업로드를 건너뜁니다.")
        return

    # 저장된 Refresh Token으로 Google API 인증 객체 생성
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret
    )

    youtube = build("youtube", "v3", credentials=creds)

    # 에피소드 제목 로드
    title = "The Clockwork Repository Episode"
    if os.path.exists("metadata.json"):
        with open("metadata.json", "r", encoding="utf-8") as f:
            meta = json.load(f)
            title = meta.get("title_ko", title)

    body = {
        "snippet": {
            "title": f"[3D AI 애니메이션] {title}",
            "description": "OpenMontage 및 AI 자동화 파이프라인으로 생성된 에피소드입니다.",
            "tags": ["3D애니메이션", "AI영상", "철학", "ClockworkRepository"],
            "categoryId": "1"  # Film & Animation 카테고리
        },
        "status": {
            "privacyStatus": "private",  # 🔒 핵심: 비공개 상태 업로드
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_file_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    
    response = request.execute()
    print(f"✅ YouTube 비공개 업로드 성공! Video ID: {response.get('id')}")
