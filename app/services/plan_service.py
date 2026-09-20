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
    PlanResponse,
    UpdatePlanRequest,
)

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
        now = datetime.now(timezone.utc)
        plan = Plan(
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
            export_validation_reports=payload.export_validation_reports,
            stage_rerun=payload.stage_rerun,
            survey_analytics=payload.survey_analytics,
            popular=payload.popular,
            is_active=payload.is_active,
            created_at=now,
            updated_at=now,
        )
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