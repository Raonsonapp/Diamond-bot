"""Mandatory subscription gate for the FF MARGEDAR reviews channel.

The bot must be an administrator/member of this channel to reliably check
membership. API errors fail closed so users cannot bypass the subscription
gate when Telegram membership checks are unavailable.
"""

from __future__ import annotations

import logging

from aiogram import Bot

from bot.config import config

_NOT_MEMBER_STATUSES = {"left", "kicked"}


async def is_subscribed_to_sponsor_channel(bot: Bot, user_id: int) -> bool:
    """Return True only when Telegram confirms the user is a channel member."""
    try:
        member = await bot.get_chat_member(config.review_channel_id, user_id)
    except Exception:
        logging.exception(
            "Sponsor membership check failed for Telegram user %s in channel %s",
            user_id,
            config.review_channel_id,
        )
        return False
    return member.status not in _NOT_MEMBER_STATUSES
