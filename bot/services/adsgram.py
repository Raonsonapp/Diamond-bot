"""AdsGram ad delivery for ALMAZ TJ Telegram bot.

Fetches an ad from AdsGram and sends it as a clearly labelled, non-forwardable
Telegram message. Per-user delivery is rate-limited to one ad per 24 hours.
A stored event means Telegram accepted the message; it is not proof of a view.
"""
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import aiohttp
from aiogram import Bot
from aiogram.enums import ParseMode
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


def _ad_keyboard(payload: dict) -> InlineKeyboardMarkup | None:
    """Build buttons from AdsGram's documented response fields."""
    rows = []
    click_url = _valid_http_url(payload.get("click_url"))
    if click_url:
        rows.append([
            InlineKeyboardButton(
                text=str(payload.get("button_name") or "Кушодан")[:64],
                url=click_url,
            )
        ])

    reward_url = _valid_http_url(payload.get("reward_url"))
    if reward_url:
        rows.append([
            InlineKeyboardButton(
                text=str(payload.get("button_reward_name") or "Гирифтани мукофот")[:64],
                url=reward_url,
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def maybe_send_ad(bot: Bot, user_id: int, language: str = "ru") -> bool:
    """Fetch and send an ad if this user has not received one in 24 hours.

    Fail open: AdsGram/database/Telegram errors must not block shopping.
    """
    if not config.adsgram_token or not config.adsgram_block_id:
        return False

    now = datetime.now(timezone.utc)
    try:
        async with get_session() as db:
            last = await db.scalar(
                select(AdsGramEvent.created_at)
                .where(
                    AdsGramEvent.user_id == user_id,
                    AdsGramEvent.event_type == "delivered",
                )
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
            # AdsGram requires the numeric ID only, without its "bot-" prefix.
            "blockid": str(config.adsgram_block_id).removeprefix("bot-"),
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
            log.warning("AdsGram returned an unexpected response type")
            return False

        ad_text = payload.get("text_html")
        if not isinstance(ad_text, str) or not ad_text.strip():
            log.info("AdsGram returned no ad for Telegram user %s", user_id)
            return False

        # Telegram's message text limit is 4096 characters. Do not truncate
        # HTML mid-tag, which can make Telegram reject the entire ad.
        caption = "📢 <b>РЕКЛАМА</b>\n\n" + ad_text.strip()
        if len(caption) > 4096:
            log.warning("AdsGram ad text is too long to send safely (%s chars)", len(caption))
            return False

        image_url = _valid_http_url(payload.get("image_url"))
        keyboard = _ad_keyboard(payload)

        if image_url and len(caption) <= 1024:
            sent = await bot.send_photo(
                chat_id=user_id,
                photo=image_url,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                protect_content=True,
            )
        else:
            if image_url:
                # Photo captions have a smaller Telegram limit than messages.
                await bot.send_photo(
                    chat_id=user_id,
                    photo=image_url,
                    protect_content=True,
                )
            sent = await bot.send_message(
                chat_id=user_id,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                protect_content=True,
                disable_web_page_preview=True,
            )

        async with get_session() as db:
            db.add(
                AdsGramEvent(
                    user_id=user_id,
                    event_type="delivered",
                    message_id=sent.message_id,
                    created_at=now,
                )
            )
            await db.commit()
        return True
    except Exception:
        log.exception("AdsGram delivery failed for Telegram user %s", user_id)
        return False


async def get_ad_stats() -> tuple[int, int, int]:
    """Return delivered-message count, unique recipients, and configuration status."""
    async with get_session() as db:
        delivered = int(
            await db.scalar(
                select(func.count(AdsGramEvent.id)).where(
                    AdsGramEvent.event_type == "delivered"
                )
            )
            or 0
        )
        unique_users = int(
            await db.scalar(
                select(func.count(func.distinct(AdsGramEvent.user_id))).where(
                    AdsGramEvent.event_type == "delivered"
                )
            )
            or 0
        )
    enabled = int(bool(config.adsgram_token and config.adsgram_block_id))
    return delivered, unique_users, enabled
