"""
app/api/v1/plans.py
────────────────────────────────────────────────────────────────────────────────
Admin Plans management endpoints:
  GET  /api/v1/plans        → list all plan details (including inactive)
  GET  /api/v1/plan/{id}    → get a specific plan
  POST /api/v1/plan         → create a new plan
  POST /api/v1/plan/with-razorpay → create a plan and auto-creates its Razorpay plans
  PUT  /api/v1/plan/{id}    → update a plan (also used to activate/deactivate)
"""
import logging

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_admin
from app.db.database import get_db
from app.db.models import User
from app.models.plan_models import (
    CreatePlanRequest,
    CreatePlanWithRazorpayRequest,
    PlanListResponse,
    PlanResponse,
    UpdatePlanRequest,
)
from app.services.plan_service import plan_service

router = APIRouter(tags=["Plans"])
logger = logging.getLogger(__name__)


@router.get(
    "/plans",
    response_model=PlanListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all plans",
    description="Returns every plan (active and inactive), ordered by tier.",
)
async def list_plans(
    db: AsyncSession = Depends(get_db),
) -> PlanListResponse:
    plans = await plan_service.list_plans(db)
    return PlanListResponse(plans=plans)


@router.get(
    "/plan/{plan_id}",
    response_model=PlanResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a single plan",
    description="Returns the details of one plan by id.",
)
async def get_plan(
    plan_id: int = Path(..., ge=1, description="ID of the plan to fetch"),
    db: AsyncSession = Depends(get_db),
) -> PlanResponse:
    return await plan_service.get_plan(plan_id, db)


@router.post(
    "/plan",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a plan",
    description="Saves a new plan into the plans table.",
)
async def create_plan(
    payload: CreatePlanRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PlanResponse:
    logger.info("Creating plan code=%r", payload.code)
    return await plan_service.create_plan(payload, db)


@router.post(
    "/plan/with-razorpay",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a plan and auto-create its Razorpay plans",
    description=(
        "Saves a new plan locally, creates the monthly and yearly Razorpay Plans "
        "using the supplied Razorpay credentials, persists the returned plan ids, "
        "and returns the complete plan. Free tiers (price 0) are not provisioned "
        "in Razorpay."
    ),
)
async def create_plan_with_razorpay(
    payload: CreatePlanWithRazorpayRequest,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PlanResponse:
    logger.info("Creating plan with Razorpay code=%r", payload.code)
    return await plan_service.create_plan_with_razorpay(payload, db)


@router.put(
    "/plan/{plan_id}",
    response_model=PlanResponse,
    status_code=status.HTTP_200_OK,
    summary="Update a plan",
    description="Updates the plan details. Set `is_active` to activate/deactivate the plan.",
)
async def update_plan(
    payload: UpdatePlanRequest,
    plan_id: int = Path(..., ge=1, description="ID of the plan to update"),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PlanResponse:
    logger.info("Updating plan id=%s", plan_id)
    return await plan_service.update_plan(plan_id, payload, db)