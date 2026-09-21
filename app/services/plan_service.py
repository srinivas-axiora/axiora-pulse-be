"""
app/services/plan_service.py
────────────────────────────────────────────────────────────────────────────────
Admin CRUD for the local subscription plan catalog. Used by the admin Plans
management endpoints (GET /plans, GET /plan/{id}, POST /plan, PUT /plan/{id}).
"""
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Plan
from app.models.plan_models import (
    CreatePlanRequest,
    CreatePlanWithRazorpayRequest,
    PlanResponse,
    UpdatePlanRequest,
)
from app.services.razorpay_service import razorpay_service

logger = logging.getLogger(__name__)


class PlanService:
    async def list_plans(self, db: AsyncSession) -> list[PlanResponse]:
        """Return every plan (including inactive) ordered by tier, then id."""
        result = await db.execute(select(Plan).order_by(Plan.tier, Plan.id))
        plans = result.scalars().all()
        logger.info("Fetched %s plans", len(plans))
        return [PlanResponse.model_validate(p) for p in plans]

    async def get_plan(self, plan_id: int, db: AsyncSession) -> PlanResponse:
        """Return a single plan by id, or 404."""
        plan = await db.get(Plan, plan_id)
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
        return PlanResponse.model_validate(plan)

    async def create_plan(
        self, payload: CreatePlanRequest, db: AsyncSession
    ) -> PlanResponse:
        """Persist a new plan."""
        plan = self._build_plan(payload)
        db.add(plan)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"A plan with code '{payload.code.strip()}' already exists.",
            )
        await db.refresh(plan)
        logger.info("Plan created: id=%s code=%r", plan.id, plan.code)
        return PlanResponse.model_validate(plan)

    async def create_plan_with_razorpay(
        self,
        payload: CreatePlanWithRazorpayRequest,
        db: AsyncSession,
    ) -> PlanResponse:
        """Persist a new plan and auto-create its monthly/yearly Razorpay plans.

        The local row and the Razorpay plans are treated as one unit of work:
        if either Razorpay call fails the whole transaction is rolled back so
        no partial plan is left behind. Free tiers (price 0) get no Razorpay
        plan, mirroring ``scratch/seed_razorpay_plans.py``.
        """
        plan = self._build_plan(payload)
        db.add(plan)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"A plan with code '{payload.code.strip()}' already exists.",
            )

        try:
            if plan.price_monthly > 0:
                created = razorpay_service.create_plan(
                    name=f"{plan.name} (Monthly)",
                    amount_paise=plan.price_monthly * 100,
                    currency=plan.currency or "INR",
                    description=plan.description or plan.name,
                    period="monthly",
                    interval=1,
                    notes={"plan_code": plan.code, "billing_period": "monthly"},
                    key_id=payload.razorpay_key_id,
                    key_secret=payload.razorpay_key_secret,
                )
                plan.razorpay_plan_id_monthly = created["id"]

            if plan.price_yearly > 0:
                created = razorpay_service.create_plan(
                    name=f"{plan.name} (Yearly)",
                    amount_paise=plan.price_yearly * 100,
                    currency=plan.currency or "INR",
                    description=plan.description or plan.name,
                    period="yearly",
                    interval=1,
                    notes={"plan_code": plan.code, "billing_period": "yearly"},
                    key_id=payload.razorpay_key_id,
                    key_secret=payload.razorpay_key_secret,
                )
                plan.razorpay_plan_id_yearly = created["id"]

            await db.flush()
        except Exception as exc:
            plan_code = plan.code
            await db.rollback()
            logger.error(
                "Razorpay plan provisioning failed for code=%r: %s",
                plan_code, exc,
            )
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"Failed to create Razorpay plans for '{plan_code}': {exc}",
            )

        await db.refresh(plan)
        logger.info(
            "Plan created with Razorpay ids: id=%s code=%r monthly=%s yearly=%s",
            plan.id, plan.code, plan.razorpay_plan_id_monthly, plan.razorpay_plan_id_yearly,
        )
        return PlanResponse.model_validate(plan)

    @staticmethod
    def _build_plan(payload: CreatePlanRequest) -> Plan:
        """Construct (unpersisted) Plan from any plan request payload."""
        now = datetime.now(timezone.utc)
        return Plan(
            code=payload.code.strip(),
            name=payload.name.strip(),
            description=payload.description.strip() if payload.description else None,
            razorpay_plan_id_monthly=payload.razorpay_plan_id_monthly,
            razorpay_plan_id_yearly=payload.razorpay_plan_id_yearly,
            price_monthly=payload.price_monthly,
            price_yearly=payload.price_yearly,
            currency=payload.currency,
            features=list(payload.features),
            tier=payload.tier,
            workspace_limit=payload.workspace_limit,
            survey_response_cap=payload.survey_response_cap,
            regeneration_limit=payload.regeneration_limit,
            export_enabled=payload.export_enabled,
            stage_rerun=payload.stage_rerun,
            survey_analytics=payload.survey_analytics,
            storage_limit=payload.storage_limit,
            popular=payload.popular,
            is_active=payload.is_active,
            created_at=now,
            updated_at=now,
        )

    async def update_plan(
        self,
        plan_id: int,
        payload: UpdatePlanRequest,
        db: AsyncSession,
    ) -> PlanResponse:
        """Partially update a plan. Use ``is_active`` to activate/deactivate."""
        plan = await db.get(Plan, plan_id)
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")

        data = payload.model_dump(exclude_unset=True)
        for field, value in data.items():
            setattr(plan, field, value)

        plan.updated_at = datetime.now(timezone.utc)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Another plan already uses that code.",
            )
        await db.refresh(plan)
        logger.info("Plan updated: id=%s code=%r is_active=%s", plan.id, plan.code, plan.is_active)
        return PlanResponse.model_validate(plan)


plan_service = PlanService()