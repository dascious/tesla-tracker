"""
Configuration for Tesla Inventory Tracker.
Copy .env.example to .env and fill in your values.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Tesla API ────────────────────────────────────────────────
TESLA_API_URL = "https://www.tesla.com/inventory/api/v4/inventory-results"

# Models to track: "my" = Model Y, "m3" = Model 3
MODELS = ["my", "m3"]

# Conditions to track
CONDITIONS = ["new", "used"]

# Location (user's Australian postcode from the original URL)
ZIP_CODE = os.getenv("TESLA_ZIP", "2759")
MARKET = os.getenv("TESLA_MARKET", "AU")
LANGUAGE = os.getenv("TESLA_LANGUAGE", "en")
SUPER_REGION = os.getenv("TESLA_SUPER_REGION", "north america")
# Tesla uses "north america" as super_region even for AU in some endpoints;
# the market=AU param is what actually filters to Australia.

# Coordinates for the postcode (St Marys / Penrith area)
LATITUDE = float(os.getenv("TESLA_LAT", "-33.7462"))
LONGITUDE = float(os.getenv("TESLA_LNG", "150.7936"))

# How many results per query
RESULTS_COUNT = 50

# ── Scraper Timing ───────────────────────────────────────────
# Base interval in seconds (randomized ±30% to avoid detection)
SCRAPE_INTERVAL_SECONDS = int(os.getenv("SCRAPE_INTERVAL", "300"))  # 5 min default

# Exponential backoff on errors (max wait in seconds)
MAX_BACKOFF_SECONDS = 1800  # 30 min

# ── Email Notifications (Gmail SMTP) ────────────────────────
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")       # your Gmail address
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # Gmail App Password
EMAIL_TO = os.getenv("EMAIL_TO", "")           # recipient(s), comma-separated

# ── Push Notifications (ntfy.sh) ────────────────────────────
NTFY_ENABLED = os.getenv("NTFY_ENABLED", "false").lower() == "true"
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "")  # e.g. "tesla-tracker-eshan-xyz123"
NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")

# ── Web Dashboard ────────────────────────────────────────────
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
BASE_PATH = os.getenv("BASE_PATH", "/tesla")  # subpath for reverse proxy

# ── Deployment mode ──────────────────────────────────────────
# Set SCRAPER_ENABLED=false on VPS when Mac Mini is doing the scraping.
# Mac Mini should set SCRAPER_ENABLED=true (or omit, default is true).
SCRAPER_ENABLED = os.getenv("SCRAPER_ENABLED", "true").lower() == "true"

# Secret token for the /api/ingest endpoint.
# Must match on both VPS and Mac Mini. Generate with: openssl rand -hex 32
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")

# ── Proxy (needed for VPS/datacenter IPs) ───────────────────
# Tesla blocks datacenter IPs. Route through a residential proxy.
# Format: http://user:pass@host:port  or  socks5://user:pass@host:port
# Leave blank if running on a residential IP (home, Mac Mini, etc.)
PROXY_URL = os.getenv("PROXY_URL", "")

# ── Database ─────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "tesla_inventory.db")
