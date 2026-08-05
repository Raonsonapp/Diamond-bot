"""Mandatory "sponsor channel" subscription gate — customers must join
config.review_channel_id (the shop's own review/announcement channel)
before they can use the bot. Reuses that channel rather than a separate
setting since the admin only asked for one channel to double as both.
"""

from __future__ import annotations

from aiogram import Bot

from bot.config import config

_NOT_MEMBER_STATUSES = {"left", "kicked"}


async def is_subscribed_to_sponsor_channel(bot: Bot, user_id: int) -> bool:
    """Fails open (treats the user as subscribed) on any Telegram API
    error — e.g. the bot hasn't been made an admin of the channel yet, or
    a transient network error — since a sponsor-channel misconfiguration
    should never be able to lock every customer out of the whole shop."""
    try:
        member = await bot.get_chat_member(config.review_channel_id, user_id)
    except Exception:
        return True
    return member.status not in _NOT_MEMBER_STATUSES
