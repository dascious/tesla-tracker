"""
Tesla Inventory Tracker — main entry point.
Runs the FastAPI web server + background scraper scheduler.
"""
import asyncio
import logging
import random
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import config
import database
import scraper
import notifier

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("tesla-tracker")

# ── Background scraper task ──────────────────────────────────
scraper_task = None
consecutive_errors = 0


async def scraper_loop():
    """Background loop that scrapes Tesla inventory on a randomized interval."""
    global consecutive_errors

    logger.info("Scraper loop started")
    # Small initial delay to let the server finish starting
    await asyncio.sleep(2)

    while True:
        try:
            logger.info("Running scrape cycle...")
            new_listings = await scraper.scrape_once()

            if new_listings:
                logger.info(f"Found {len(new_listings)} new listing(s) — sending notifications")
                await notifier.notify_new_listings(new_listings)
            else:
                logger.info("No new listings this cycle")

            consecutive_errors = 0
            interval = scraper.get_randomized_interval()

        except Exception as e:
            consecutive_errors += 1
            # Exponential backoff: base * 2^errors, capped at MAX_BACKOFF
            backoff = min(
                config.SCRAPE_INTERVAL_SECONDS * (2 ** consecutive_errors),
                config.MAX_BACKOFF_SECONDS,
            )
            interval = int(backoff + random.uniform(0, 30))
            logger.error(f"Scrape cycle failed (attempt #{consecutive_errors}): {e}")
            logger.info(f"Backing off for {interval}s")

        logger.info(f"Next scrape in {interval}s ({interval/60:.1f} min)")
        await asyncio.sleep(interval)


# ── FastAPI app ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global scraper_task
    database.init_db()
    logger.info("Database initialized")
    scraper_task = asyncio.create_task(scraper_loop())
    logger.info("Scraper background task created")
    yield
    if scraper_task:
        scraper_task.cancel()
        logger.info("Scraper task cancelled")
    await scraper.shutdown_browser()
    logger.info("Browser shut down")


# Sub-application mounted at /tesla
sub_app = FastAPI(title="Tesla Inventory Tracker")
sub_app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Root app that mounts sub_app at the configured base path
app = FastAPI(lifespan=lifespan)
app.mount(config.BASE_PATH, sub_app)


# ── Web routes ───────────────────────────────────────────────
@sub_app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = database.get_stats()
    active = database.get_active_listings()
    recent = database.get_recent_listings(hours=24)
    gone = database.get_gone_listings(limit=30)
    log = database.get_scrape_log(limit=20)

    return templates.TemplateResponse("index.html", {
        "request": request,
        "stats": stats,
        "active": active,
        "recent": recent,
        "gone": gone,
        "log": log,
        "now": datetime.now(timezone.utc),
        "base_path": config.BASE_PATH,
        "config": {
            "models": config.MODELS,
            "conditions": config.CONDITIONS,
            "zip": config.ZIP_CODE,
            "interval": config.SCRAPE_INTERVAL_SECONDS,
            "email_enabled": config.EMAIL_ENABLED,
            "ntfy_enabled": config.NTFY_ENABLED,
        },
    })


# ── API routes (for AJAX refresh) ───────────────────────────
@sub_app.get("/api/listings")
async def api_listings(
    model: str = Query(None),
    condition: str = Query(None),
    status: str = Query("active"),  # active | gone | all
):
    if status == "active":
        data = database.get_active_listings(model, condition)
    elif status == "gone":
        data = database.get_gone_listings()
    else:
        data = database.get_all_listings()
    return JSONResponse(data)


@sub_app.get("/api/stats")
async def api_stats():
    return JSONResponse(database.get_stats())


@sub_app.get("/api/log")
async def api_log():
    return JSONResponse(database.get_scrape_log())


@sub_app.post("/api/scrape-now")
async def api_scrape_now():
    """Trigger an immediate scrape (manual refresh button)."""
    try:
        new_listings = await scraper.scrape_once()
        if new_listings:
            await notifier.notify_new_listings(new_listings)
        return JSONResponse({
            "status": "ok",
            "new_listings": len(new_listings),
            "message": f"Found {len(new_listings)} new listing(s)",
        })
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ── Entry point ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        reload=False,
        log_level="info",
    )
