"""Authenticated customer API: wallet balance, product catalog and wallet-paid orders."""
import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from aiohttp import web
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.db.models import (
    ApiOrderRequest, CustomerApiKey, Order, OrderStatus, Product, ProductCategory,
    User, WalletTransaction,
)
from bot.db.session import get_session
from bot.formatting import format_price

log = logging.getLogger(__name__)


async def _authenticate(request: web.Request) -> int | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    raw = auth[7:].strip()
    if not raw or len(raw) > 200:
        return None
    digest = hashlib.sha256(raw.encode()).hexdigest()
    async with get_session() as session:
        result = await session.execute(
            select(CustomerApiKey).where(
                CustomerApiKey.key_hash == digest,
                CustomerApiKey.revoked_at.is_(None),
            )
        )
        key = result.scalar_one_or_none()
        return key.user_id if key else None


async def api_me(request: web.Request) -> web.Response:
    user_id = await _authenticate(request)
    if user_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    async with get_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            return web.json_response({"error": "account_not_found"}, status=404)
        return web.json_response({
            "telegram_user_id": user_id,
            "wallet_balance_somoni": round(float(user.wallet_balance_somoni or 0), 2),
            "currency": "TJS",
        })


async def api_products(request: web.Request) -> web.Response:
    user_id = await _authenticate(request)
    if user_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    async with get_session() as session:
        result = await session.execute(
            select(Product).where(Product.is_active.is_(True)).order_by(Product.id)
        )
        products = list(result.scalars().all())
    return web.json_response({
        "products": [{
            "id": p.id, "name": p.display_name, "category": p.category.value,
            "quantity": p.total_diamonds, "price_somoni": round(p.price_somoni, 2),
        } for p in products]
    })


def _valid_recipient(product: Product, recipient: str) -> bool:
    if product.category in (ProductCategory.DIAMONDS, ProductCategory.BIGO_LIVE, ProductCategory.FF_INDONESIA):
        return recipient.isdigit() and 5 <= len(recipient) <= 15
    recipient = recipient.removeprefix("@")
    return 5 <= len(recipient) <= 32 and recipient.replace("_", "").isalnum()


async def _fulfill_after_response(bot, order_id: int) -> None:
    try:
        from bot.services.fulfillment import confirm_and_deliver
        await confirm_and_deliver(bot, order_id, payment_reference="wallet_api")
    except Exception:
        log.exception("Wallet API order %s fulfillment failed; admin must inspect", order_id)


async def api_create_order(request: web.Request) -> web.Response:
    user_id = await _authenticate(request)
    if user_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    idem = request.headers.get("Idempotency-Key", "").strip()
    if not idem or len(idem) > 128:
        return web.json_response({"error": "Idempotency-Key header (1-128 chars) is required"}, status=400)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return web.json_response({"error": "JSON object required"}, status=400)
        product_id = int(body.get("product_id"))
        recipient = str(body.get("recipient", "")).strip()
    except (ValueError, TypeError, json.JSONDecodeError):
        return web.json_response({"error": "invalid_json_or_product_id"}, status=400)
    if not recipient or len(recipient) > 32:
        return web.json_response({"error": "invalid_recipient"}, status=400)

    order_id = None
    existing_order = False
    async with get_session() as session:
        # Idempotency: same key can replay the original result, never charge twice.
        existing = await session.execute(select(ApiOrderRequest).where(
            ApiOrderRequest.user_id == user_id,
            ApiOrderRequest.idempotency_key == idem,
        ))
        old = existing.scalar_one_or_none()
        if old is not None:
            if old.product_id != product_id or old.recipient != recipient:
                return web.json_response({"error": "idempotency_key_reused_with_different_payload"}, status=409)
            order_id = old.order_id
            existing_order = True
        else:
            product = await session.get(Product, product_id)
            if product is None or not product.is_active:
                return web.json_response({"error": "product_not_found_or_inactive"}, status=404)
            if not _valid_recipient(product, recipient):
                return web.json_response({"error": "recipient_format_invalid"}, status=400)

            user_result = await session.execute(
                select(User).where(User.id == user_id).with_for_update()
            )
            user = user_result.scalar_one_or_none()
            if user is None:
                return web.json_response({"error": "account_not_found"}, status=404)
            price = round(float(product.price_somoni), 2)
            balance = round(float(user.wallet_balance_somoni or 0), 2)
            if balance < price:
                return web.json_response({
                    "error": "insufficient_balance", "balance_somoni": balance,
                    "required_somoni": price,
                }, status=402)

            # Debit and order + ledger + idempotency row are committed together.
            user.wallet_balance_somoni = round(balance - price, 2)
            order = Order(
                user_id=user_id, product_id=product.id, ff_player_id=recipient,
                amount_somoni=price, payment_provider="wallet_api",
                status=OrderStatus.PAID,
            )
            session.add(order)
            await session.flush()
            session.add(WalletTransaction(
                user_id=user_id, amount_somoni=-price, kind="api_purchase",
                reference=f"api_purchase:{user_id}:{hashlib.sha256(idem.encode()).hexdigest()}", note=f"order_id:{order.id}",
            ))
            session.add(ApiOrderRequest(
                user_id=user_id, idempotency_key=idem, product_id=product_id,
                recipient=recipient, order_id=order.id,
            ))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return web.json_response({"error": "idempotency_conflict_retry_lookup"}, status=409)
            order_id = order.id

    if order_id is None:
        return web.json_response({"error": "order_creation_failed"}, status=500)

    if not existing_order:
        # Kick off the supplier attempt after the atomic wallet/order commit.
        # If the worker fails/restarts, the paid order remains in the admin queue.
        asyncio.create_task(_fulfill_after_response(request.app["telegram_bot"], order_id))

    async with get_session() as session:
        order = await session.get(Order, order_id)
        product = await session.get(Product, order.product_id) if order else None
        status = order.status.name.lower() if order else "unknown"
    return web.json_response({
        "order_id": order_id, "status": status,
        "product": product.display_name if product else None,
        "charged_somoni": round(float(order.amount_somoni), 2) if order else None,
        "recipient": order.ff_player_id if order else None,
        "note": "Order accepted. Delivery status may update asynchronously; query this order in the shop bot or contact support.",
    }, status=202)


async def api_order_status(request: web.Request) -> web.Response:
    user_id = await _authenticate(request)
    if user_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    raw_id = request.match_info.get("order_id", "")
    if not raw_id.isdigit():
        return web.json_response({"error": "invalid_order_id"}, status=400)
    async with get_session() as session:
        order = await session.get(Order, int(raw_id))
        if order is None or order.user_id != user_id:
            return web.json_response({"error": "order_not_found"}, status=404)
        return web.json_response({
            "order_id": order.id, "status": order.status.name.lower(),
            "amount_somoni": round(order.amount_somoni, 2),
            "recipient": order.ff_player_id,
        })


def register_wallet_api(app: web.Application, bot) -> None:
    app["telegram_bot"] = bot
    app.router.add_get("/api/v1/me", api_me)
    app.router.add_get("/api/v1/products", api_products)
    app.router.add_post("/api/v1/orders", api_create_order)
    app.router.add_get("/api/v1/orders/{order_id}", api_order_status)
