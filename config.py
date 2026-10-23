import os

class Config:
    FPS = 30
    OUTPUT_VIDEO_PATH = "output_final.mp4"
    TRACK_INPUT_PATH = "track_input.json"
    YOUTUBE_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")

config = Config()
