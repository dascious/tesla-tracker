"""
Mac Mini scraper using Playwright.
Navigates to Tesla's inventory page (passing Cloudflare JS challenges),
intercepts the API response, then pushes results to the VPS.

Runs headlessly — no visible window.

Run manually:   python3 mac_scraper.py
Scheduled via:  com.eshanb.tesla-scraper.plist (every 5 min)
"""
import asyncio
import json
import logging
import os
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


async def scrape_all() -> dict:
    """
    Launches a headless browser, navigates to each inventory page,
    intercepts the API JSON response. Returns {(model, condition): [vehicles]}.
    """
    results = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-AU",
            timezone_id="Australia/Sydney",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )

        for model in MODELS:
            for condition in CONDITIONS:
                page = await context.new_page()
                api_data = None

                async def handle_response(response):
                    nonlocal api_data
                    if "/inventory/api/" in response.url and "inventory-results" in response.url:
                        try:
                            if response.status == 200:
                                api_data = await response.json()
                                logger.info(
                                    f"[{model}/{condition}] Intercepted API: "
                                    f"{len(api_data.get('results', []))} results "
                                    f"(total: {api_data.get('total_matches_found', 0)})"
                                )
                        except Exception as e:
                            logger.error(f"[{model}/{condition}] JSON parse error: {e}")

                page.on("response", handle_response)

                url = (f"https://www.tesla.com/en_AU/inventory/{condition}/{model}"
                       f"?arrangeby=plh&zip={ZIP_CODE}&range=0")
                try:
                    logger.info(f"[{model}/{condition}] Loading {url}")
                    await page.goto(url, wait_until="networkidle", timeout=45000)
                    await asyncio.sleep(2)

                    if api_data is None:
                        # Fallback: fetch from within page context (has cookies + session)
                        logger.info(f"[{model}/{condition}] No intercept, trying in-page fetch")
                        query_str = quote(json.dumps(build_query(model, condition), separators=(",", ":")))
                        api_url = f"{TESLA_API_URL}?query={query_str}"
                        raw = await page.evaluate(f"""
                            async () => {{
                                const r = await fetch("{api_url}");
                                const t = await r.text();
                                return t;
                            }}
                        """)
                        try:
                            api_data = json.loads(raw)
                        except Exception:
                            logger.error(f"[{model}/{condition}] Fallback returned non-JSON: {raw[:120]}")

                    if api_data:
                        results[(model, condition)] = api_data.get("results", [])
                    else:
                        results[(model, condition)] = None

                except Exception as e:
                    logger.error(f"[{model}/{condition}] Page error: {e}")
                    results[(model, condition)] = None
                finally:
                    await page.close()

        await browser.close()

    return results


def push_to_vps(model: str, condition: str, vehicles: list):
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
        logger.info(f"[{model}/{condition}] VPS: {body}")


async def main():
    try:
        all_results = await scrape_all()
    except Exception as e:
        logger.error(f"Scrape failed: {e}")
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
    asyncio.run(main())
