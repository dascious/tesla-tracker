"""
Mac Mini scraper — runs on your residential IP, pushes results to VPS.
No web server, no database. Just scrape + POST.

Run manually:   python3 mac_scraper.py
Runs via cron:  see com.eshanb.tesla-scraper.plist
"""
import json
import logging
import os
import random
import sys
from urllib.parse import quote

# ── Minimal config (reads from env or defaults) ──────────────
VPS_URL = os.getenv("VPS_INGEST_URL", "http://db.bhide.au:8080/tesla/api/ingest")
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "")
MARKET = os.getenv("TESLA_MARKET", "AU")
LANGUAGE = os.getenv("TESLA_LANGUAGE", "en")
SUPER_REGION = os.getenv("TESLA_SUPER_REGION", "north america")
ZIP_CODE = os.getenv("TESLA_ZIP", "2759")
RESULTS_COUNT = int(os.getenv("RESULTS_COUNT", "50"))
MODELS = ["my", "m3"]
CONDITIONS = ["new", "used"]
TESLA_API_URL = "https://www.tesla.com/inventory/api/v4/inventory-results"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mac-scraper")


def build_query(model: str, condition: str) -> dict:
    return {
        "query": {
            "model": model,
            "condition": condition,
            "options": {},
            "arrangeby": "Price",
            "order": "asc",
            "market": MARKET,
            "language": LANGUAGE,
            "super_region": SUPER_REGION,
        },
        "offset": 0,
        "count": RESULTS_COUNT,
        "outsideOffset": 0,
        "outsideSearch": False,
        "isFalconDeliverySelectionEnabled": True,
        "version": "v2",
    }


def fetch_inventory(model: str, condition: str) -> list[dict]:
    from curl_cffi.requests import Session

    query_str = quote(json.dumps(build_query(model, condition), separators=(",", ":")))
    url = f"{TESLA_API_URL}?query={query_str}"

    headers = {
        "Accept": "application/json",
        "Accept-Language": "en-AU,en;q=0.9",
        "Referer": f"https://www.tesla.com/en_AU/inventory/{condition}/{model}",
        "sec-ch-ua": '"Chromium";v="125", "Google Chrome";v="125", "Not.A/Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    with Session(impersonate="chrome110") as session:
        response = session.get(url, headers=headers, timeout=30)

    if response.status_code != 200:
        raise Exception(f"Tesla API returned HTTP {response.status_code}")

    content_type = response.headers.get("content-type", "")
    if "json" not in content_type:
        preview = response.text[:120].replace("\n", " ")
        raise Exception(f"Got non-JSON response: {preview}")

    data = response.json()
    results = data.get("results", [])
    logger.info(f"[{model}/{condition}] Tesla API: {len(results)} results "
                f"(total: {data.get('total_matches_found', 0)})")
    return results


def push_to_vps(model: str, condition: str, vehicles: list[dict]):
    import urllib.request

    active_vins = [v.get("VIN", "") for v in vehicles if v.get("VIN")]
    payload = json.dumps({
        "model": model,
        "condition": condition,
        "vehicles": vehicles,
        "active_vins": active_vins,
    }).encode()

    req = urllib.request.Request(
        VPS_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Ingest-Token": INGEST_TOKEN,
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
        logger.info(f"[{model}/{condition}] VPS ingest: {body}")


def main():
    errors = 0
    for model in MODELS:
        for condition in CONDITIONS:
            try:
                vehicles = fetch_inventory(model, condition)
                push_to_vps(model, condition, vehicles)
            except Exception as e:
                logger.error(f"[{model}/{condition}] Error: {e}")
                errors += 1

            # Small delay between requests
            import time
            time.sleep(random.uniform(2.0, 4.0))

    if errors == len(MODELS) * len(CONDITIONS):
        # Everything failed
        sys.exit(1)


if __name__ == "__main__":
    main()
