"""
Mac Mini scraper using Playwright.
Navigates to Tesla's inventory page (passing Cloudflare JS challenges),
intercepts the API response, then pushes results to the VPS.

Run manually:   python3 mac_scraper.py
Scheduled via:  com.eshanb.tesla-scraper.plist (every 5 min)
"""
import asyncio
import json
import logging
import os
import random
import sys
import urllib.request
from urllib.parse import quote

from playwright.async_api import async_playwright

# ── Config ────────────────────────────────────────────────────
VPS_INGEST_URL = os.getenv("VPS_INGEST_URL", "http://db.bhide.au:8080/tesla/api/ingest")
INGEST_TOKEN   = os.getenv("INGEST_TOKEN", "")
MARKET         = os.getenv("TESLA_MARKET", "AU")
LANGUAGE       = os.getenv("TESLA_LANGUAGE", "en")
SUPER_REGION   = os.getenv("TESLA_SUPER_REGION", "north america")
ZIP_CODE       = os.getenv("TESLA_ZIP", "2759")
RESULTS_COUNT  = int(os.getenv("RESULTS_COUNT", "50"))
MODELS         = ["my", "m3"]
CONDITIONS     = ["new", "used"]
TESLA_API_URL  = "https://www.tesla.com/inventory/api/v4/inventory-results"

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


async def fetch_one(context, model: str, condition: str) -> list | None:
    """
    Opens one page, navigates to the Tesla inventory page,
    intercepts the inventory API response. Returns list of vehicle dicts or None.
    """
    page = await context.new_page()
    captured = {}   # use dict so the closure can mutate it

    async def handle_response(response):
        if "/inventory/api/" in response.url and "inventory-results" in response.url:
            try:
                if response.status == 200:
                    data = await response.json()
                    captured["data"] = data
                    results = data.get("results", [])
                    logger.info(
                        f"[{model}/{condition}] Intercepted: {len(results)} results "
                        f"(total: {data.get('total_matches_found', 0)})"
                    )
            except Exception as e:
                logger.error(f"[{model}/{condition}] Response parse error: {e}")

    page.on("response", handle_response)

    url = (f"https://www.tesla.com/en_AU/inventory/{condition}/{model}"
           f"?arrangeby=plh&zip={ZIP_CODE}&range=0")
    try:
        logger.info(f"[{model}/{condition}] Loading {url}")
        await page.goto(url, wait_until="networkidle", timeout=45000)
        await asyncio.sleep(2)

        if "data" not in captured:
            # Fallback: fetch from within the page's browser context
            logger.info(f"[{model}/{condition}] No intercept — trying in-page fetch")
            query_str = quote(json.dumps(build_query(model, condition), separators=(",", ":")))
            api_url = f"{TESLA_API_URL}?query={query_str}"
            raw = await page.evaluate(f"""
                async () => {{
                    const r = await fetch("{api_url}");
                    return await r.text();
                }}
            """)
            try:
                captured["data"] = json.loads(raw)
                results = captured["data"].get("results", []) if isinstance(captured["data"], dict) else []
                logger.info(f"[{model}/{condition}] In-page fetch: {len(results)} results")
            except Exception:
                logger.error(f"[{model}/{condition}] Non-JSON response: {raw[:150]}")
                return None

        data = captured.get("data")
        if not isinstance(data, dict):
            logger.error(f"[{model}/{condition}] Unexpected data type: {type(data)}")
            return None

        vehicles = data.get("results", [])
        # Guard: ensure results is actually a list of dicts
        if not isinstance(vehicles, list):
            logger.error(f"[{model}/{condition}] results field is not a list: {type(vehicles)}")
            return None

        vehicles = [v for v in vehicles if isinstance(v, dict)]
        logger.info(f"[{model}/{condition}] {len(vehicles)} valid vehicle dicts")
        return vehicles

    except Exception as e:
        logger.error(f"[{model}/{condition}] Page error: {e}")
        return None
    finally:
        await page.close()


async def scrape_all() -> dict:
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_state.json")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-AU",
            timezone_id="Australia/Sydney",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            storage_state=state_file if os.path.exists(state_file) else None,
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        results = {}
        for model in MODELS:
            for condition in CONDITIONS:
                vehicles = await fetch_one(context, model, condition)
                results[(model, condition)] = vehicles
                # Human-like delay between page loads — reduces bot detection risk
                delay = random.uniform(4.0, 8.0)
                logger.info(f"Waiting {delay:.1f}s before next page...")
                await asyncio.sleep(delay)

        try:
            await context.storage_state(path=state_file)
            logger.info(f"Session saved → {state_file}")
        except Exception as e:
            logger.warning(f"Could not save session: {e}")

        await browser.close()

    return results


def push_to_vps(model: str, condition: str, vehicles: list):
    active_vins = [v["VIN"] for v in vehicles if v.get("VIN")]
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
        logger.info(f"[{model}/{condition}] VPS ack: {body}")


async def main():
    try:
        all_results = await scrape_all()
    except Exception as e:
        logger.error(f"Scrape session failed: {e}")
        sys.exit(1)

    errors = 0
    for (model, condition), vehicles in all_results.items():
        if vehicles is None:
            logger.warning(f"[{model}/{condition}] Skipping — no data")
            errors += 1
            continue
        if len(vehicles) == 0:
            logger.info(f"[{model}/{condition}] No inventory found, pushing empty list to mark all gone")
        try:
            push_to_vps(model, condition, vehicles)
        except Exception as e:
            logger.error(f"[{model}/{condition}] VPS push failed: {e}")
            errors += 1

    if errors == len(MODELS) * len(CONDITIONS):
        logger.error("All scrapes failed")
        sys.exit(1)
    else:
        logger.info(f"Done. {len(MODELS) * len(CONDITIONS) - errors}/{len(MODELS) * len(CONDITIONS)} succeeded.")


if __name__ == "__main__":
    asyncio.run(main())
