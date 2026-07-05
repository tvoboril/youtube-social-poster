"""Set required env vars before app modules import their Settings singleton."""
import os

os.environ.setdefault("YT_CHANNEL_ID", "UCtestchannel0000000000")
os.environ.setdefault("YT_API_KEY", "test-key")
os.environ.setdefault("PUBLIC_BASE_URL", "https://example.test")
os.environ.setdefault("WEBSUB_SECRET", "test-secret")
os.environ.setdefault("FB_PAGE_ID", "1234567890")
os.environ.setdefault("FB_PAGE_TOKEN", "test-token")
os.environ.setdefault("POST_TEMPLATE", "Live: {url}")
