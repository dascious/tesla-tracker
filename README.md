# Tesla Inventory Tracker (Australia)

Monitors Tesla's inventory API for new Model Y and Model 3 listings (new + used) in Australia. Sends email and/or push notifications when new cars appear, and provides a web dashboard showing current and historical inventory.

## Quick Start

```bash
cd tesla-tracker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your settings (see below)

python main.py
# Dashboard: http://localhost:8000
```

## Notification Setup

### Email (Gmail)
1. Go to https://myaccount.google.com/apppasswords
2. You may need to enable 2FA first
3. Generate an App Password for "Mail"
4. In `.env`, set:
   - `EMAIL_ENABLED=true`
   - `SMTP_USER=your.email@gmail.com`
   - `SMTP_PASSWORD=xxxx xxxx xxxx xxxx` (the 16-char app password)
   - `EMAIL_TO=your.email@gmail.com`

### Push Notifications (ntfy.sh) — recommended for speed
1. Install the **ntfy** app on your phone ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iOS](https://apps.apple.com/app/ntfy/id1625396347))
2. Pick a unique topic name (treat it like a password, e.g. `tesla-eshan-a8f3k`)
3. Subscribe to that topic in the app
4. In `.env`, set:
   - `NTFY_ENABLED=true`
   - `NTFY_TOPIC=tesla-eshan-a8f3k`

Push notifications arrive in ~1 second. Much faster than email for snagging a car before it sells.

## VPS Deployment

### Option A: systemd service
```bash
sudo cp tesla-tracker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tesla-tracker
```

### Option B: Docker
```bash
docker build -t tesla-tracker .
docker run -d --env-file .env -p 8000:8000 -v ./data:/app/data tesla-tracker
```

## How It Works

- Hits Tesla's undocumented inventory API (`/inventory/api/v1/inventory-results`) directly — no browser rendering needed
- Scrapes every ~5 minutes with randomized intervals (±30%) to avoid detection
- Exponential backoff on errors (429 rate limits, server errors)
- Tracks each VIN in SQLite: when it first appeared, when it disappeared, price changes
- Dashboard auto-refreshes every 60 seconds, with a manual "Scrape Now" button

## Files

```
main.py          — FastAPI app + background scraper scheduler
scraper.py       — Tesla API client with anti-detection measures
notifier.py      — Email (SMTP) + push notification (ntfy.sh) sender
database.py      — SQLite operations for listing tracking
config.py        — Configuration from .env
templates/       — Jinja2 HTML templates
static/          — CSS
```
