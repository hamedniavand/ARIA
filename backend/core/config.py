from pathlib import Path
from dotenv import load_dotenv
import os

# Load .env from repo root (two levels up from this file)
_env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_PROXY_URL: str = os.getenv("GEMINI_PROXY_URL", "https://generativelanguage.googleapis.com")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
SECRET_KEY: str = os.getenv("SECRET_KEY", "changeme")
CAPTCHA_API_KEY: str = os.getenv("CAPTCHA_API_KEY", "")
BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
SCREENSHOTS_DIR: str = os.getenv("SCREENSHOTS_DIR", "/root/ARIA/screenshots")
DB_PATH: str = os.getenv("DB_PATH", "/root/ARIA/aria.db")
UPLOADS_DIR: str = os.getenv("UPLOADS_DIR", "/root/ARIA/uploads")
DASHBOARD_USER: str = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS: str = os.getenv("DASHBOARD_PASS", "changeme")
SERPER_API_KEY: str = os.getenv("SERPER_API_KEY", "")
SERPER_LIMIT: int = 2500

os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
