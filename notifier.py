"""
Notification system: Email (Gmail SMTP) and push notifications (ntfy.sh).
"""
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

import httpx

import config
import database

logger = logging.getLogger("tesla-tracker.notifier")


def _format_price(price) -> str:
    if price is None:
        return "N/A"
    return f"${price:,.0f}"


def _format_listing_text(v: dict) -> str:
    """Format a single vehicle listing for plain text."""
    model = v.get("Model", v.get("_model_code", "?"))
    trim = v.get("TrimName", "")
    year = v.get("Year", "")
    price = _format_price(v.get("Price"))
    color = v.get("PAINT", [""])[0] if isinstance(v.get("PAINT"), list) else v.get("PAINT", "")
    odo = v.get("Odometer", 0)
    condition = v.get("_condition", "new")
    url = v.get("_listing_url", "")

    lines = [
        f"  {year} {model} {trim}".strip(),
        f"  Price: {price} AUD | Condition: {condition.title()}",
        f"  Color: {color} | Odometer: {odo:,} km",
        f"  VIN: {v.get('VIN', 'N/A')}",
        f"  Link: {url}",
    ]
    return "\n".join(lines)


def _format_listing_html(v: dict) -> str:
    """Format a single vehicle listing for HTML email."""
    model = v.get("Model", v.get("_model_code", "?"))
    trim = v.get("TrimName", "")
    year = v.get("Year", "")
    price = _format_price(v.get("Price"))
    color = v.get("PAINT", [""])[0] if isinstance(v.get("PAINT"), list) else v.get("PAINT", "")
    odo = v.get("Odometer", 0)
    condition = v.get("_condition", "new")
    url = v.get("_listing_url", "")

    return f"""
    <div style="border:1px solid #ddd; border-radius:8px; padding:16px; margin:12px 0; background:#fafafa;">
        <h3 style="margin:0 0 8px 0; color:#171a20;">{year} {model} {trim}</h3>
        <table style="border-collapse:collapse; font-size:14px;">
            <tr><td style="padding:2px 12px 2px 0; color:#666;">Price</td><td><strong>{price} AUD</strong></td></tr>
            <tr><td style="padding:2px 12px 2px 0; color:#666;">Condition</td><td>{condition.title()}</td></tr>
            <tr><td style="padding:2px 12px 2px 0; color:#666;">Color</td><td>{color}</td></tr>
            <tr><td style="padding:2px 12px 2px 0; color:#666;">Odometer</td><td>{odo:,} km</td></tr>
            <tr><td style="padding:2px 12px 2px 0; color:#666;">VIN</td><td style="font-family:monospace;">{v.get('VIN', 'N/A')}</td></tr>
        </table>
        <a href="{url}" style="display:inline-block; margin-top:10px; padding:8px 16px; background:#3e6ae1; color:white; text-decoration:none; border-radius:4px;">View on Tesla.com</a>
    </div>
    """


async def notify_new_listings(listings: list[dict]):
    """Send notifications for new listings via all enabled channels."""
    if not listings:
        return

    vins = [v.get("VIN") for v in listings if v.get("VIN")]

    if config.EMAIL_ENABLED:
        try:
            _send_email(listings)
            logger.info(f"Email sent for {len(listings)} new listing(s)")
        except Exception as e:
            logger.error(f"Email failed: {e}")

    if config.NTFY_ENABLED:
        try:
            await _send_ntfy(listings)
            logger.info(f"ntfy push sent for {len(listings)} new listing(s)")
        except Exception as e:
            logger.error(f"ntfy push failed: {e}")

    # Mark as notified in DB
    database.mark_notified(vins)


def _send_email(listings: list[dict]):
    """Send a batched email with all new listings."""
    if not config.SMTP_USER or not config.EMAIL_TO:
        logger.warning("Email not configured (SMTP_USER or EMAIL_TO missing)")
        return

    count = len(listings)
    subject = f"Tesla Alert: {count} new listing{'s' if count != 1 else ''} found"

    # Plain text version
    text_body = f"Found {count} new Tesla listing{'s' if count != 1 else ''}:\n\n"
    for v in listings:
        text_body += _format_listing_text(v) + "\n\n"
    text_body += f"\nTimestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

    # HTML version
    html_listings = "".join(_format_listing_html(v) for v in listings)
    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width:600px; margin:0 auto;">
        <h2 style="color:#171a20;">Tesla Inventory Alert</h2>
        <p>Found <strong>{count}</strong> new listing{'s' if count != 1 else ''}:</p>
        {html_listings}
        <p style="color:#999; font-size:12px; margin-top:24px;">
            Sent by Tesla Inventory Tracker &bull;
            {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
        </p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.SMTP_USER
    msg["To"] = config.EMAIL_TO

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        recipients = [addr.strip() for addr in config.EMAIL_TO.split(",")]
        server.sendmail(config.SMTP_USER, recipients, msg.as_string())


async def _send_ntfy(listings: list[dict]):
    """Send push notification via ntfy.sh for each new listing."""
    if not config.NTFY_TOPIC:
        logger.warning("ntfy not configured (NTFY_TOPIC missing)")
        return

    url = f"{config.NTFY_SERVER}/{config.NTFY_TOPIC}"

    for v in listings:
        model = v.get("Model", v.get("_model_code", "Tesla"))
        trim = v.get("TrimName", "")
        year = v.get("Year", "")
        price = _format_price(v.get("Price"))
        condition = v.get("_condition", "new").title()
        listing_url = v.get("_listing_url", "")

        title = f"New Tesla: {year} {model} {trim}".strip()
        body = f"{price} AUD | {condition}\nVIN: {v.get('VIN', 'N/A')}"

        headers = {
            "Title": title,
            "Priority": "high",
            "Tags": "car,zap",
        }
        if listing_url:
            headers["Click"] = listing_url
            headers["Actions"] = f"view, View on Tesla.com, {listing_url}"

        async with httpx.AsyncClient() as client:
            await client.post(url, content=body, headers=headers)
