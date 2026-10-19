"""Referral milestone prizes, unlocked only after referral purchases are delivered."""
from __future__ import annotations
import logging
from aiogram import Bot
from sqlalchemy import func, select
from bot.config import config
from bot.db.models import Order, OrderStatus, ReferralMilestoneAward, User
from bot.db.session import get_session

# Values based on the current FF MARGEDAR retail price list.
MILESTONES = (
    (5, 1, "🎟 Ваучери Лайт ×1", 5.50),
    (10, 2, "🎟 Ваучери Лайт ×2", 11.00),
    (15, 3, "💎 110 алмос", 9.00),
    (20, 5, "🎁 Ваучери ҳафтаина ×1", 17.00),
)

async def maybe_credit_referral_milestone(bot: Bot, referrer_id: int) -> None:
    """Unlock each milestone once. Purchases count only after delivery and are
    orders placed by users directly invited by this referrer.
    """
    unlocked: list[tuple[int, str, float]] = []
    async with get_session() as session:
        referral_count = await session.scalar(
            select(func.count(User.id)).where(User.referred_by == referrer_id)
        ) or 0
        purchase_count = await session.scalar(
            select(func.count(Order.id)).join(User, User.id == Order.user_id).where(
                User.referred_by == referrer_id, Order.status == OrderStatus.DELIVERED
            )
        ) or 0
        for milestone, required_purchases, reward_name, value in MILESTONES:
            if referral_count < milestone or purchase_count < required_purchases:
                continue
            existing = await session.scalar(select(ReferralMilestoneAward.id).where(
                ReferralMilestoneAward.user_id == referrer_id,
                ReferralMilestoneAward.milestone == milestone,
            ))
            if existing:
                continue
            session.add(ReferralMilestoneAward(
                user_id=referrer_id, milestone=milestone, reward_name=reward_name,
                reward_value_somoni=value, status="pending",
            ))
            unlocked.append((milestone, reward_name, value))
        if unlocked:
            await session.commit()

    for milestone, reward_name, value in unlocked:
        try:
            await bot.send_message(
                referrer_id,
                f"🎉 <b>Ҷоизаи рефералии шумо кушода шуд!</b>\n\n"
                f"👥 Даъватшудагон: {milestone} нафар\n🎁 Ҷоиза: <b>{reward_name}</b>\n"
                f"💵 Арзиши тахминӣ: <b>{value:.2f} сомонӣ</b>\n\n"
                "✅ Шартҳо иҷро шуданд. Админ ҷоизаро дастӣ мефиристад.",
                parse_mode="HTML",
            )
        except Exception:
            logging.exception("Could not notify referrer %s for milestone %s", referrer_id, milestone)
        admin_targets = set(config.admin_user_ids)
        if config.admin_chat_id:
            admin_targets.add(config.admin_chat_id)
        for admin_id in admin_targets:
            try:
                await bot.send_message(
                    admin_id,
                    f"🎁 Ҷоизаи рефералӣ барои фиристодан: корбар <code>{referrer_id}</code>\n"
                    f"👥 Марҳила: {milestone} нафар\n🏆 {reward_name}\n"
                    f"💵 Арзиш: {value:.2f} сомонӣ\n🧾 Вазъ: интизори фиристодани ҷоиза",
                    parse_mode="HTML",
                )
            except Exception:
                logging.exception("Could not notify admin %s about referral award", admin_id)
