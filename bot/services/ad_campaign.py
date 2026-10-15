"""First-party app promotion and sponsored campaigns for ALMAZ TJ BOT.

The bot cannot place a persistent Telegram Ads banner above the chat list.
This service sends a clearly-labelled in-chat promo at most once per user per
24 hours. When no paid campaign is configured, it promotes the official Raonson
Android app. Delivery counts are not verified views; click tracking records
redirect requests, not installs.
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from bot.config import config
from bot.db.models import AdsGramEvent
from bot.db.repo import get_setting
from bot.db.session import get_session

log = logging.getLogger(__name__)
COOLDOWN = timedelta(hours=24)
APP_URL = "https://play.google.com/store/apps/details?id=com.raonson.app"
APP_TEXT = (
    "📱 <b>Raonson — барномаи иҷтимоии мо</b>\n"
    "Бо дӯстон муошират кун, наворҳо ва постҳоро бин ва бо ҷомеа пайваст бош!\n\n"
    "⬇️ Барномаро аз Google Play насб кун."
)
FAR_FUTURE = "2099-12-31T23:59:59+00:00"


def _valid_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _tracked_url(user_id: int) -> str:
    base = config.public_url.rstrip("/")
    if not base:
        return APP_URL
    return f"{base}/promo/click?uid={user_id}"


async def maybe_send_campaign_ad(bot: Bot, user_id: int) -> bool:
    """Send the active campaign or default Raonson app promo once per 24h."""
    try:
        async with get_session() as db:
            disabled = await get_setting(db, "campaign_disabled", "0")
            custom_text = await get_setting(db, "campaign_text", "")
            custom_url = await get_setting(db, "campaign_url", "")
            ends_raw = await get_setting(db, "campaign_ends_at", "")
            last = await db.scalar(
                select(AdsGramEvent.created_at)
                .where(
                    AdsGramEvent.user_id == user_id,
                    AdsGramEvent.event_type == "campaign_delivered",
                )
                .order_by(AdsGramEvent.created_at.desc()).limit(1)
            )
        if disabled == "1":
            return False

        now = datetime.now(timezone.utc)
        active_custom = False
        if custom_text and ends_raw:
            try:
                ends = datetime.fromisoformat(ends_raw)
                if ends.tzinfo is None:
                    ends = ends.replace(tzinfo=timezone.utc)
                active_custom = ends > now
            except ValueError:
                log.warning("Campaign end timestamp is invalid; using Raonson app promo")

        if active_custom:
            body_text = custom_text.strip()
            target_url = custom_url if _valid_url(custom_url) else APP_URL
        else:
            body_text = APP_TEXT
            target_url = APP_URL

        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < COOLDOWN:
                return False

        # Custom ad copy is HTML-escaped when saved by /setad; the built-in
        # app copy intentionally contains safe, authored HTML formatting.
        body = "📢 <b>РЕКЛАМА</b>\n\n" + body_text
        if len(body) > 4000:
            body = body[:3990] + "…"
        button_url = _tracked_url(user_id) if target_url == APP_URL else target_url
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="📲 Зеркашии Raonson" if target_url == APP_URL else "🔗 Кушодан",
                url=button_url,
            )
        ]])
        sent = await bot.send_message(
            chat_id=user_id, text=body, reply_markup=keyboard,
            disable_web_page_preview=True, protect_content=True,
        )
        async with get_session() as db:
            db.add(AdsGramEvent(
                user_id=user_id, event_type="campaign_delivered",
                message_id=sent.message_id, created_at=now,
            ))
            await db.commit()
        return True
    except Exception:
        log.exception("App promo/campaign delivery failed for user %s", user_id)
        return False


async def record_promo_click(user_id: int) -> None:
    """Record an app-promo redirect click. It does not verify Play Store install."""
    async with get_session() as db:
        db.add(AdsGramEvent(
            user_id=user_id, event_type="campaign_clicked", created_at=datetime.now(timezone.utc)
        ))
        await db.commit()


async def campaign_stats() -> tuple[int, int, int, int, str, str]:
    async with get_session() as db:
        delivered = int(await db.scalar(
            select(func.count(AdsGramEvent.id)).where(AdsGramEvent.event_type == "campaign_delivered")
        ) or 0)
        unique = int(await db.scalar(
            select(func.count(func.distinct(AdsGramEvent.user_id))).where(
                AdsGramEvent.event_type == "campaign_delivered"
            )
        ) or 0)
        clicks = int(await db.scalar(
            select(func.count(AdsGramEvent.id)).where(AdsGramEvent.event_type == "campaign_clicked")
        ) or 0)
        unique_clicks = int(await db.scalar(
            select(func.count(func.distinct(AdsGramEvent.user_id))).where(
                AdsGramEvent.event_type == "campaign_clicked"
            )
        ) or 0)
        ends = await get_setting(db, "campaign_ends_at", "")
        text = await get_setting(db, "campaign_text", "")
        disabled = await get_setting(db, "campaign_disabled", "0")
    active = disabled != "1"
    if text and ends:
        try:
            end_dt = datetime.fromisoformat(ends)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            # Expired custom campaigns fall back to the default app promo.
            active = active and end_dt > datetime.now(timezone.utc)
        except ValueError:
            pass
    return delivered, unique, clicks, unique_clicks, ("фаъол" if active else "ғайрифаъол"), ends
