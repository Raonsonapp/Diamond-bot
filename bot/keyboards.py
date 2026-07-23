from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import config
from bot.db.models import Order, Product


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Харидани алмаз", callback_data="menu:buy")],
            [InlineKeyboardButton(text="📦 Фармоишҳои ман", callback_data="menu:myorders")],
            [InlineKeyboardButton(text="📢 Канали мо (отзывҳо)", url=config.shop_channel_url)],
            [InlineKeyboardButton(text="📞 Тамос бо мо", callback_data="menu:contact")],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")]]
    )


def contact_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 WhatsApp", url=config.contact_whatsapp_url)],
            [InlineKeyboardButton(text="📷 Instagram", url=config.contact_instagram_url)],
            [InlineKeyboardButton(text="📢 Канал", url=config.shop_channel_url)],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


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
    rows.append(
        [InlineKeyboardButton(text="✏️ Миқдори дигар", callback_data="product:custom")]
    )
    rows.append(
        [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")]
    )
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
