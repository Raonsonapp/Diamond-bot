"""Default catalog and one-time-compatible ALMAZ TJ price migration."""
from sqlalchemy import select

from bot.db.models import Product, ProductCategory

# (display name, final diamond count, price, legacy counts to fold into this pack)
DEFAULT_PRODUCTS = [
    ("110 алмаз", 110, 9.00, {100, 110}),
    ("341 алмаз", 341, 28.50, {310, 341}),
    ("572 алмаз", 572, 47.50, {520, 572}),
    ("1166 алмаз", 1166, 91.50, {1060, 1166}),
    ("2398 алмаз", 2398, 184.00, {2180, 2398}),
    ("6160 алмаз", 6160, 449.00, {5600, 6160}),
    ("Ваучер Lite 90 алмаз", 90, 5.50, {90}),
    ("Ваучери ҳафтагӣ 450 алмаз", 450, 17.00, {450}),
    ("Ваучери моҳона 2600 алмаз", 2600, 62.00, {2600}),
]


async def seed_default_products(session) -> None:
    """Migrate old bonus+base packs into one final pack and deactivate duplicates.

    Prefer legacy base packs (100/310/520/1060/2180/5600) so existing
    supplier mapping stays on the surviving product. Never overwrite unrelated
    categories or voucher products.
    """
    result = await session.execute(select(Product))
    products = list(result.scalars().all())
    if not products:
        for name, units, price, _legacy in DEFAULT_PRODUCTS:
            session.add(Product(
                name=name, category=ProductCategory.DIAMONDS, diamonds=units,
                bonus_diamonds=0, price_somoni=price, cost_somoni=price,
            ))
        await session.commit()
        return

    changed = False
    for name, units, price, legacy_units in DEFAULT_PRODUCTS:
        is_voucher = "ваучер" in name.lower()
        candidates = [
            p for p in products
            if p.category == ProductCategory.DIAMONDS
            and p.diamonds in legacy_units
            and (("ваучер" in p.name.lower()) == is_voucher)
        ]
        if not candidates:
            product = Product(
                name=name, category=ProductCategory.DIAMONDS, diamonds=units,
                bonus_diamonds=0, price_somoni=price, cost_somoni=price,
            )
            session.add(product)
            products.append(product)
            changed = True
            continue

        # Prefer the original base pack, which is most likely to carry supplier mapping.
        legacy_base = {110: 100, 341: 310, 572: 520, 1166: 1060, 2398: 2180, 6160: 5600}
        preferred_units = legacy_base.get(units, units)
        candidates.sort(key=lambda p: (p.diamonds != preferred_units, p.id))
        survivor = candidates[0]
        for attr, value in (
            ("name", name), ("diamonds", units), ("bonus_diamonds", 0),
            ("price_somoni", price), ("is_active", True),
        ):
            if getattr(survivor, attr) != value:
                setattr(survivor, attr, value)
                changed = True
        # Deactivate duplicates so users only see one pack; retain their rows for old orders.
        for duplicate in candidates[1:]:
            if duplicate.is_active:
                duplicate.is_active = False
                changed = True

    if changed:
        await session.commit()
