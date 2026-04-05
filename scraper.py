"""
Tesla Inventory scraper using curl-cffi.
Impersonates Chrome's TLS fingerprint for direct API calls — no browser needed.
Works on residential IPs (Mac, home server) natively.
On VPS/datacenter IPs, set PROXY_URL in .env to route through a residential proxy.
"""
import asyncio
import json
import random
import logging
from urllib.parse import quote

from curl_cffi.requests import AsyncSession

import config
import database

logger = logging.getLogger("tesla-tracker.scraper")


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


async def fetch_inventory(model: str, condition: str) -> list[dict]:
    """
    Fetch inventory via direct API call, impersonating Chrome's TLS fingerprint.
    """
    query = _build_query(model, condition)
    query_str = quote(json.dumps(query, separators=(",", ":")))
    url = f"{config.TESLA_API_URL}?query={query_str}"

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

    proxy = config.PROXY_URL or None

    async with AsyncSession(impersonate="chrome110") as session:
        response = await session.get(
            url,
            headers=headers,
            proxy=proxy,
            timeout=30,
        )

    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}")

    # Check we got JSON not a challenge page
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type:
        preview = response.text[:120].replace("\n", " ")
        raise Exception(f"Got non-JSON response ({content_type}): {preview}")

    data = response.json()
    results = data.get("results", [])
    total = data.get("total_matches_found", 0)
    logger.info(f"[{model}/{condition}] {len(results)} results (total: {total})")
    return results


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

            await asyncio.sleep(random.uniform(2.0, 5.0))

    return all_new


def get_randomized_interval() -> int:
    """Return the scrape interval with ±30% jitter."""
    base = config.SCRAPE_INTERVAL_SECONDS
    jitter = base * 0.3
    return int(random.uniform(base - jitter, base + jitter))


async def shutdown_browser():
    """No-op — kept for compatibility with main.py."""
    pass
