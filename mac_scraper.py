"""
Mac Mini scraper — runs on your residential IP, pushes results to VPS.
No web server, no database. Just scrape + POST.

Uses curl-cffi with a warm-up page visit to establish session cookies
before hitting the Tesla inventory API.

Run manually:   python3 mac_scraper.py
Runs via cron:  see com.eshanb.tesla-scraper.plist
"""
import json
import logging
import os
import random
import sys
import time
from urllib.parse import quote

from curl_cffi.requests import Session

# ── Config (reads from env or defaults) ──────────────────────
VPS_INGEST_URL = os.getenv("VPS_INGEST_URL", "http://db.bhide.au:8080/tesla/api/ingest")
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


def scrape_all() -> dict[tuple, list]:
    """
    Opens one persistent session, warms it up with a real page visit,
    then fetches all model/condition combos. Returns {(model, condition): [vehicles]}.
    """
    results = {}

    with Session(impersonate="chrome110") as session:

        # Warm up: visit the inventory page as a real browser would.
        # This sets cookies and passes bot checks before we hit the API.
        warmup_url = f"https://www.tesla.com/en_AU/inventory/new/my?arrangeby=plh&zip={ZIP_CODE}&range=0"
        logger.info(f"Warming up session via {warmup_url}")
        warmup = session.get(
            warmup_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-AU,en;q=0.9",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
            },
            timeout=30,
        )
        logger.info(f"Warmup response: HTTP {warmup.status_code} "
                    f"(cookies: {len(session.cookies)})")

        if warmup.status_code not in (200, 304):
            raise Exception(f"Warmup failed with HTTP {warmup.status_code}")

        # Small pause like a real user would have
        time.sleep(random.uniform(1.5, 3.0))

        # Now hit the API with the established session
        for model in MODELS:
            for condition in CONDITIONS:
                try:
                    query_str = quote(json.dumps(build_query(model, condition), separators=(",", ":")))
                    url = f"{TESLA_API_URL}?query={query_str}"

                    resp = session.get(
                        url,
                        headers={
                            "Accept": "application/json",
                            "Accept-Language": "en-AU,en;q=0.9",
                            "Referer": f"https://www.tesla.com/en_AU/inventory/{condition}/{model}",
                            "Sec-Fetch-Dest": "empty",
                            "Sec-Fetch-Mode": "cors",
                            "Sec-Fetch-Site": "same-origin",
                        },
                        timeout=30,
                    )

                    if resp.status_code != 200:
                        raise Exception(f"HTTP {resp.status_code}")

                    content_type = resp.headers.get("content-type", "")
                    if "json" not in content_type:
                        preview = resp.text[:120].replace("\n", " ")
                        raise Exception(f"Got HTML instead of JSON: {preview}")

                    data = resp.json()
                    vehicles = data.get("results", [])
                    total = data.get("total_matches_found", 0)
                    logger.info(f"[{model}/{condition}] {len(vehicles)} results (total: {total})")
                    results[(model, condition)] = vehicles

                except Exception as e:
                    logger.error(f"[{model}/{condition}] Fetch error: {e}")
                    results[(model, condition)] = None

                time.sleep(random.uniform(1.5, 3.5))

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
        VPS_INGEST_URL,
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
    try:
        all_results = scrape_all()
    except Exception as e:
        logger.error(f"Scrape session failed: {e}")
        sys.exit(1)

    errors = 0
    for (model, condition), vehicles in all_results.items():
        if vehicles is None:
            errors += 1
            continue
        try:
            push_to_vps(model, condition, vehicles)
        except Exception as e:
            logger.error(f"[{model}/{condition}] VPS push failed: {e}")
            errors += 1

    if errors == len(MODELS) * len(CONDITIONS):
        sys.exit(1)


if __name__ == "__main__":
    main()
