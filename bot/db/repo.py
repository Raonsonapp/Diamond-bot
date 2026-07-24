from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from bot.db.models import Order, OrderStatus, Product, ProductCategory, User


async def upsert_user(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    full_name: str | None,
    referred_by: int | None = None,
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        if referred_by == user_id:
            referred_by = None
        user = User(id=user_id, username=username, full_name=full_name, referred_by=referred_by)
        session.add(user)
    else:
        user.username = username
        user.full_name = full_name
    await session.commit()
    return user


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def accept_terms(session: AsyncSession, user: User) -> User:
    user.accepted_terms_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user)
    return user


async def credit_referral_balance(session: AsyncSession, user_id: int, amount: float) -> None:
    user = await session.get(User, user_id)
    if user is not None and amount:
        user.referral_balance = round(user.referral_balance + amount, 2)
        await session.commit()


async def deduct_referral_balance(session: AsyncSession, user: User, amount: float) -> User:
    user.referral_balance = round(user.referral_balance - amount, 2)
    await session.commit()
    await session.refresh(user)
    return user


async def count_referrals(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(User).where(User.referred_by == user_id)
    )
    return result.scalar_one()


async def top_referrers(session: AsyncSession, limit: int = 10) -> list[tuple[User, int]]:
    referred = aliased(User)
    stmt = (
        select(User, func.count(referred.id).label("referral_count"))
        .join(referred, referred.referred_by == User.id)
        .group_by(User.id)
        .order_by(func.count(referred.id).desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


async def list_active_products(
    session: AsyncSession, category: ProductCategory | None = None
) -> list[Product]:
    stmt = select(Product).where(Product.is_active.is_(True))
    if category is not None:
        stmt = stmt.where(Product.category == category)
    stmt = stmt.order_by(Product.diamonds)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    return await session.get(Product, product_id)


async def set_product_fzr_mapping(
    session: AsyncSession, product: Product, category_id: str, offer_id: str
) -> Product:
    product.fzr_category_id = category_id
    product.fzr_offer_id = offer_id
    await session.commit()
    await session.refresh(product)
    return product


async def set_product_bonus(session: AsyncSession, product: Product, bonus_diamonds: int) -> Product:
    product.bonus_diamonds = max(0, bonus_diamonds)
    await session.commit()
    await session.refresh(product)
    return product


async def set_product_price(
    session: AsyncSession, product: Product, price_somoni: float, cost_somoni: float | None = None
) -> Product:
    product.price_somoni = price_somoni
    if cost_somoni is not None:
        product.cost_somoni = cost_somoni
    await session.commit()
    await session.refresh(product)
    return product


async def create_order(
    session: AsyncSession,
    user_id: int,
    product: Product,
    ff_player_id: str,
    payment_provider: str,
    paid_with_referral_balance: bool = False,
    cart_group_id: str | None = None,
) -> Order:
    order = Order(
        user_id=user_id,
        product_id=product.id,
        ff_player_id=ff_player_id,
        amount_somoni=product.price_somoni,
        payment_provider=payment_provider,
        status=OrderStatus.PAID if paid_with_referral_balance else OrderStatus.AWAITING_PAYMENT,
        paid_with_referral_balance=paid_with_referral_balance,
        cart_group_id=cart_group_id,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def get_orders_by_group(session: AsyncSession, cart_group_id: str) -> list[Order]:
    result = await session.execute(
        select(Order).where(Order.cart_group_id == cart_group_id).order_by(Order.id)
    )
    return list(result.scalars().all())


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    result = await session.execute(
        select(Order).where(Order.id == order_id)
    )
    return result.scalar_one_or_none()


async def get_last_recipient(
    session: AsyncSession, user_id: int, category: ProductCategory
) -> str | None:
    """Most recent recipient (Free Fire player ID or Telegram @username)
    this user ordered something for, in this category — lets the bot offer
    a "use the same one again" shortcut instead of retyping every time."""
    result = await session.execute(
        select(Order.ff_player_id)
        .join(Product, Product.id == Order.product_id)
        .where(Order.user_id == user_id, Product.category == category)
        .order_by(Order.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def list_orders_by_status(session: AsyncSession, status: OrderStatus) -> list[Order]:
    result = await session.execute(
        select(Order).where(Order.status == status).order_by(Order.created_at)
    )
    return list(result.scalars().all())


async def find_orders_awaiting_amount(
    session: AsyncSession, amount_somoni: float, since: datetime
) -> list[Order]:
    """Candidate orders an incoming bank SMS of this amount could be for."""
    result = await session.execute(
        select(Order).where(
            Order.status == OrderStatus.AWAITING_PAYMENT,
            Order.created_at >= since,
            func.abs(Order.amount_somoni - amount_somoni) < 0.01,
        )
    )
    return list(result.scalars().all())


async def find_order_by_payment_reference(session: AsyncSession, reference: str) -> Order | None:
    result = await session.execute(select(Order).where(Order.payment_reference == reference))
    return result.scalars().first()


async def set_payment_proof_hash(session: AsyncSession, order: Order, proof_hash: str) -> Order:
    order.payment_proof_hash = proof_hash
    await session.commit()
    await session.refresh(order)
    return order


async def find_duplicate_proof(
    session: AsyncSession, proof_hash: str, exclude_order_id: int
) -> Order | None:
    result = await session.execute(
        select(Order).where(
            Order.payment_proof_hash == proof_hash,
            Order.id != exclude_order_id,
        )
    )
    return result.scalars().first()


async def set_order_status(
    session: AsyncSession,
    order: Order,
    status: OrderStatus,
    admin_note: str | None = None,
    payment_reference: str | None = None,
) -> Order:
    order.status = status
    if admin_note is not None:
        order.admin_note = admin_note
    if payment_reference is not None:
        order.payment_reference = payment_reference
    await session.commit()
    await session.refresh(order)
    return order


async def get_user_purchase_stats(session: AsyncSession, user_id: int) -> tuple[int, float]:
    result = await session.execute(
        select(func.count(Order.id), func.coalesce(func.sum(Order.amount_somoni), 0.0)).where(
            Order.user_id == user_id, Order.status == OrderStatus.DELIVERED
        )
    )
    count, total = result.one()
    return count, float(total)


async def top_buyers(session: AsyncSession, limit: int = 10) -> list[tuple[User, int, float]]:
    stmt = (
        select(
            User,
            func.count(Order.id).label("purchase_count"),
            func.coalesce(func.sum(Order.amount_somoni), 0.0).label("total_spent"),
        )
        .join(Order, Order.user_id == User.id)
        .where(Order.status == OrderStatus.DELIVERED)
        .group_by(User.id)
        .order_by(func.count(Order.id).desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(row[0], row[1], float(row[2])) for row in result.all()]


async def get_buyer_rank(session: AsyncSession, user_id: int) -> int | None:
    stmt = (
        select(Order.user_id, func.count(Order.id).label("cnt"))
        .where(Order.status == OrderStatus.DELIVERED)
        .group_by(Order.user_id)
        .order_by(func.count(Order.id).desc())
    )
    result = await session.execute(stmt)
    for idx, (uid, _cnt) in enumerate(result.all(), start=1):
        if uid == user_id:
            return idx
    return None


async def count_total_users(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(User))
    return result.scalar_one()


async def count_total_delivered_orders(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count()).select_from(Order).where(Order.status == OrderStatus.DELIVERED)
    )
    return result.scalar_one()
