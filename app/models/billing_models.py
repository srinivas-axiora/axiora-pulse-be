"""
app/models/billing_models.py
────────────────────────────────────────────────────────────────────────────────
Pydantic request/response models for the Razorpay Subscriptions billing endpoints.

Endpoints covered:
  GET  /api/billing/plans          → PlansEnvelope
  POST /api/billing/subscribe      → SubscribeRequest        → SubscribeEnvelope
  POST /api/billing/verify         → VerifyPaymentRequest    → VerifyEnvelope
  GET  /api/billing/subscription   → SubscriptionEnvelope
  POST /api/billing/cancel         → CancelEnvelope

Response shape:
  The frontend `billingService` expects the standard `{ success, data }` envelope
  (see src/types/response.types.ts → ApiResponse<T>), so billing responses wrap
  their payload in that envelope. Field names in the data payload are camelCase to
  match the existing `PricingPlan` / axios contract on the client.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Data payloads (camelCase to match the SPA contract) ─────────────────────────

class PlanOut(BaseModel):
    """A sellable plan, shaped to the frontend `PricingPlan` type."""
    id: str = Field(..., description="Plan code used as a stable client id, e.g. 'professional'")
    name: str
    priceMonthly: int = Field(..., description="Monthly price in whole rupees")
    priceYearly: int = Field(..., description="Yearly price in whole rupees")
    features: List[str] = Field(default_factory=list)
    description: Optional[str] = Field(None, description="Short tagline shown under the price")
    popular: bool = Field(False, description="Whether to visually highlight this plan as recommended")


class SubscribeOut(BaseModel):
    """Handle the client needs to open Razorpay Checkout for a subscription."""
    subscriptionId: str = Field(..., description="Razorpay subscription id (sub_...)")
    keyId: str = Field(..., description="Razorpay public key id (rzp_test_/rzp_live_...)")
    shortUrl: Optional[str] = Field(None, description="Razorpay hosted checkout URL (fallback)")


class SubscriptionOut(BaseModel):
    """Current subscription state for the authenticated user."""
    status: str = Field(..., description="none | created | authenticated | active | pending | halted | cancelled | completed | expired")
    planCode: Optional[str] = None
    planName: Optional[str] = None
    billingPeriod: Optional[str] = None
    currentEnd: Optional[datetime] = None
    cancelAtPeriodEnd: bool = False


class AllowanceOut(BaseModel):
    """The user's current accumulating allowance (user_allowed_workspaces)."""
    planCode: str = Field(..., description="Plan code selected, e.g. 'starter'")
    allowedWorkspaces: int = Field(..., description="Total workspaces the user may create")
    allowedResponses: int = Field(..., description="Total survey responses allowed across the account")


class AccountStatusOut(BaseModel):
    """The user's current plan + access status, for the profile 'My Plan' / settings view.

    Only fields with data are serialized (the route uses ``response_model_exclude_none``),
    so e.g. a user without a paid subscription gets no ``priceMonthly`` key. The usage
    "used" counters cover what is actually tracked today:
      - workspaces & survey responses (DB counts),
      - attachments storage (summed file sizes, in MB).
    Regeneration / stage-rerun / report usage is not recorded yet, so only the plan
    limits are returned for those.
    """
    plan: Optional[str] = Field(None, description="Current plan code (starter|builder|pro); null if none")
    planName: Optional[str] = Field(None, description="Human plan name, e.g. 'Builder'")
    status: str = Field(..., description="active (paid) | trial | expired | none")
    trialEndsAt: Optional[datetime] = Field(None, description="Free-trial end (only when status='trial')")
    currentEnd: Optional[datetime] = Field(None, description="Billing period end of the active subscription")
    billingPeriod: Optional[str] = Field(None, description="monthly | yearly")
    cancelAtPeriodEnd: bool = Field(False, description="True when the subscription is set to cancel at period end")
    priceMonthly: Optional[int] = Field(None, description="Monthly price of the current plan in whole rupees")
    priceYearly: Optional[int] = Field(None, description="Yearly price of the current plan in whole rupees")
    currency: Optional[str] = Field(None, description="Plan currency, e.g. 'INR'")
    features: Optional[List[str]] = Field(None, description="Feature list of the current plan (e.g. '3 Workspaces')")
    workspaceLimit: Optional[int] = Field(None, description="Workspaces included in the plan")
    responseCap: Optional[int] = Field(None, description="Survey responses included in the plan")
    storageLimitMB: Optional[int] = Field(None, description="Storage allowance in MB")
    regenerationLimit: Optional[int] = Field(None, description="Regeneration cap; omitted when not included")
    stageRerun: Optional[int] = Field(None, description="Stage re-run cap; omitted when not included")
    exportEnabled: Optional[bool] = Field(None, description="Whether the plan includes report export")
    surveyAnalytics: Optional[str] = Field(None, description="Basic | Advanced analytics tier")
    allowedWorkspaces: int = Field(0, description="Total workspaces the user may create")
    usedWorkspaces: int = Field(0, description="Workspaces currently used (active + archived)")
    allowedResponses: int = Field(0, description="Total survey responses allowed across the account")
    usedResponses: int = Field(0, description="Survey responses collected across the user's surveys")
    storageUsedMB: int = Field(0, description="Attachment storage used, rounded up to MB")


# ── Requests ────────────────────────────────────────────────────────────────────

class SubscribeRequest(BaseModel):
    """Payload for POST /api/billing/subscribe."""
    planId: str = Field(..., description="Plan code to subscribe to, e.g. 'pro'")
    billingPeriod: str = Field("monthly", description="monthly | yearly")


class VerifyPaymentRequest(BaseModel):
    """
    Payload for POST /api/billing/verify — the fields Razorpay Checkout returns in
    its success handler for a subscription payment. Verified as defense-in-depth;
    the webhook remains the source of truth for entitlement.
    """
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str


# ── Envelopes ───────────────────────────────────────────────────────────────────

class PlansEnvelope(BaseModel):
    success: bool = True
    data: List[PlanOut]
    message: Optional[str] = None


class SubscribeEnvelope(BaseModel):
    success: bool = True
    data: SubscribeOut
    message: Optional[str] = None


class SubscriptionEnvelope(BaseModel):
    success: bool = True
    data: SubscriptionOut
    message: Optional[str] = None


class VerifyEnvelope(BaseModel):
    success: bool = True
    data: SubscriptionOut
    message: Optional[str] = None


class CancelEnvelope(BaseModel):
    success: bool = True
    data: SubscriptionOut
    message: Optional[str] = None


class AllowanceEnvelope(BaseModel):
    success: bool = True
    data: AllowanceOut
    message: Optional[str] = None


class AccountStatusEnvelope(BaseModel):
    success: bool = True
    data: AccountStatusOut
    message: Optional[str] = None
