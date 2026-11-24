"""Referral rewards are currently disabled for all customers."""

from __future__ import annotations

from aiogram import Bot


async def maybe_credit_referral_milestone(bot: Bot, referrer_id: int) -> None:
    """Intentionally do nothing while referral prizes are disabled."""
    return None
