"""First-party sponsored campaign delivery for ALMAZ TJ BOT.

Campaigns are configured by an admin, stored in BotSetting (no schema migration),
and shown at most once per user per campaign within a 24-hour cooldown.
A delivery count means Telegram accepted the message; it is not a verified view.
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from bot.db.models import AdsGramEvent
from bot.db.repo import get_setting
from bot.db.session import get_session

log = logging.getLogger(__name__)
CAMPAIGN_KEYS = ("campaign_text", "campaign_url", "campaign_ends_at", "campaign_id")
COOLDOWN = timedelta(hours=24)


def _valid_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def maybe_send_campaign_ad(bot: Bot, user_id: int) -> bool:
    """Send an active admin campaign, once per user every 24h, without blocking shop use."""
    try:
        async with get_session() as db:
            text = await get_setting(db, "campaign_text", "")
            url = await get_setting(db, "campaign_url", "")
            ends_raw = await get_setting(db, "campaign_ends_at", "")
            campaign_id = await get_setting(db, "campaign_id", "")
            last = await db.scalar(
                select(AdsGramEvent.created_at)
                .where(AdsGramEvent.user_id == user_id, AdsGramEvent.event_type == "campaign_delivered")
                .order_by(AdsGramEvent.created_at.desc()).limit(1)
            )
        if not text or not campaign_id or not ends_raw:
            return False
        try:
            ends = datetime.fromisoformat(ends_raw)
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=timezone.utc)
        except ValueError:
            log.warning("Campaign end timestamp is invalid")
            return False
        now = datetime.now(timezone.utc)
        if ends <= now:
            return False
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < COOLDOWN:
                return False

        body = "📢 <b>РЕКЛАМА</b>\n\n" + text.strip()
        if len(body) > 4000:
            body = body[:3990] + "…"
        keyboard = None
        if _valid_url(url):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🔗 Кушодан", url=url)
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
        log.exception("First-party campaign delivery failed for user %s", user_id)
        return False


async def campaign_stats() -> tuple[int, int, str, str]:
    async with get_session() as db:
        delivered = int(await db.scalar(
            select(func.count(AdsGramEvent.id)).where(AdsGramEvent.event_type == "campaign_delivered")
        ) or 0)
        unique = int(await db.scalar(
            select(func.count(func.distinct(AdsGramEvent.user_id))).where(
                AdsGramEvent.event_type == "campaign_delivered"
            )
        ) or 0)
        ends = await get_setting(db, "campaign_ends_at", "")
        text = await get_setting(db, "campaign_text", "")
    active = False
    if ends:
        try:
            end_dt = datetime.fromisoformat(ends)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            active = end_dt > datetime.now(timezone.utc)
        except ValueError:
            pass
    return delivered, unique, ("фаъол" if active and text else "ғайрифаъол"), ends
