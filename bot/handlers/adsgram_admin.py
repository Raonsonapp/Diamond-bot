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
                    # Never print the request URL: it contains the private token.
                    log.warning("AdsGram diagnostic returned HTTP %s", status)
                    await message.answer(
                        f"⚠️ AdsGram API ҷавоби HTTP {status} дод. "
                        "Токен, рақами блок ва ҳолати тасдиқи платформаро санҷед."
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
