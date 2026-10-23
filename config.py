import os

class Config:
    FPS = 30
    OPENING_PATH = "season_intro_16_9.mp4"
    CLOSING_PATH = "season_outro_16_9.mp4"
    OUTPUT_VIDEO_PATH = "output_final.mp4"
    METADATA_PATH = "metadata.json"
    YOUTUBE_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")

config = Config()
