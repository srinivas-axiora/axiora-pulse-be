"""
app/services/entitlements_service.py
────────────────────────────────────────────────────────────────────────────────
Per-user accumulating entitlements (issues #191/#192/#194).

Each user has ONE `user_allowed_workspaces` row: a running allowance that only ever
grows. Free baseline = 1 workspace / 100 responses; every paid Razorpay charge adds
the plan's amounts on top (Builder +3 / +500, Pro +10 / +2000), including on each
monthly renewal. There is no decrement.

Single source of truth for the two limits enforced today:
  - workspaces  → checked in workspace_service.create_workspace
  - responses   → checked in survey_service.submit_public_survey
Export stays a per-plan on/off (billing_service); storage/regenerations/analytics
are out of scope (not built).
"""
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import now_ist
from app.db.models import Plan, Subscription, User, UserAllowedWorkspaces

logger = logging.getLogger(__name__)

# Free tier baseline — mirrors the Starter plan. A user's row is created with these
# the first time it is needed; paid charges accumulate on top.
FREE_WORKSPACES = 1
FREE_RESPONSES = 100
# Length of the free (Starter) trial.
FREE_TRIAL_DAYS = 7


class EntitlementsService:
    """Stateless — all state lives in the DB session."""

    async def get(self, user_id: int, db: AsyncSession) -> UserAllowedWorkspaces | None:
        """Read the user's allowance row (no side effects)."""
        return (
            await db.execute(
                select(UserAllowedWorkspaces).where(UserAllowedWorkspaces.user_id == user_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    def is_trial_active(row: UserAllowedWorkspaces | None) -> bool:
        """True if the row has a free trial that has not yet expired."""
        if not row or not row.free_trial_expires_at:
            return False
        expires = row.free_trial_expires_at
        now = now_ist()
        # SQLite (tests) drops tzinfo; we always store now_ist(), so compare on the
        # same wall clock when the stored value came back naive.
        if expires.tzinfo is None:
            now = now.replace(tzinfo=None)
        return expires > now

    async def start_free_trial(self, user_id: int, db: AsyncSession) -> UserAllowedWorkspaces:
        """Ensure the allowance row exists and start the 7-day free trial if not already.

        Idempotent: the trial clock is stamped only the first time, so re-selecting
        the free plan never extends it.
        """
        row = await self.get_or_create(user_id, db)
        if row.free_trial_expires_at is None:
            row.free_trial_expires_at = now_ist() + timedelta(days=FREE_TRIAL_DAYS)
            await db.flush()
            logger.info(
                "Started free trial for user %s → expires %s", user_id, row.free_trial_expires_at
            )
        return row

    async def get_or_create(self, user_id: int, db: AsyncSession) -> UserAllowedWorkspaces:
        """Return the user's allowance row, creating it at the free baseline if absent."""
        row = (
            await db.execute(
                select(UserAllowedWorkspaces).where(UserAllowedWorkspaces.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row is not None:
            return row

        row = UserAllowedWorkspaces(
            user_id=user_id,
            allowed_workspaces=FREE_WORKSPACES,
            allowed_responses=FREE_RESPONSES,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            # Created concurrently by another request — fetch the winner.
            await db.rollback()
            row = (
                await db.execute(
                    select(UserAllowedWorkspaces).where(UserAllowedWorkspaces.user_id == user_id)
                )
            ).scalar_one()
        return row

    async def grant_for_plan(self, user_id: int, plan: Plan, db: AsyncSession) -> None:
        """Add one plan's grant to the user's allowance (called on each paid charge).

        The plan's ``workspace_limit`` / ``survey_response_cap`` columns are the
        per-payment grant amounts (Builder 3/500, Pro 10/2000). Accumulates; never
        decrements.
        """
        # The free tier IS the baseline — it must never grant on top of itself.
        # (Starter has no Razorpay plan so never charges; this is a defensive guard.)
        if (plan.price_monthly or 0) <= 0:
            return

        row = await self.get_or_create(user_id, db)
        row.allowed_workspaces += plan.workspace_limit or 0
        row.allowed_responses += plan.survey_response_cap or 0
        await db.flush()
        logger.info(
            "Granted plan %s to user %s → workspaces=%s responses=%s",
            plan.code, user_id, row.allowed_workspaces, row.allowed_responses,
        )

    async def get_caps(self, user: User, db: AsyncSession) -> tuple[int | None, int | None]:
        """Return (workspace_cap, response_cap) for this user — None means unlimited.

        Unlimited for admins and when subscription enforcement is disabled (local
        dev); otherwise the user's stored allowance (created at the free baseline on
        first access).
        """
        # Imported lazily to avoid an import cycle (billing_service imports this).
        from app.services.billing_service import SUBSCRIPTION_ENFORCED

        if not SUBSCRIPTION_ENFORCED or user.has_role("admin"):
            return (None, None)
        row = await self.get_or_create(user.id, db)
        return (row.allowed_workspaces, row.allowed_responses)


entitlements_service = EntitlementsService()
