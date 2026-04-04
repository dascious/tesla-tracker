"""
SQLite database layer for tracking Tesla inventory listings.
"""
import sqlite3
import json
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional

import config


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS listings (
                vin TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                trim_name TEXT,
                condition TEXT NOT NULL,
                price REAL,
                currency TEXT DEFAULT 'AUD',
                odometer INTEGER DEFAULT 0,
                exterior_color TEXT,
                interior_color TEXT,
                autopilot TEXT,
                wheels TEXT,
                city TEXT,
                state TEXT,
                year INTEGER,
                listing_url TEXT,
                raw_json TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                gone_at TEXT,
                notified INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS scrape_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT,
                condition TEXT,
                status TEXT NOT NULL,
                total_found INTEGER DEFAULT 0,
                new_listings INTEGER DEFAULT 0,
                error_message TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_listings_model ON listings(model);
            CREATE INDEX IF NOT EXISTS idx_listings_condition ON listings(condition);
            CREATE INDEX IF NOT EXISTS idx_listings_first_seen ON listings(first_seen);
            CREATE INDEX IF NOT EXISTS idx_listings_gone_at ON listings(gone_at);
        """)


def upsert_listing(vehicle: dict, model_code: str, condition: str) -> bool:
    """
    Insert or update a listing. Returns True if the VIN is brand new (not seen before).
    """
    now = datetime.now(timezone.utc).isoformat()
    vin = vehicle.get("VIN", "")
    if not vin:
        return False

    # Build the listing URL
    listing_url = f"https://www.tesla.com/en_AU/{condition}/{model_code}/order/{vin}"

    with db() as conn:
        existing = conn.execute("SELECT vin, gone_at FROM listings WHERE vin = ?", (vin,)).fetchone()

        if existing:
            # Already seen — update last_seen and clear gone_at if it was marked gone
            conn.execute("""
                UPDATE listings
                SET last_seen = ?, gone_at = NULL, price = ?, raw_json = ?
                WHERE vin = ?
            """, (now, vehicle.get("Price"), json.dumps(vehicle), vin))

            # If it was gone and came back, treat it as a re-appearance (notify again)
            if existing["gone_at"] is not None:
                conn.execute("UPDATE listings SET notified = 0 WHERE vin = ?", (vin,))
                return True
            return False
        else:
            # Brand new listing
            model_name = _resolve_model_name(model_code, vehicle)
            conn.execute("""
                INSERT INTO listings
                (vin, model, trim_name, condition, price, odometer,
                 exterior_color, interior_color, autopilot, wheels,
                 city, state, year, listing_url, raw_json,
                 first_seen, last_seen, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """, (
                vin,
                model_name,
                vehicle.get("TrimName", ""),
                condition,
                vehicle.get("Price"),
                vehicle.get("Odometer", 0),
                vehicle.get("PAINT", [None])[0] if isinstance(vehicle.get("PAINT"), list) else vehicle.get("PAINT", ""),
                vehicle.get("INTERIOR", [None])[0] if isinstance(vehicle.get("INTERIOR"), list) else vehicle.get("INTERIOR", ""),
                vehicle.get("AUTOPILOT", [None])[0] if isinstance(vehicle.get("AUTOPILOT"), list) else vehicle.get("AUTOPILOT", ""),
                vehicle.get("WHEELS", [None])[0] if isinstance(vehicle.get("WHEELS"), list) else vehicle.get("WHEELS", ""),
                vehicle.get("City", ""),
                vehicle.get("StateProvince", ""),
                vehicle.get("Year"),
                listing_url,
                json.dumps(vehicle),
                now,
                now,
            ))
            return True


def _resolve_model_name(model_code: str, vehicle: dict) -> str:
    names = {"my": "Model Y", "m3": "Model 3", "ms": "Model S", "mx": "Model X"}
    return vehicle.get("Model", names.get(model_code, model_code))


def mark_gone(active_vins: set[str], model_code: str, condition: str):
    """Mark listings as gone if they're no longer in the API results."""
    now = datetime.now(timezone.utc).isoformat()
    model_name_candidates = {
        "my": ("Model Y", "my"),
        "m3": ("Model 3", "m3"),
    }
    names = model_name_candidates.get(model_code, (model_code,))

    with db() as conn:
        placeholders = ",".join("?" for _ in names)
        rows = conn.execute(f"""
            SELECT vin FROM listings
            WHERE model IN ({placeholders})
              AND condition = ?
              AND gone_at IS NULL
        """, (*names, condition)).fetchall()

        for row in rows:
            if row["vin"] not in active_vins:
                conn.execute("UPDATE listings SET gone_at = ? WHERE vin = ?", (now, row["vin"]))


def mark_notified(vins: list[str]):
    with db() as conn:
        for vin in vins:
            conn.execute("UPDATE listings SET notified = 1 WHERE vin = ?", (vin,))


def log_scrape(model: str, condition: str, status: str, total: int = 0, new: int = 0, error: str = ""):
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute("""
            INSERT INTO scrape_log (timestamp, model, condition, status, total_found, new_listings, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (now, model, condition, status, total, new, error))


def get_active_listings(model: Optional[str] = None, condition: Optional[str] = None) -> list[dict]:
    """Get all currently active listings (not gone)."""
    with db() as conn:
        query = "SELECT * FROM listings WHERE gone_at IS NULL"
        params = []
        if model:
            query += " AND model = ?"
            params.append(model)
        if condition:
            query += " AND condition = ?"
            params.append(condition)
        query += " ORDER BY first_seen DESC"
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def get_recent_listings(hours: int = 24) -> list[dict]:
    """Get listings first seen within the last N hours."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM listings WHERE first_seen >= ? ORDER BY first_seen DESC", (cutoff,)
        ).fetchall()]


def get_gone_listings(limit: int = 50) -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM listings WHERE gone_at IS NOT NULL ORDER BY gone_at DESC LIMIT ?", (limit,)
        ).fetchall()]


def get_all_listings(limit: int = 200) -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM listings ORDER BY first_seen DESC LIMIT ?", (limit,)
        ).fetchall()]


def get_scrape_log(limit: int = 50) -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM scrape_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()]


def get_stats() -> dict:
    with db() as conn:
        active = conn.execute("SELECT COUNT(*) as c FROM listings WHERE gone_at IS NULL").fetchone()["c"]
        total = conn.execute("SELECT COUNT(*) as c FROM listings").fetchone()["c"]
        gone = conn.execute("SELECT COUNT(*) as c FROM listings WHERE gone_at IS NOT NULL").fetchone()["c"]
        last_scrape = conn.execute("SELECT * FROM scrape_log ORDER BY timestamp DESC LIMIT 1").fetchone()
        return {
            "active": active,
            "total_seen": total,
            "gone": gone,
            "last_scrape": dict(last_scrape) if last_scrape else None,
        }
