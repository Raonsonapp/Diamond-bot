from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import config
from bot.db.models import Order, Product, ProductCategory


def terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Қабул мекунам", callback_data="terms:accept")]]
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎮 Бозиҳо", callback_data="menu:games"),
                InlineKeyboardButton(text="✈️ Telegram", callback_data="menu:telegram"),
            ],
            [
                InlineKeyboardButton(text="👤 Профил", callback_data="menu:profile"),
                InlineKeyboardButton(text="🤝 Реферал", callback_data="menu:referral"),
            ],
            [
                InlineKeyboardButton(text="⭐ Отзив", url=config.shop_channel_url),
                InlineKeyboardButton(text="🆘 Дастгирӣ", callback_data="menu:contact"),
            ],
            [
                InlineKeyboardButton(text="❓ Саволҳои маъмул", callback_data="menu:faq"),
                InlineKeyboardButton(text="ℹ️ Маълумот", callback_data="menu:about"),
            ],
        ]
    )


def games_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Free Fire", callback_data="menu:buy_diamonds")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def profile_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Фармоишҳои ман", callback_data="menu:myorders")],
            [InlineKeyboardButton(text="🏆 Топ харидорон", callback_data="menu:top_buyers")],
            [InlineKeyboardButton(text="🎖 Топ рефералдорон", callback_data="menu:top_referrers")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def referral_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎖 Топ рефералдорон", callback_data="menu:top_referrers")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
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


def products_keyboard(products: list[Product], category: ProductCategory) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{p.diamonds} {p.unit_label} — {p.price_somoni:.0f} сомонӣ",
                callback_data=f"product:{p.id}",
            )
        ]
        for p in products
    ]
    rows.append(
        [InlineKeyboardButton(text="✏️ Миқдори дигар", callback_data=f"product:custom:{category.value}")]
    )
    rows.append(
        [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reuse_recipient_keyboard(recipient: str, label_suffix: str = "") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Истифодаи: {recipient}{label_suffix}",
                    callback_data=f"reuseid:{recipient}",
                )
            ]
        ]
    )


def review_prompt_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Гузарондан", callback_data=f"review:skip:{order_id}")]
        ]
    )


def confirm_order_keyboard(offer_balance_payment: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if offer_balance_payment:
        rows.append(
            [InlineKeyboardButton(text="💰 Пардохт аз баланси реферал", callback_data="order:pay_balance")]
        )
    rows.append(
        [
            InlineKeyboardButton(text="✅ Тасдиқ (бо чек)", callback_data="order:confirm"),
            InlineKeyboardButton(text="❌ Бекор", callback_data="order:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
