"""Default product catalog, seeded automatically on first run.

Values taken directly from the price list image the admin provided. Cost
(харид/purchase price) is unknown, so it's set equal to the sale price as
a neutral placeholder — that's only used for the admin's own profit view
(/products) and is never shown to customers. Tell the bot the real
wholesale cost and it'll be corrected.
"""

from sqlalchemy import select

from bot.db.models import Product

DEFAULT_PRODUCTS = [
    # (name, diamonds, price_somoni)
    ("100 диамонд", 100, 10),
    ("310 диамонд", 310, 30),
    ("520 диамонд", 520, 50),
    ("1060 диамонд", 1060, 110),
    ("2180 диамонд", 2180, 210),
    ("5600 диамонд", 5600, 500),
    ("Ваучери ҳафтагӣ", 450, 18),
    ("Ваучери моҳона", 2600, 99),
]


async def seed_default_products(session) -> None:
    result = await session.execute(select(Product.id).limit(1))
    if result.scalar_one_or_none() is not None:
        return  # products already exist — never overwrite admin's own edits

    for name, diamonds, price in DEFAULT_PRODUCTS:
        session.add(
            Product(name=name, diamonds=diamonds, price_somoni=price, cost_somoni=price)
        )
    await session.commit()
