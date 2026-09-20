"""
app/services/billing_service.py
────────────────────────────────────────────────────────────────────────────────
Billing service — all database operations for Razorpay Subscriptions, plus the
webhook processing that keeps subscription state authoritative.

Design:
  - The router stays thin; all persistence and Razorpay orchestration live here.
  - `handle_webhook()` is idempotent (WebhookEvent ledger) and is the *only* place
    a subscription's status is advanced to active/halted/cancelled/etc. The browser
    `verify` path is treated as a best-effort hint, never the source of truth.
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment, Plan, Role, Subscription, User, WebhookEvent
from app.models.billing_models import AllowanceOut, PlanOut, SubscribeOut, SubscriptionOut
from app.services.razorpay_service import razorpay_service

logger = logging.getLogger(__name__)

# Subscription statuses that mean the user still holds (or is completing) a live
# subscription — used to block creating a duplicate.
_NON_TERMINAL = {"created", "authenticated", "active", "pending", "halted"}
# Statuses that grant paid entitlement. Only a webhook-confirmed 'active'
# subscription counts as paid — 'authenticated' means checkout was authorized but
# the charge hasn't been confirmed, so a mid-payment user is still routed to pay.
_ENTITLED = {"active"}
# Terminal / churned statuses — the paid relationship has ended (payment retries
# exhausted, cancelled, run to completion, or expired). When a user's last entitled
# subscription reaches one of these, their 'member' role is revoked to 'viewer'.
_CHURNED = {"halted", "cancelled", "completed", "expired"}

# Feature flag — when false, the payment gate is bypassed and every user is treated
# as entitled. Intended for LOCAL DEVELOPMENT so developers can reach subscription-
# gated features without configuring Razorpay keys, plans, or webhooks/ngrok.
#
# Defaults to TRUE (secure by default). The gate is bypassed ONLY when the value is
# an explicit falsey token — so a missing OR malformed value (e.g. a typo like
# "flase") still ENFORCES, and a forgotten flag can never silently open QA/production.
# Set `SUBSCRIPTION_ENFORCED=false` in a local .env only.
SUBSCRIPTION_ENFORCED = os.getenv("SUBSCRIPTION_ENFORCED", "true").strip().lower() not in (
    "false", "0", "f", "no", "n", "off",
)


def _epoch_to_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


@dataclass(frozen=True)
class PlanLimits:
    """The per-plan quota limits enforced by the entitlement layer.

    Integer caps use ``None`` to mean "unlimited / not enforced". ``export_enabled``
    is a hard boolean. Resolved via ``BillingService.get_plan_limits`` — always call
    that rather than reading ``Plan`` columns directly, so admin bypass and the
    ``SUBSCRIPTION_ENFORCED`` dev flag are honoured in one place.
    """
    workspace_limit: int | None
    survey_response_cap: int | None
    regeneration_limit: int | None
    export_enabled: bool


# Everything permitted — admins and (local dev) disabled enforcement.
_UNLIMITED = PlanLimits(
    workspace_limit=None,
    survey_response_cap=None,
    regeneration_limit=None,
    export_enabled=True,
)


class BillingService:
    """Stateless — all state lives in the DB session."""

    # ── Plans ─────────────────────────────────────────────────────────────────

    async def list_plans(self, db: AsyncSession) -> list[PlanOut]:
        result = await db.execute(
            select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.tier)
        )
        plans = result.scalars().all()
        return [
            PlanOut(
                id=p.code,
                name=p.name,
                priceMonthly=p.price_monthly,
                priceYearly=p.price_yearly,
                features=p.features or [],
                description=p.description,
                popular=p.popular,
            )
            for p in plans
        ]

    # ── Free plan selection (issue #190/#191) ─────────────────────────────────────

    async def select_free_plan(self, plan_code: str, user: User, db: AsyncSession) -> AllowanceOut:
        """Select the FREE plan: ensure the user's baseline allowance row exists.

        Paid plans must go through Razorpay Checkout (`create_subscription`) — their
        allowance is granted by the `subscription.charged` webhook. This endpoint only
        handles the free tier (Case 1): it creates the `user_allowed_workspaces` row at
        the free baseline if absent, and is idempotent (returns the existing row
        otherwise, never adding on top).
        """
        plan = (
            await db.execute(
                select(Plan).where(Plan.code == plan_code, Plan.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Plan '{plan_code}' not found.")
        if (plan.price_monthly or 0) > 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Plan '{plan_code}' is a paid plan — use /api/billing/subscribe to purchase it.",
            )

        from app.services.entitlements_service import entitlements_service  # lazy: avoid cycle

        row = await entitlements_service.get_or_create(user.id, db)
        return AllowanceOut(
            planCode=plan.code,
            allowedWorkspaces=row.allowed_workspaces,
            allowedResponses=row.allowed_responses,
        )

    # ── Subscribe ───────────────────────────────────────────────────────────────

    async def create_subscription(
        self, plan_code: str, billing_period: str, user: User, db: AsyncSession
    ) -> SubscribeOut:
        billing_period = (billing_period or "monthly").lower()
        if billing_period not in ("monthly", "yearly"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "billingPeriod must be 'monthly' or 'yearly'.")

        plan = (
            await db.execute(
                select(Plan).where(Plan.code == plan_code, Plan.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Plan '{plan_code}' not found.")

        rzp_plan_id = (
            plan.razorpay_plan_id_yearly if billing_period == "yearly" else plan.razorpay_plan_id_monthly
        )
        if not rzp_plan_id:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                f"Plan '{plan_code}' ({billing_period}) is not yet configured with a Razorpay plan id.",
            )

        # Block a second live subscription for the same user.
        existing = (
            await db.execute(
                select(Subscription)
                .where(Subscription.user_id == user.id, Subscription.status.in_(_NON_TERMINAL))
                .order_by(Subscription.created_at.desc())
            )
        ).scalars().first()
        if existing is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "You already have an active or pending subscription.",
            )

        try:
            rzp_sub = razorpay_service.create_subscription(
                rzp_plan_id,
                notes={"user_id": str(user.id), "plan_code": plan.code, "period": billing_period},
            )
        except RuntimeError as exc:
            # Razorpay not configured.
            logger.error("Razorpay configuration error: %s", exc)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Payment gateway is not configured.")
        except Exception:
            logger.exception("Razorpay subscription creation failed.")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to create subscription with the payment gateway.")

        subscription = Subscription(
            user_id=user.id,
            plan_id=plan.id,
            razorpay_subscription_id=rzp_sub["id"],
            razorpay_plan_id=rzp_plan_id,
            billing_period=billing_period,
            status=rzp_sub.get("status", "created"),
            short_url=rzp_sub.get("short_url"),
        )
        db.add(subscription)
        await db.flush()

        return SubscribeOut(
            subscriptionId=rzp_sub["id"],
            keyId=razorpay_service.key_id,
            shortUrl=rzp_sub.get("short_url"),
        )

    # ── Verify (post-checkout, best-effort) ──────────────────────────────────────

    async def verify_payment(
        self, razorpay_payment_id: str, razorpay_subscription_id: str, razorpay_signature: str,
        user: User, db: AsyncSession,
    ) -> SubscriptionOut:
        if not razorpay_service.verify_subscription_payment_signature(
            razorpay_payment_id, razorpay_subscription_id, razorpay_signature
        ):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Payment signature verification failed.")

        subscription = (
            await db.execute(
                select(Subscription).where(
                    Subscription.razorpay_subscription_id == razorpay_subscription_id,
                    Subscription.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if subscription is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found.")

        # Optimistically reflect authorization; the webhook will confirm 'active'.
        if subscription.status == "created":
            subscription.status = "authenticated"
        await db.flush()

        return await self._to_out(subscription, db)

    # ── Current subscription ──────────────────────────────────────────────────────

    async def get_current(self, user: User, db: AsyncSession) -> SubscriptionOut:
        subscription = (
            await db.execute(
                select(Subscription)
                .where(Subscription.user_id == user.id)
                .order_by(Subscription.created_at.desc())
            )
        ).scalars().first()
        if subscription is None:
            return SubscriptionOut(status="none")
        return await self._to_out(subscription, db)

    async def cancel(self, user: User, db: AsyncSession) -> SubscriptionOut:
        subscription = (
            await db.execute(
                select(Subscription)
                .where(Subscription.user_id == user.id, Subscription.status.in_(_NON_TERMINAL))
                .order_by(Subscription.created_at.desc())
            )
        ).scalars().first()
        if subscription is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No active subscription to cancel.")

        try:
            razorpay_service.cancel_subscription(
                subscription.razorpay_subscription_id, cancel_at_cycle_end=True
            )
        except Exception:
            logger.exception("Razorpay subscription cancel failed.")
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Failed to cancel subscription with the payment gateway.")

        subscription.cancel_at_period_end = True
        await db.flush()
        return await self._to_out(subscription, db)

    # ── Webhook (source of truth) ─────────────────────────────────────────────────

    async def handle_webhook(
        self, event_id: str, event_type: str, payload: dict, db: AsyncSession
    ) -> None:
        """Idempotently process one Razorpay webhook event."""
        # Idempotency guard — insert the event id first; a duplicate delivery collides.
        event = WebhookEvent(event_id=event_id, event_type=event_type, payload=payload)
        db.add(event)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            logger.info("Duplicate webhook event %s ignored.", event_id)
            return

        entities = payload.get("payload", {})
        sub_entity = entities.get("subscription", {}).get("entity")
        pay_entity = entities.get("payment", {}).get("entity")

        if sub_entity:
            await self._apply_subscription_update(sub_entity, db)
            # Each successful charge (including every monthly renewal) accumulates the
            # plan's workspace/response grant onto the user's allowance.
            if event_type == "subscription.charged":
                await self._grant_charge_entitlements(sub_entity, db)
        if pay_entity:
            await self._record_payment(pay_entity, db)

        event.processed = True
        await db.flush()

    async def _grant_charge_entitlements(self, entity: dict, db: AsyncSession) -> None:
        """On subscription.charged, add the plan's grant to the user's allowance."""
        rzp_sub_id = entity.get("id")
        if not rzp_sub_id:
            return
        subscription = (
            await db.execute(
                select(Subscription).where(Subscription.razorpay_subscription_id == rzp_sub_id)
            )
        ).scalar_one_or_none()
        if subscription is None or not subscription.user_id or subscription.plan_id is None:
            return
        plan = (
            await db.execute(select(Plan).where(Plan.id == subscription.plan_id))
        ).scalar_one_or_none()
        if plan is None:
            return
        from app.services.entitlements_service import entitlements_service  # lazy: avoid cycle

        await entitlements_service.grant_for_plan(subscription.user_id, plan, db)

    async def _apply_subscription_update(self, entity: dict, db: AsyncSession) -> None:
        rzp_sub_id = entity.get("id")
        if not rzp_sub_id:
            return
        subscription = (
            await db.execute(
                select(Subscription).where(Subscription.razorpay_subscription_id == rzp_sub_id)
            )
        ).scalar_one_or_none()
        if subscription is None:
            logger.warning("Webhook for unknown subscription %s — skipping.", rzp_sub_id)
            return

        new_status = entity.get("status")
        if new_status:
            subscription.status = new_status
        subscription.current_start = _epoch_to_dt(entity.get("current_start")) or subscription.current_start
        subscription.current_end = _epoch_to_dt(entity.get("current_end")) or subscription.current_end
        await db.flush()
        logger.info("Subscription %s → status=%s", rzp_sub_id, subscription.status)

        # Keep the user's role in sync with their subscription entitlement.
        if subscription.user_id:
            await self._sync_role_to_entitlement(subscription, db)

    async def _sync_role_to_entitlement(self, subscription: Subscription, db: AsyncSession) -> None:
        """Align the subscriber's role with their live entitlement.

        - Subscription becomes ``active``  → upgrade viewer → member.
        - Subscription churns (halted/cancelled/completed/expired) → downgrade
          member → viewer, but ONLY if the user has no other still-active
          subscription. Admins are never touched.
        """
        user = (
            await db.execute(select(User).where(User.id == subscription.user_id))
        ).scalar_one_or_none()
        if user is None or user.has_role("admin"):
            return

        status = subscription.status

        if status in _ENTITLED:
            if not user.has_role("member"):
                member_role = (
                    await db.execute(select(Role).where(Role.name == "member"))
                ).scalar_one_or_none()
                if member_role:
                    user.role = member_role
                    await db.flush()
                    logger.info(
                        "User %s role upgraded to member (subscription %s)",
                        user.id, subscription.razorpay_subscription_id,
                    )
            return

        if status in _CHURNED and user.has_role("member"):
            # Don't revoke if another subscription still grants entitlement.
            other_active = (
                await db.execute(
                    select(Subscription.id).where(
                        Subscription.user_id == user.id,
                        Subscription.status.in_(_ENTITLED),
                        Subscription.id != subscription.id,
                    )
                )
            ).first()
            if other_active is not None:
                return
            viewer_role = (
                await db.execute(select(Role).where(Role.name == "viewer"))
            ).scalar_one_or_none()
            if viewer_role:
                user.role = viewer_role
                await db.flush()
                logger.info(
                    "User %s role downgraded to viewer (subscription %s churned: %s)",
                    user.id, subscription.razorpay_subscription_id, status,
                )

    async def _record_payment(self, entity: dict, db: AsyncSession) -> None:
        rzp_payment_id = entity.get("id")
        if not rzp_payment_id:
            return
        # Skip if already recorded (idempotent on payment id).
        exists = (
            await db.execute(
                select(Payment.id).where(Payment.razorpay_payment_id == rzp_payment_id)
            )
        ).scalar_one_or_none()
        if exists is not None:
            return

        # Link back to the subscription (and its user) when present.
        subscription = None
        rzp_sub_id = entity.get("subscription_id")
        if rzp_sub_id:
            subscription = (
                await db.execute(
                    select(Subscription).where(Subscription.razorpay_subscription_id == rzp_sub_id)
                )
            ).scalar_one_or_none()

        db.add(
            Payment(
                user_id=subscription.user_id if subscription else None,
                subscription_id=subscription.id if subscription else None,
                razorpay_payment_id=rzp_payment_id,
                razorpay_invoice_id=entity.get("invoice_id"),
                amount=entity.get("amount", 0) or 0,
                currency=entity.get("currency", "INR"),
                status=entity.get("status", "unknown"),
                method=entity.get("method"),
            )
        )
        await db.flush()

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _to_out(self, subscription: Subscription, db: AsyncSession) -> SubscriptionOut:
        plan = None
        if subscription.plan_id is not None:
            plan = (
                await db.execute(select(Plan).where(Plan.id == subscription.plan_id))
            ).scalar_one_or_none()
        return SubscriptionOut(
            status=subscription.status,
            planCode=plan.code if plan else None,
            planName=plan.name if plan else None,
            billingPeriod=subscription.billing_period,
            currentEnd=subscription.current_end,
            cancelAtPeriodEnd=subscription.cancel_at_period_end,
        )

    async def has_active_entitlement(self, user: User, db: AsyncSession) -> bool:
        """True if the user currently holds a paid, entitled subscription.

        When `SUBSCRIPTION_ENFORCED` is disabled (local dev), the gate is bypassed
        and every user is treated as entitled. This is the single chokepoint used
        both by the auth-response `hasActivePlan` and the `require_active_subscription`
        dependency, so one flag flips the entire payment gate.
        """
        if not SUBSCRIPTION_ENFORCED:
            return True

        row = (
            await db.execute(
                select(Subscription.id).where(
                    Subscription.user_id == user.id, Subscription.status.in_(_ENTITLED)
                )
            )
        ).first()
        return row is not None

    # ── Per-plan quota limits (entitlement layer) ──────────────────────────────

    async def _resolve_active_plan(self, user: User, db: AsyncSession) -> Plan | None:
        """Return the Plan behind the user's current entitled subscription, if any."""
        subscription = (
            await db.execute(
                select(Subscription)
                .where(Subscription.user_id == user.id, Subscription.status.in_(_ENTITLED))
                .order_by(Subscription.created_at.desc())
            )
        ).scalars().first()
        if subscription is None or subscription.plan_id is None:
            return None
        return (
            await db.execute(select(Plan).where(Plan.id == subscription.plan_id))
        ).scalar_one_or_none()

    async def get_plan_limits(self, user: User, db: AsyncSession) -> PlanLimits:
        """Resolve the quota limits that apply to ``user`` right now.

        The single place quota limits are read. Resolution order:
          1. Admins and disabled enforcement (local dev) → unlimited.
          2. The user's active (entitled) subscription plan.
          3. Fallback: the lowest active tier, so caps are always defined even
             before/without a subscription (paid features sit behind the paywall
             anyway; this just keeps the numbers sane and never returns "unlimited"
             for an ordinary user).
        """
        if not SUBSCRIPTION_ENFORCED or user.has_role("admin"):
            return _UNLIMITED

        plan = await self._resolve_active_plan(user, db)
        if plan is None:
            plan = (
                await db.execute(
                    select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.tier)
                )
            ).scalars().first()
        if plan is None:
            # No catalog at all — fail open rather than block every user.
            return _UNLIMITED

        return PlanLimits(
            workspace_limit=plan.workspace_limit,
            survey_response_cap=plan.survey_response_cap,
            regeneration_limit=plan.regeneration_limit,
            export_enabled=plan.export_enabled,
        )

    async def assert_export_allowed(self, user: User, db: AsyncSession) -> None:
        """Raise 402 if the user's plan does not include report export."""
        limits = await self.get_plan_limits(user, db)
        if not limits.export_enabled:
            raise HTTPException(
                status.HTTP_402_PAYMENT_REQUIRED,
                "Report export isn't included in your current plan. "
                "Upgrade to Builder or Pro to export reports.",
            )


billing_service = BillingService()
