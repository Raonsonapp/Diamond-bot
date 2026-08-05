"""Flat milestone bonus: for every REFERRAL_MILESTONE_INTERVAL people a
user refers (just for signing up — no purchase required), credit
REFERRAL_MILESTONE_BONUS somoni to their balance. Separate from the
existing 5%-of-purchase-amount referral commission in
bot/services/fulfillment.py, and separate from the referral-race contest
in bot/services/contest.py — all three can fire independently.
"""

from __future__ import annotations

from aiogram import Bot

from bot.db.repo import count_referrals, credit_referral_balance
from bot.db.session import get_session

REFERRAL_MILESTONE_INTERVAL = 7
REFERRAL_MILESTONE_BONUS = 1.0


async def maybe_credit_referral_milestone(bot: Bot, referrer_id: int) -> None:
    async with get_session() as session:
        count = await count_referrals(session, referrer_id)
        if count == 0 or count % REFERRAL_MILESTONE_INTERVAL != 0:
            return
        await credit_referral_balance(session, referrer_id, REFERRAL_MILESTONE_BONUS)

    await bot.send_message(
        referrer_id,
        f"🎉 Шумо {count} нафарро даъват кардед! "
        f"{REFERRAL_MILESTONE_BONUS:.2f} сомонӣ ба балансатон гузаронда шуд. Ташаккур!",
    )
