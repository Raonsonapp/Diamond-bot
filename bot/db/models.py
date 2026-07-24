import enum
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class OrderStatus(str, enum.Enum):
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ProductCategory(str, enum.Enum):
    DIAMONDS = "diamonds"  # Free Fire diamonds — recipient is a player ID
    TELEGRAM = "telegram"  # Telegram Stars/Premium — recipient is a @username


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram user id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    accepted_terms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    referred_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    referral_balance: Mapped[float] = mapped_column(Float, default=0.0)

    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Product(Base):
    """A diamond package or Telegram Stars/Premium package the bot sells."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[ProductCategory] = mapped_column(
        Enum(ProductCategory), default=ProductCategory.DIAMONDS
    )
    diamonds: Mapped[int] = mapped_column(Integer)  # unit count: diamonds, or Stars
    # Extra units the supplier throws in on top of `diamonds` for this pack
    # (e.g. FazerCards' "110_diamonds" offer for a 100-pack = 10 bonus) —
    # set automatically by /mapproduct from the live offer, so the bot can
    # advertise the same bonus the supplier's own site shows.
    bonus_diamonds: Mapped[int] = mapped_column(Integer, default=0)
    price_somoni: Mapped[float] = mapped_column(Float)
    cost_somoni: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(default=True)
    # FazerCards (api.fzr.cards) mapping — set via /mapproduct once known
    # from /fzr_categories + /fzr_offers. Empty means: no auto-delivery for
    # this product, falls back to manual fulfillment.
    fzr_category_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fzr_offer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def margin_somoni(self) -> float:
        return round(self.price_somoni - self.cost_somoni, 2)

    @property
    def unit_label(self) -> str:
        return "💎" if self.category == ProductCategory.DIAMONDS else "⭐"

    @property
    def total_diamonds(self) -> int:
        return self.diamonds + self.bonus_diamonds


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id"))
    # Free Fire player ID (diamonds) or @username (Telegram Stars/Premium),
    # depending on the product's category.
    ff_player_id: Mapped[str] = mapped_column(String(32))
    amount_somoni: Mapped[float] = mapped_column(Float)
    paid_with_referral_balance: Mapped[bool] = mapped_column(default=False)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus), default=OrderStatus.AWAITING_PAYMENT
    )
    payment_provider: Mapped[str] = mapped_column(String(32), default="manual")
    payment_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Shared token for orders created together in one "buy several packs at
    # once" checkout — one payment/invoice covers the whole group, and
    # admin Paid/Delivered taps cascade to every order sharing this value.
    # Null for ordinary single-product orders.
    cart_group_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    admin_note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    # SHA-256 of the payment-proof photo bytes, so the same screenshot can't
    # be reused across orders without the admin being warned. Not a
    # substitute for actually checking the bank app — just catches replay.
    payment_proof_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    user: Mapped["User"] = relationship(back_populates="orders")
    product: Mapped["Product"] = relationship()
