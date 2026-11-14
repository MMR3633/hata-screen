import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ALLOWED_IDS = {int(x) for x in os.environ.get("ALLOWED_IDS", "").replace(" ", "").split(",") if x}
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
YT_API_KEY = os.environ.get("YT_API_KEY", "")
PORT = int(os.environ.get("PORT") or os.environ.get("WEB_PORT") or "8080")
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE / "data"))
DB_PATH = DATA_DIR / "hata.db"
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
PROXY = os.environ.get("TG_PROXY", "")
SCREEN_URL = os.environ.get("SCREEN_URL", "https://hata-screen.bothost.tech").rstrip("/")
