"""Post a rich "proof of purchase" / review message to the public shop
channel after a delivery, matching the format competing shops use:
sequential review number, customer name, their comment (if they left
one), product, order id, and their running purchase count.

Requires the bot to be a channel admin with post rights (REVIEW_CHANNEL_ID
in config). Never lets a channel/permission problem break the actual
order flow — failures here are swallowed.
"""

from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import func, select

from bot.config import config
from bot.db.models import Order, OrderStatus, User


async def post_review_announcement(
    bot: Bot, session, order: Order, product, review_text: str | None
) -> None:
    if not config.review_channel_id:
        return

    user = await session.get(User, order.user_id)
    display_name = user.full_name if user and user.full_name else "Мизоҷ"

    purchase_count_result = await session.execute(
        select(func.count()).select_from(Order).where(
            Order.user_id == order.user_id, Order.status == OrderStatus.DELIVERED
        )
    )
    purchase_count = purchase_count_result.scalar_one()

    review_number_result = await session.execute(
        select(func.count()).select_from(Order).where(Order.status == OrderStatus.DELIVERED)
    )
    review_number = review_number_result.scalar_one()

    lines = [f"🏅 ОТЗИВИ МУШТАРӢ #{review_number}", "", f"👤 Муштарӣ: {display_name}"]

    if review_text:
        lines += ["", "💬 Назари муштарӣ:", f"«{review_text}»"]

    lines += [
        "",
        f"🎁 Маҳсулот: 💎 {product.diamonds}",
        f"🆔 ID фармоиш: #{order.id}",
        "",
        f"🔥 Ин муштарӣ аллакай {purchase_count}-умин хариди худро анҷом дод. "
        f"Ташаккур барои эътимод ва ҳамкории доимӣ! ❤️",
    ]

    try:
        await bot.send_message(config.review_channel_id, "\n".join(lines))
    except TelegramAPIError:
        pass
