"""
Tesla Inventory scraper using Playwright.
Uses a real headless browser to bypass Akamai Bot Manager,
then intercepts the inventory API responses.
"""
import asyncio
import json
import random
import logging
from urllib.parse import quote

from playwright.async_api import async_playwright, Browser, BrowserContext

import config
import database

logger = logging.getLogger("tesla-tracker.scraper")

# Persistent browser instance (reused across scrape cycles)
_browser: Browser | None = None
_context: BrowserContext | None = None


def _build_query(model: str, condition: str) -> dict:
    """Build the JSON query object matching Tesla's actual v4 API format."""
    return {
        "query": {
            "model": model,
            "condition": condition,
            "options": {},
            "arrangeby": "Price",
            "order": "asc",
            "market": config.MARKET,
            "language": config.LANGUAGE,
            "super_region": config.SUPER_REGION,
        },
        "offset": 0,
        "count": config.RESULTS_COUNT,
        "outsideOffset": 0,
        "outsideSearch": False,
        "isFalconDeliverySelectionEnabled": True,
        "version": "v2",
    }


async def _get_browser() -> BrowserContext:
    """Get or create a persistent browser context."""
    global _browser, _context

    if _browser and _browser.is_connected():
        return _context

    logger.info("Launching headless browser...")
    pw = await async_playwright().start()
    _browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    _context = await _browser.new_context(
        viewport={"width": 1920, "height": 1080},
        locale="en-AU",
        timezone_id="Australia/Sydney",
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    )
    logger.info("Browser launched successfully")
    return _context


async def fetch_inventory(model: str, condition: str) -> list[dict]:
    """
    Fetch inventory by navigating to the Tesla inventory page
    and intercepting the API response.
    """
    context = await _get_browser()
    page = await context.new_page()

    api_data = None
    api_error = None

    async def handle_response(response):
        nonlocal api_data, api_error
        if "/inventory/api/" in response.url and "inventory-results" in response.url:
            try:
                if response.status == 200:
                    api_data = await response.json()
                else:
                    api_error = f"HTTP {response.status}"
            except Exception as e:
                api_error = str(e)

    page.on("response", handle_response)

    try:
        url = f"https://www.tesla.com/en_AU/inventory/{condition}/{model}?arrangeby=plh&zip={config.ZIP_CODE}&range=0"
        logger.info(f"[{model}/{condition}] Navigating to {url}")

        await page.goto(url, wait_until="networkidle", timeout=45000)

        # Give a moment for any late API responses
        await asyncio.sleep(2)

        if api_error:
            raise Exception(f"API returned error: {api_error}")

        if api_data is None:
            # Fallback: try hitting the API directly using page cookies/session
            logger.info(f"[{model}/{condition}] No intercepted response, trying direct API call via page")
            query = _build_query(model, condition)
            query_str = quote(json.dumps(query))
            api_url = f"{config.TESLA_API_URL}?query={query_str}"

            resp = await page.evaluate(f"""
                async () => {{
                    const resp = await fetch("{api_url}");
                    return await resp.json();
                }}
            """)
            api_data = resp

        results = api_data.get("results", [])
        total = api_data.get("total_matches_found", 0)
        logger.info(f"[{model}/{condition}] Got {len(results)} results (total_matches: {total})")
        return results

    finally:
        await page.close()


async def scrape_once() -> list[dict]:
    """
    Run one full scrape cycle across all configured models and conditions.
    Returns list of new listings (for notification purposes).
    """
    all_new = []

    for model in config.MODELS:
        for condition in config.CONDITIONS:
            try:
                vehicles = await fetch_inventory(model, condition)
                new_count = 0
                active_vins = set()

                for v in vehicles:
                    vin = v.get("VIN", "")
                    if vin:
                        active_vins.add(vin)
                    is_new = database.upsert_listing(v, model, condition)
                    if is_new:
                        new_count += 1
                        v["_model_code"] = model
                        v["_condition"] = condition
                        v["_listing_url"] = f"https://www.tesla.com/en_AU/{condition}/{model}/order/{vin}"
                        all_new.append(v)

                database.mark_gone(active_vins, model, condition)
                database.log_scrape(model, condition, "success", len(vehicles), new_count)
                logger.info(f"[{model}/{condition}] {new_count} new listings found")

            except Exception as e:
                logger.error(f"[{model}/{condition}] Error: {e}")
                database.log_scrape(model, condition, "error", error=str(e))

            # Random delay between page loads to look human
            await asyncio.sleep(random.uniform(3.0, 7.0))

    return all_new


async def shutdown_browser():
    """Clean up browser on app shutdown."""
    global _browser, _context
    if _browser:
        await _browser.close()
        _browser = None
        _context = None
        logger.info("Browser closed")


def get_randomized_interval() -> int:
    """Return the scrape interval with ±30% jitter."""
    base = config.SCRAPE_INTERVAL_SECONDS
    jitter = base * 0.3
    return int(random.uniform(base - jitter, base + jitter))
