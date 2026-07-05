"""Application configuration loaded from environment / .env file."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # YouTube
    yt_channel_id: str
    yt_api_key: str

    # Public URL / WebSub
    public_base_url: str
    websub_secret: str
    renew_interval_seconds: int = 345_600  # ~4 days

    # Facebook
    fb_page_id: str
    fb_page_token: str
    fb_api_version: str = "v25.0"

    # Post content
    post_template: str = "🔴 We're live now — join us: {url}"

    # Runtime
    db_path: str = "data/posted.sqlite3"

    @property
    def callback_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/youtube/webhook"

    @property
    def topic_url(self) -> str:
        return (
            "https://www.youtube.com/xml/feeds/videos.xml"
            f"?channel_id={self.yt_channel_id}"
        )

    def video_url(self, video_id: str) -> str:
        return f"https://www.youtube.com/watch?v={video_id}"


settings = Settings()  # type: ignore[call-arg]
