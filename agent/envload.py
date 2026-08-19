"""Tiny .env.local reader. Reuses the SP Payment project's .env.local so the Sheets
service account + Metabase key are configured in ONE place only."""
import os

DEFAULT_ENV = os.path.join(
    r"C:\Users\user\OneDrive\Documents\Claude\Projects\SP Payment weekly report",
    ".env.local",
)


KEYS = ("METABASE_URL", "METABASE_API_KEY", "SHEETS_CLIENT_EMAIL",
        "SHEETS_PRIVATE_KEY", "TELEGRAM_BOT_TOKEN")


def load_env(path=None):
    """Read KEY=VALUE lines from .env.local into a dict. Strips surrounding quotes.
    In the cloud (GitHub Actions) there is no .env.local — fall back to real environment
    variables (GitHub Secrets). Never prints values."""
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local")
    path = path or (local if os.path.exists(local) else DEFAULT_ENV)
    env = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and ((v[0] == '"' and v[-1] == '"') or (v[0] == "'" and v[-1] == "'")):
                    v = v[1:-1]
                env[k.strip()] = v
    for k in KEYS:                       # env vars fill anything the file didn't provide
        if not env.get(k) and os.environ.get(k):
            env[k] = os.environ[k]
    return env
