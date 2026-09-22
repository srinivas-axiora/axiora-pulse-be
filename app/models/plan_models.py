"""Request and response schemas for the admin Plans management endpoints.

Endpoints:
  GET  /api/v1/plans      → PlanListResponse (all plans, including inactive)
  GET  /api/v1/plan/{id}  → PlanResponse
  POST /api/v1/plan       → PlanResponse (admin)
  POST /api/v1/plan/with-razorpay → PlanResponse (admin; also creates Razorpay plans)
  PUT  /api/v1/plan/{id}  → PlanResponse (admin; also toggles is_active)
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CreatePlanRequest(BaseModel):
    """Payload for POST /api/v1/plan."""

    code: str = Field(..., min_length=1, max_length=50, description="Unique plan code, e.g. 'pro'")
    name: str = Field(..., min_length=1, max_length=100, description="Display name, e.g. 'Pro'")
    description: str | None = Field(default=None)
    razorpay_plan_id_monthly: str | None = Field(default=None, max_length=255)
    razorpay_plan_id_yearly: str | None = Field(default=None, max_length=255)
    price_monthly: int = Field(default=0, ge=0)
    price_yearly: int = Field(default=0, ge=0)
    old_price: int | None = Field(default=None, ge=0, description="Pre-discount monthly price shown as strikethrough")
    currency: str = Field(default="INR", max_length=3)
    tier: int = Field(default=0, ge=0, description="Gating rank: free=0, pro=1, ...")
    workspace_limit: int | None = Field(default=None, ge=0)
    survey_response_cap: int | None = Field(default=None, ge=0)
    regeneration_limit: int | None = Field(default=None, ge=0)
    export_enabled: bool = Field(default=True)
    stage_rerun: int | None = Field(default=None, ge=0, description="NULL = not allowed")
    survey_analytics: Literal["Basic", "Advanced"] = Field(default="Basic")
    storage_limit: int | None = Field(default=None, ge=0, description="Storage allowance in MB; NULL = not enforced")
    popular: bool = Field(default=False)
    is_active: bool = Field(default=True)


class CreatePlanWithRazorpayRequest(CreatePlanRequest):
    """Payload for POST /api/v1/plan/with-razorpay.

    All ``CreatePlanRequest`` fields plus optional Razorpay credentials used to
    auto-provision the monthly/yearly Razorpay plans. When omitted, the
    ``RAZORPAY_KEY_ID`` / ``RAZORPAY_KEY_SECRET`` environment values are used —
    the normal flow, so credentials never need to be sent in the request body.
    Credentials are consumed on the server, never persisted or returned.
    """

    razorpay_key_id: str | None = Field(
        default=None,
        max_length=255,
        description="Optional Razorpay Key ID. Defaults to RAZORPAY_KEY_ID env value.",
    )
    razorpay_key_secret: str | None = Field(
        default=None,
        max_length=255,
        description="Optional Razorpay Key Secret. Defaults to RAZORPAY_KEY_SECRET env value.",
    )


class UpdatePlanRequest(BaseModel):
    """Payload for PUT /api/v1/plan/{id} — every field optional; set is_active to deactivate/activate."""

    code: str | None = Field(default=None, min_length=1, max_length=50)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    razorpay_plan_id_monthly: str | None = Field(default=None, max_length=255)
    razorpay_plan_id_yearly: str | None = Field(default=None, max_length=255)
    price_monthly: int | None = Field(default=None, ge=0)
    price_yearly: int | None = Field(default=None, ge=0)
    old_price: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=3)
    tier: int | None = Field(default=None, ge=0)
    workspace_limit: int | None = Field(default=None, ge=0)
    survey_response_cap: int | None = Field(default=None, ge=0)
    regeneration_limit: int | None = Field(default=None, ge=0)
    export_enabled: bool | None = None
    stage_rerun: int | None = Field(default=None, ge=0)
    survey_analytics: Literal["Basic", "Advanced"] | None = None
    storage_limit: int | None = Field(default=None, ge=0)
    popular: bool | None = None
    is_active: bool | None = None


class PlanResponse(BaseModel):
    """Full plan record returned by the admin Plans endpoints."""

    id: int
    code: str
    name: str
    description: str | None
    razorpay_plan_id_monthly: str | None
    razorpay_plan_id_yearly: str | None
    price_monthly: int
    price_yearly: int
    old_price: int | None
    currency: str
    tier: int
    workspace_limit: int | None
    survey_response_cap: int | None
    regeneration_limit: int | None
    export_enabled: bool
    stage_rerun: int | None
    survey_analytics: str
    storage_limit: int | None
    popular: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PlanListResponse(BaseModel):
    plans: list[PlanResponse]