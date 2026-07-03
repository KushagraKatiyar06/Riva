# Environment variables and shared constants. All API keys, directory paths,
# and rate limit settings are loaded here and imported by the rest of the backend.

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY")
CF_ACCOUNT_ID    = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CF_API_TOKEN     = os.getenv("CLOUDFLARE_API_TOKEN")
CF_INDEX_NAME    = "riva-intel"
RIVA_WORKER_URL  = os.getenv("RIVA_WORKER_URL", "").rstrip("/")
CF_EMBED_MODEL = "@cf/baai/bge-base-en-v1.5"
CF_HEADERS = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json",
}

EXTRACTS_DIR = Path(__file__).parent.parent / "testing" / "extracts"
REPORTS_DIR = Path(__file__).parent.parent / "testing" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

DAILY_RUN_LIMIT = 2          # per IP per UTC day
CLEAR_PASSWORD  = os.getenv("CLEAR_PASSWORD", "riva-admin")
