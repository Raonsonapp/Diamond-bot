from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Order, OrderStatus, Product, User


async def upsert_user(session: AsyncSession, user_id: int, username: str | None, full_name: str | None) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, username=username, full_name=full_name)
        session.add(user)
    else:
        user.username = username
        user.full_name = full_name
    await session.commit()
    return user


async def list_active_products(session: AsyncSession) -> list[Product]:
    result = await session.execute(
        select(Product).where(Product.is_active.is_(True)).order_by(Product.diamonds)
    )
    return list(result.scalars().all())


async def get_product(session: AsyncSession, product_id: int) -> Product | None:
    return await session.get(Product, product_id)


async def create_order(
    session: AsyncSession,
    user_id: int,
    product: Product,
    ff_player_id: str,
    payment_provider: str,
) -> Order:
    order = Order(
        user_id=user_id,
        product_id=product.id,
        ff_player_id=ff_player_id,
        amount_somoni=product.price_somoni,
        payment_provider=payment_provider,
        status=OrderStatus.AWAITING_PAYMENT,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


async def get_order(session: AsyncSession, order_id: int) -> Order | None:
    result = await session.execute(
        select(Order).where(Order.id == order_id)
    )
    return result.scalar_one_or_none()


async def list_orders_by_status(session: AsyncSession, status: OrderStatus) -> list[Order]:
    result = await session.execute(
        select(Order).where(Order.status == status).order_by(Order.created_at)
    )
    return list(result.scalars().all())


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
