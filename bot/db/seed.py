"""Default product catalog for ALMAZ TJ.

Prices were updated from the latest shop price list provided by the owner.
Existing databases are updated by product name for these known catalog items.
"""
from sqlalchemy import select

from bot.db.models import Product, ProductCategory

# (canonical name, diamond count, selling price in TJS)
DEFAULT_PRODUCTS = [
    ("110 алмаз", 110, 9),
    ("341 алмаз", 341, 28),
    ("572 алмаз", 572, 47),
    ("1166 алмаз", 1166, 91),
    ("2398 алмаз", 2398, 184),
    ("6160 алмаз", 6160, 445),
    ("Ваучер Lite 90", 90, 5.50),
    ("Ваучер ҳафтагӣ 450", 450, 17),
    ("Ваучер моҳона 2600", 2600, 62),
]


async def seed_default_products(session) -> None:
    """Create defaults on empty DB; update only these named catalog products on existing DB."""
    result = await session.execute(select(Product))
    products = list(result.scalars().all())
    if not products:
        for name, diamonds, price in DEFAULT_PRODUCTS:
            session.add(Product(
                name=name,
                category=ProductCategory.DIAMONDS,
                diamonds=diamonds,
                price_somoni=price,
                cost_somoni=price,
            ))
        await session.commit()
        return

    # Update matching products by known diamond quantity and category, so
    # current customers get the new prices after deployment. Do not touch
    # unrelated products or products outside this requested price list.
    changed = False
    for name, diamonds, price in DEFAULT_PRODUCTS:
        candidates = [
            p for p in products
            if p.category == ProductCategory.DIAMONDS and p.diamonds == diamonds
            and (
                ("ваучер" in name.lower() and "ваучер" in p.name.lower())
                or ("ваучер" not in name.lower() and "ваучер" not in p.name.lower())
            )
        ]
        product = candidates[0] if candidates else None
        if product is None:
            session.add(Product(
                name=name,
                category=ProductCategory.DIAMONDS,
                diamonds=diamonds,
                price_somoni=price,
                cost_somoni=price,
            ))
            changed = True
        else:
            if product.name != name:
                product.name = name
                changed = True
            if round(float(product.price_somoni), 2) != price:
                product.price_somoni = price
                changed = True
    if changed:
        await session.commit()
