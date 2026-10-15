"""AdsGram ad delivery for ALMAZ TJ Telegram bot.

The bot sends a clearly labelled ad message at most once per user per 24 hours.
A stored event means Telegram accepted the message; it is not proof of a human view.
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import aiohttp
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from bot.config import config
from bot.db.models import AdsGramEvent
from bot.db.session import get_session

log = logging.getLogger(__name__)
ADS_ENDPOINT = "https://api.adsgram.ai/advbot"
AD_COOLDOWN = timedelta(hours=24)


def _valid_http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


async def maybe_send_ad(bot: Bot, user_id: int, language: str = "ru") -> bool:
    """Fetch and send an ad if this user has not received one in 24 hours.

    Exceptions are caught so an advertising API outage never blocks shopping.
    """
    if not config.adsgram_token or not config.adsgram_block_id:
        return False

    now = datetime.now(timezone.utc)
    try:
        async with get_session() as db:
            last = await db.scalar(
                select(AdsGramEvent.created_at)
                .where(AdsGramEvent.user_id == user_id, AdsGramEvent.event_type == "delivered")
                .order_by(AdsGramEvent.created_at.desc())
                .limit(1)
            )
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < AD_COOLDOWN:
                return False

        params = {
            "tgid": str(user_id),
            "blockid": config.adsgram_block_id,
            "language": (language or "ru")[:12],
            "token": config.adsgram_token,
        }
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            async with http.get(ADS_ENDPOINT, params=params) as response:
                if response.status != 200:
                    log.warning("AdsGram API returned HTTP %s", response.status)
                    return False
                payload = await response.json(content_type=None)

        if not isinstance(payload, dict):
            return False
        ad_text = payload.get("text_html")
        if not isinstance(ad_text, str) or not ad_text.strip():
            return False

        click_url = _valid_http_url(payload.get("click_url"))
        image_url = _valid_http_url(payload.get("image_url"))
        button_name = str(payload.get("button_name") or "Кушодан")[:64]
        keyboard = (
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button_name, url=click_url)]])
            if click_url else None
        )
        caption = "📢 <b>РЕКЛАМА</b>\n\n" + ad_text[:850]

        if image_url:
            sent = await bot.send_photo(
                chat_id=user_id, photo=image_url, caption=caption,
                reply_markup=keyboard, protect_content=True,
            )
        else:
            sent = await bot.send_message(
                chat_id=user_id, text=caption, reply_markup=keyboard,
                protect_content=True, disable_web_page_preview=True,
            )

        async with get_session() as db:
            db.add(AdsGramEvent(
                user_id=user_id, event_type="delivered", message_id=sent.message_id,
                created_at=now,
            ))
            await db.commit()
        return True
    except Exception:
        log.exception("AdsGram delivery failed for Telegram user %s", user_id)
        return False


async def get_ad_stats() -> tuple[int, int, int]:
    """Return delivered-message count, unique recipients, and configuration status."""
    async with get_session() as db:
        delivered = int(await db.scalar(
            select(func.count(AdsGramEvent.id)).where(AdsGramEvent.event_type == "delivered")
        ) or 0)
        unique_users = int(await db.scalar(
            select(func.count(func.distinct(AdsGramEvent.user_id))).where(AdsGramEvent.event_type == "delivered")
        ) or 0)
    enabled = int(bool(config.adsgram_token and config.adsgram_block_id))
    return delivered, unique_users, enabled
