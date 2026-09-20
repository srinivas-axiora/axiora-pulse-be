"""
app/api/billing.py
────────────────────────────────────────────────────────────────────────────────
Billing router — Razorpay Subscriptions. Mounted UNVERSIONED at prefix "/api"
(like the profile router) so paths match the existing SPA contract:

Routes:
  GET   /api/billing/plans          → list_plans        (public)
  POST  /api/billing/subscribe      → subscribe         (auth)
  POST  /api/billing/verify         → verify_payment    (auth)
  GET   /api/billing/subscription   → get_subscription  (auth)
  POST  /api/billing/cancel         → cancel            (auth)
  POST  /api/billing/webhook        → webhook           (public, signature-verified)

Response envelope:
  All JSON endpoints return the standard `{ success, data }` shape the frontend
  `billingService` expects. The webhook returns a bare 200 to Razorpay.
"""
import json
import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.limiter import limiter
from app.db.database import get_db
from app.db.models import User
from app.models.billing_models import (
    AccountStatusEnvelope,
    AllowanceEnvelope,
    CancelEnvelope,
    PlansEnvelope,
    SubscribeEnvelope,
    SubscribeRequest,
    SubscriptionEnvelope,
    VerifyEnvelope,
    VerifyPaymentRequest,
)
from app.services.billing_service import billing_service
from app.services.razorpay_service import razorpay_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["Billing"])


# ── List plans (public) ─────────────────────────────────────────────────────────

@router.get(
    "/plans",
    response_model=PlansEnvelope,
    summary="List active subscription plans",
)
@limiter.limit("60/minute")
async def list_plans(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> PlansEnvelope:
    plans = await billing_service.list_plans(db)
    return PlansEnvelope(data=plans)


# ── Subscribe (auth) ─────────────────────────────────────────────────────────────

@router.post(
    "/subscribe",
    response_model=SubscribeEnvelope,
    status_code=status.HTTP_201_CREATED,
    summary="Create a Razorpay subscription for the current user",
)
@limiter.limit("10/minute")
async def subscribe(
    request: Request,
    payload: SubscribeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscribeEnvelope:
    data = await billing_service.create_subscription(
        payload.planId, payload.billingPeriod, current_user, db
    )
    return SubscribeEnvelope(data=data)


# ── Account status (auth) ────────────────────────────────────────────────────────

@router.get(
    "/status",
    response_model=AccountStatusEnvelope,
    response_model_exclude_none=True,
    summary="Current plan + access status (for the profile 'My Plan' view)",
)
@limiter.limit("60/minute")
async def account_status(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AccountStatusEnvelope:
    data = await billing_service.get_account_status(current_user, db)
    return AccountStatusEnvelope(data=data)


# ── Select free plan (auth) ──────────────────────────────────────────────────────

@router.post(
    "/subscription/{plan_id}",
    response_model=AllowanceEnvelope,
    status_code=status.HTTP_201_CREATED,
    summary="Select the free plan — ensure the user's baseline allowance exists",
)
@limiter.limit("20/minute")
async def select_free_plan(
    request: Request,
    plan_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AllowanceEnvelope:
    data = await billing_service.select_free_plan(plan_id, current_user, db)
    return AllowanceEnvelope(data=data)


# ── Verify checkout signature (auth) ─────────────────────────────────────────────

@router.post(
    "/verify",
    response_model=VerifyEnvelope,
    summary="Verify a subscription payment signature returned by Checkout",
)
@limiter.limit("20/minute")
async def verify_payment(
    request: Request,
    payload: VerifyPaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VerifyEnvelope:
    data = await billing_service.verify_payment(
        payload.razorpay_payment_id,
        payload.razorpay_subscription_id,
        payload.razorpay_signature,
        current_user,
        db,
    )
    return VerifyEnvelope(data=data)


# ── Current subscription (auth) ──────────────────────────────────────────────────

@router.get(
    "/subscription",
    response_model=SubscriptionEnvelope,
    summary="Get the current user's subscription status",
)
@limiter.limit("60/minute")
async def get_subscription(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionEnvelope:
    data = await billing_service.get_current(current_user, db)
    return SubscriptionEnvelope(data=data)


# ── Cancel (auth) ────────────────────────────────────────────────────────────────

@router.post(
    "/cancel",
    response_model=CancelEnvelope,
    summary="Cancel the current subscription at period end",
)
@limiter.limit("10/minute")
async def cancel(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CancelEnvelope:
    data = await billing_service.cancel(current_user, db)
    return CancelEnvelope(data=data)


# ── Webhook (public, signature-verified) ─────────────────────────────────────────

@router.post(
    "/webhook",
    include_in_schema=True,
    summary="Razorpay webhook receiver (source of truth for subscription status)",
)
@limiter.limit("240/minute")
async def webhook(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    # Signature MUST be verified against the raw bytes, before any JSON parsing.
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    event_id = request.headers.get("X-Razorpay-Event-Id", "")

    if not signature or not event_id:
        logger.warning("Razorpay webhook missing signature/event-id headers.")
        return JSONResponse({"success": False}, status_code=status.HTTP_400_BAD_REQUEST)

    try:
        valid = razorpay_service.verify_webhook_signature(raw_body, signature)
    except RuntimeError as exc:
        logger.error("Webhook secret not configured: %s", exc)
        return JSONResponse({"success": False}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    if not valid:
        return JSONResponse({"success": False}, status_code=status.HTTP_400_BAD_REQUEST)

    try:
        event = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"success": False}, status_code=status.HTTP_400_BAD_REQUEST)

    event_type = event.get("event", "")
    await billing_service.handle_webhook(event_id, event_type, event, db)

    # Always ack 200 once verified+persisted so Razorpay stops retrying.
    return JSONResponse({"success": True})
