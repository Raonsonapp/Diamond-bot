"""Admin-only diagnostics for the AdsGram integration."""
import logging

import aiohttp
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import config

router = Router(name="adsgram_admin")
log = logging.getLogger(__name__)


@router.message(Command("adcheck"))
async def adsgram_check(message: Message) -> None:
    """Check AdsGram configuration/API without sending an ad to the admin."""
    if message.from_user.id not in config.admin_user_ids:
        await message.answer("⛔ Ин фармон танҳо барои админ аст.")
        return

    if not config.adsgram_token or not config.adsgram_block_id:
        await message.answer(
            "⚠️ AdsGram танзим нашудааст. Дар Render тағйирёбандаҳои "
            "ADSGRAM_TOKEN ва ADSGRAM_BLOCK_ID-ро санҷед."
        )
        return

    params = {
        "tgid": str(message.from_user.id),
        "blockid": str(config.adsgram_block_id).removeprefix("bot-"),
        "language": getattr(message.from_user, "language_code", None) or "ru",
        "token": config.adsgram_token,
    }
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://api.adsgram.ai/advbot", params=params) as response:
                status = response.status
                if status != 200:
                    # Read the provider's error message, but never log the request URL:
                    # aiohttp's request URL includes the private token.
                    try:
                        body = (await response.text())[:700]
                    except Exception:
                        body = "(ҷавоби матнӣ хонда нашуд)"
                    log.warning("AdsGram diagnostic HTTP %s; response body: %s", status, body)
                    safe_body = body.replace(config.adsgram_token, "[HIDDEN TOKEN]") if config.adsgram_token else body
                    await message.answer(
                        f"⚠️ AdsGram API: HTTP {status}.\n"
                        f"Ҷавоби сервер: <code>{safe_body.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}</code>\n\n"
                        "Токенро махфӣ нигоҳ доред; агар дар ҷавоб сабаб равшан набошад, матнро ба ман фиристед."
                    )
                    return
                payload = await response.json(content_type=None)

        if not isinstance(payload, dict):
            await message.answer("⚠️ AdsGram ҷавоби ғайричашмдошт баргардонд.")
        elif isinstance(payload.get("text_html"), str) and payload["text_html"].strip():
            await message.answer(
                "✅ Пайвастшавӣ ба API-и AdsGram кор мекунад ва матни реклама гирифтем.\n"
                "Ин фармон реклама намефиристад. Барои нишон додани реклама "
                "блок бояд дар AdsGram тасдиқ ва фаъол бошад."
            )
        else:
            await message.answer(
                "ℹ️ API дастрас аст, аммо ҳоло реклама барнагардонд. "
                "Ин метавонад аз набудани реклама ё ҳолати блок вобаста бошад."
            )
    except (aiohttp.ClientError, TimeoutError, ValueError):
        log.exception("AdsGram API diagnostic failed")
        await message.answer(
            "❌ Ба API-и AdsGram пайваст шуда натавонистем. "
            "Пайвасти интернети сервер ва логҳои Render-ро санҷед."
        )



@router.message(Command("setad"))
async def set_campaign_ad(message: Message) -> None:
    """Start a first-party sponsored campaign for 1-168 hours.

    Format: /setad <hours> <https-url> | <plain-text ad copy>
    """
    import html
    import uuid
    from datetime import datetime, timedelta, timezone
    from urllib.parse import urlparse

    from bot.db.repo import set_setting
    from bot.db.session import get_session

    if message.from_user.id not in config.admin_user_ids:
        await message.answer("⛔ Ин фармон танҳо барои админ аст.")
        return

    raw = message.text or ""
    left, sep, body = raw.partition("|")
    parts = left.strip().split(maxsplit=2)
    if not sep or len(parts) != 3 or not parts[1].isdigit():
        await message.answer(
            "Истифода:\n<code>/setad 24 https://example.com | Матни реклама</code>\n"
            "Соат: аз 1 то 168. Матнро оддӣ нависед, HTML лозим нест."
        )
        return
    hours = int(parts[1])
    url = parts[2].strip()
    body = body.strip()
    parsed = urlparse(url)
    if not 1 <= hours <= 168:
        await message.answer("⚠️ Давомнокӣ бояд аз 1 то 168 соат бошад.")
        return
    if parsed.scheme != "https" or not parsed.netloc:
        await message.answer("⚠️ Линк бояд HTTPS бошад ва дуруст навишта шавад.")
        return
    if not body or len(body) > 3000:
        await message.answer("⚠️ Матни реклама бояд аз 1 то 3000 аломат бошад.")
        return

    ends = datetime.now(timezone.utc) + timedelta(hours=hours)
    campaign_id = uuid.uuid4().hex
    safe_body = html.escape(body)
    async with get_session() as db:
        await set_setting(db, "campaign_text", safe_body)
        await set_setting(db, "campaign_url", url)
        await set_setting(db, "campaign_ends_at", ends.isoformat())
        await set_setting(db, "campaign_id", campaign_id)
        await set_setting(db, "campaign_disabled", "0")

    await message.answer(
        "✅ <b>Реклама фаъол шуд!</b>\n"
        f"⏱ Давомнокӣ: {hours} соат\n"
        f"🕒 Анҷом: {ends.strftime('%d.%m.%Y %H:%M UTC')}\n"
        "📍 Дар оғози меню нишон дода мешавад.\n"
        "👤 Барои ҳар корбар на бештар аз як маротиба дар 24 соат.\n"
        "📊 Барои ҳисоб: /campaignstats"
    )


@router.message(Command("stopad"))
async def stop_campaign_ad(message: Message) -> None:
    from datetime import datetime, timezone
    from bot.db.repo import set_setting
    from bot.db.session import get_session

    if message.from_user.id not in config.admin_user_ids:
        await message.answer("⛔ Ин фармон танҳо барои админ аст.")
        return
    async with get_session() as db:
        await set_setting(db, "campaign_ends_at", datetime.now(timezone.utc).isoformat())
        await set_setting(db, "campaign_disabled", "1")
    await message.answer("🛑 Рекламаи дохилӣ хомӯш карда шуд.")


@router.message(Command("campaignstats"))
async def campaign_ad_stats(message: Message) -> None:
    from bot.services.ad_campaign import campaign_stats

    if message.from_user.id not in config.admin_user_ids:
        await message.answer("⛔ Ин фармон танҳо барои админ аст.")
        return
    delivered, unique, clicks, unique_clicks, status, ends = await campaign_stats()
    await message.answer(
        "📊 <b>Ҳисоботи рекламаи дохилӣ</b>\n"
        f"Статус: {status}\n"
        f"Паёмҳои қабулкардаи Telegram: {delivered}\n"
        f"Корбарони нодир: {unique}\n"
        f"Кликҳои пайванд: {clicks}\n"
        f"Корбарони нодири кликкунанда: {unique_clicks}\n"
        f"Анҷоми рекламаи фармоишӣ: {ends or '—'}\n\n"
        "Агар рекламаи фармоишӣ фаъол набошад, бот барномаи Raonson-ро нишон медиҳад. "
        "Паёми фиристодашуда дидани воқеиро тасдиқ намекунад; клик ҳам насбро тасдиқ намекунад."
    )
