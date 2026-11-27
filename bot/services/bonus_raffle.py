import logging
import random

from sqlalchemy import func, select

from bot.config import config
from bot.db.models import BonusRaffleDraw, BonusRaffleEntry, Order, User
from bot.db.session import get_session

log = logging.getLogger(__name__)
RAFFLE_SIZE = 25
PRIZE_NAME = "Ваучери Lite (90 алмаз)"


async def register_delivered_orders(bot, orders: list[Order]) -> None:
    """Register each delivered order once and draw a winner per 25 entries."""
    new_draws: list[dict] = []
    async with get_session() as session:
        for order in orders:
            exists = await session.scalar(
                select(BonusRaffleEntry.id).where(BonusRaffleEntry.order_id == order.id)
            )
            if exists is None:
                session.add(BonusRaffleEntry(order_id=order.id, user_id=order.user_id))
        await session.flush()

        while True:
            pending_entries = list((await session.scalars(
                select(BonusRaffleEntry)
                .where(BonusRaffleEntry.round_number.is_(None))
                .order_by(BonusRaffleEntry.id)
            )).all())
            # One entry per distinct customer in each draw, even if that
            # customer placed several orders during the same round.
            unique_entries = {}
            for entry in pending_entries:
                unique_entries.setdefault(entry.user_id, entry)
            if len(unique_entries) < RAFFLE_SIZE:
                break
            participants = list(unique_entries.values())[:RAFFLE_SIZE]
            participant_ids = {entry.user_id for entry in participants}

            previous_round = await session.scalar(
                select(BonusRaffleDraw.round_number)
                .order_by(BonusRaffleDraw.round_number.desc())
                .limit(1)
            )
            round_number = (previous_round or 0) + 1
            winner = random.SystemRandom().choice(participants)
            for entry in pending_entries:
                if entry.user_id in participant_ids:
                    entry.round_number = round_number
                    entry.is_winner = entry.id == winner.id
            session.add(BonusRaffleDraw(
                round_number=round_number,
                winner_user_id=winner.user_id,
                winner_order_id=winner.order_id,
                prize_name=PRIZE_NAME,
            ))
            new_draws.append({
                "round": round_number,
                "user_id": winner.user_id,
                "order_id": winner.order_id,
            })
        await session.commit()

    for draw in new_draws:
        try:
            await bot.send_message(
                draw["user_id"],
                "🎉 <b>ШУМО ҒОЛИБИ ҚУРЪАКАШӢ ШУДЕД!</b>\n\n"
                f"🏆 Даври №{draw['round']}\n"
                f"🎁 Мукофот: <b>{PRIZE_NAME}</b>\n"
                f"🧾 Фармоиши иштирок: #{draw['order_id']}\n\n"
                "Маъмури FF MARGEDAR барои супоридани туҳфа бо шумо тамос мегирад. 💚",
            )
        except Exception:
            log.exception("Could not notify raffle winner user_id=%s", draw["user_id"])

        if config.admin_chat_id:
            try:
                await bot.send_message(
                    config.admin_chat_id,
                    "🎁 <b>Ғолиби нави қуръакашӣ</b>\n"
                    f"Давр: #{draw['round']}\n"
                    f"Ғолиб ID: <code>{draw['user_id']}</code>\n"
                    f"Фармоиш: #{draw['order_id']}\n"
                    f"Мукофот: {PRIZE_NAME}\n"
                    "⚠️ Ваучерро дастӣ супоред.",
                )
            except Exception:
                log.exception("Could not notify admin about raffle draw #%s", draw["round"])


async def get_raffle_status(session) -> tuple[int, dict | None]:
    pending_count = await session.scalar(
        select(func.count(func.distinct(BonusRaffleEntry.user_id)))
        .where(BonusRaffleEntry.round_number.is_(None))
    )
    latest = await session.scalar(
        select(BonusRaffleDraw)
        .order_by(BonusRaffleDraw.round_number.desc())
        .limit(1)
    )
    if latest is None:
        return int(pending_count or 0), None
    user = await session.get(User, latest.winner_user_id)
    winner = (
        f"@{user.username}" if user and user.username
        else (user.full_name if user else f"ID {latest.winner_user_id}")
    )
    return int(pending_count or 0), {"winner": winner, "prize": latest.prize_name}
