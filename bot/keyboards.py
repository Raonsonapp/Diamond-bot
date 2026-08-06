from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import config
from bot.db.models import Order, Product, ProductCategory


def sponsor_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Ба канал ҳамроҳ шудан", url=config.shop_channel_url)],
            [InlineKeyboardButton(text="✅ Санҷидан", callback_data="sponsor:check")],
        ]
    )


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
                InlineKeyboardButton(text="⭐ Отзив", callback_data="menu:reviews"),
                InlineKeyboardButton(text="🆘 Дастгирӣ", callback_data="menu:contact"),
            ],
            [
                InlineKeyboardButton(text="❓ Саволҳои маъмул", callback_data="menu:faq"),
                InlineKeyboardButton(text="ℹ️ Маълумот", callback_data="menu:about"),
            ],
        ]
    )


def review_channel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Кушодани канал", url=config.shop_channel_url)],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def games_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Free Fire", callback_data="menu:buy_diamonds")],
            [InlineKeyboardButton(text="🇮🇩 Free Fire Indonesia", callback_data="menu:buy_ff_id")],
            [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")],
        ]
    )


def telegram_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Telegram Stars", callback_data="menu:telegram_stars")],
            [InlineKeyboardButton(text="💎 Telegram Premium", callback_data="menu:telegram_premium")],
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


def _product_label(p: Product) -> str:
    # A plain pack's admin-given name is just its size ("100 диамонд"), so a
    # numeric diamond count says more than the name; a voucher/subscription
    # ("Ваучери ҳафтагӣ") has a name that carries real information the raw
    # diamond-equivalent number would hide — show whichever is meaningful.
    if p.name[:1].isdigit():
        bonus = f" (+{p.bonus_diamonds} бонус)" if p.bonus_diamonds else ""
        return f"{p.diamonds}{bonus} {p.unit_label} — {p.price_somoni:.2f} сомонӣ"
    return f"🎟 {p.name} — {p.price_somoni:.2f} сомонӣ"


def products_keyboard(
    products: list[Product], category: ProductCategory, telegram_kind: str | None = None
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=_product_label(p), callback_data=f"product:{p.id}")]
        for p in products
    ]
    # Free Fire (fixed official denominations only, per the admin) and
    # Bigo Live (FazerCards only sells fixed package sizes for it — no
    # sensible "any amount" quote to interpolate) both skip this button.
    # Telegram Premium is fixed 3/6/12-month plans too (any other number of
    # months isn't a real FazerCards offer) — only Stars is a flat rate
    # that any quantity in range can be quoted from.
    if category == ProductCategory.TELEGRAM and telegram_kind == "stars":
        rows.append(
            [InlineKeyboardButton(text="✏️ Миқдори дигар", callback_data=f"product:custom:{category.value}")]
        )
    if category == ProductCategory.DIAMONDS:
        rows.append(
            [InlineKeyboardButton(text="🛒 Якчанд бастаро якҷоя харидан", callback_data=f"cartmode:{category.value}")]
        )
    rows.append(
        [InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cart_select_keyboard(
    products: list[Product], category: ProductCategory, selected_ids: set[int]
) -> InlineKeyboardMarkup:
    rows = []
    for p in products:
        mark = "✅" if p.id in selected_ids else "⬜"
        rows.append(
            [InlineKeyboardButton(text=f"{mark} {_product_label(p)}", callback_data=f"cartitem:{p.id}")]
        )
    if selected_ids:
        total = sum(p.price_somoni for p in products if p.id in selected_ids)
        rows.append(
            [InlineKeyboardButton(text=f"🛍 Идома ({len(selected_ids)} — {total:.2f} сомонӣ)", callback_data="cart:checkout")]
        )
    rows.append(
        [InlineKeyboardButton(text="🔙 Якто-якто харидан", callback_data=f"cartmode:exit:{category.value}")]
    )
    rows.append([InlineKeyboardButton(text="🔙 Ба меню", callback_data="menu:main")])
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


def payment_link_keyboard(pay_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💳 Пардохт", url=pay_url)]]
    )


# All four route to the exact same receiving card (config.receiving_card_number)
# — Tajikistan's interbank card-to-card transfer works from any bank's app to
# any other bank's card, so there's no real difference in *where the money
# goes*, only in which bank the customer already has open. Keeping them as
# separate buttons (labelled with each bank's own name, same as a competitor
# bot's payment picker) rather than one generic "Пардохт" is purely so a
# customer immediately recognizes their own bank instead of wondering whether
# a "DC" card accepts a transfer from, say, their Eskhata app.
PAYMENT_METHODS = [
    ("dc", "🏦 Душанбе Сити"),
    ("eskhata", "🏦 Эсхата"),
    ("alif", "🏦 Алиф Мобайл"),
]


def payment_method_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"paymethod:{key}")] for key, label in PAYMENT_METHODS]
    rows.append([InlineKeyboardButton(text="❌ Бекор", callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
