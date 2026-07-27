"""Referral-race contest logic ("first to invite N friends who also buy
something wins a prize") — shared between the moment a new referral signs
up (bot/handlers/customer.py) and the moment one of them actually pays for
something (bot/services/fulfillment.py), since winning requires both:
N new referrals AND at least one of them completing a purchase, and either
event can be the one that finally satisfies both conditions.
"""

from __future__ import annotations

from aiogram import Bot

from bot.config import config
from bot.db.repo import (
    count_new_referrals,
    get_active_contest,
    has_referral_with_purchase,
    set_contest_winner,
)
from bot.db.session import get_session


async def maybe_declare_contest_winner(bot: Bot, referrer_id: int) -> None:
    async with get_session() as session:
        contest = await get_active_contest(session)
        if contest is None or contest.winner_user_id is not None:
            return
        new_count = await count_new_referrals(session, referrer_id, contest.started_at)
        if new_count < contest.target_referrals:
            return
        if not await has_referral_with_purchase(session, referrer_id, contest.started_at):
            return
        contest = await set_contest_winner(session, contest, referrer_id)

    await bot.send_message(
        referrer_id,
        f"🏆 Табрик! Шумо ғолиби мусобиқа шудед — {contest.target_referrals} дӯст даъват кардед ва "
        f"ҳадди ақал яке аз онҳо харид кард!\n"
        f"🎁 Ҷоизаи шумо: {contest.prize_name} — ба зудӣ аз ҷониби админ фиристода мешавад.",
    )
    if config.admin_chat_id:
        await bot.send_message(
            config.admin_chat_id,
            f"🏆 Мусобиқа анҷом ёфт! Ғолиб: id={referrer_id} — {contest.target_referrals} реферал "
            f"(ҳадди ақал 1-тоаш харид кард).\nЛутфан ҷоизаро ({contest.prize_name}) ба ӯ дастӣ фиристед.",
        )
