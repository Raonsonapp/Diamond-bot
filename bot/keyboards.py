from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db.models import Order, Product


def products_keyboard(products: list[Product]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{p.diamonds} 💎 — {p.price_somoni:.0f} сомонӣ",
                callback_data=f"product:{p.id}",
            )
        ]
        for p in products
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_order_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Тасдиқ", callback_data="order:confirm"),
                InlineKeyboardButton(text="❌ Бекор", callback_data="order:cancel"),
            ]
        ]
    )


def admin_order_keyboard(order: Order) -> InlineKeyboardMarkup:
    rows = []
    if order.status.value == "awaiting_payment":
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Пардохт тасдиқ шуд", callback_data=f"admin:paid:{order.id}"
                ),
                InlineKeyboardButton(
                    text="❌ Рад", callback_data=f"admin:reject:{order.id}"
                ),
            ]
        )
    elif order.status.value in ("paid", "delivering"):
        rows.append(
            [
                InlineKeyboardButton(
                    text="📦 Дода шуд (Delivered)", callback_data=f"admin:delivered:{order.id}"
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
