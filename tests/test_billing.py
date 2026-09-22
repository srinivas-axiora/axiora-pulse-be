"""
Tests for the billing domain:

  - app/services/billing_service.py  (subscription lifecycle + webhook handling)
  - app/api/billing.py               (HTTP endpoints)
  - app/services/razorpay_service.py (Razorpay SDK wrapper)
"""
import json
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.models import Payment, Plan, Role, Subscription, User, WebhookEvent
from app.services.billing_service import (
    BillingService,
    PlanLimits,
    SUBSCRIPTION_ENFORCED,
    _UNLIMITED,
    _epoch_to_dt,
    billing_service,
)
from app.services.razorpay_service import RazorpayService, razorpay_service


# ── Helpers ────────────────────────────────────────────────────────────────────

def test_epoch_to_dt_branches():
    from datetime import datetime, timezone
    assert _epoch_to_dt(None) is None
    assert _epoch_to_dt("") is None
    assert _epoch_to_dt(0) is None
    assert _epoch_to_dt("100") == datetime.fromtimestamp(100, tz=timezone.utc)
    assert _epoch_to_dt("not-a-number") is None


async def _create_plan(db: AsyncSession, code="pro", rzp_monthly="rzp_plan_m", rzp_yearly="rzp_plan_y", is_active=True, tier=1) -> Plan:
    plan = Plan(
        code=code,
        name=code.title(),
        description="A plan",
        razorpay_plan_id_monthly=rzp_monthly,
        razorpay_plan_id_yearly=rzp_yearly,
        price_monthly=499,
        price_yearly=4990,
        tier=tier,
        popular=True,
        is_active=is_active,
    )
    db.add(plan)
    await db.flush()
    return plan


async def _create_user(db: AsyncSession, username="bill@axiorapulse.com", role_name="member") -> User:
    role = (await db.execute(select(Role).where(Role.name == role_name))).scalar_one_or_none()
    user = User(username=username, password="x", register_mfa=True, role=role)
    db.add(user)
    await db.flush()
    return user


async def _create_subscription(db: AsyncSession, user: User, plan: Plan, status="active", rzp_sub_id="sub_123", created_at=None) -> Subscription:
    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        razorpay_subscription_id=rzp_sub_id,
        razorpay_plan_id=plan.razorpay_plan_id_monthly,
        billing_period="monthly",
        status=status,
        created_at=created_at,
    )
    db.add(sub)
    await db.flush()
    return sub


def authenticate_as(user: User) -> None:
    from main import app
    role_name = user._primary_role

    def _has_role(name: str) -> bool:
        return role_name == name

    current_user = type("U", (), {"id": user.id, "username": user.username, "role": role_name, "has_role": staticmethod(_has_role)})()
    app.dependency_overrides[get_current_user] = lambda: current_user


# ── BillingService: plans / create ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_plans_returns_active_plans_ordered(db_session: AsyncSession):
    await _create_plan(db_session, "pro", tier=2)
    await _create_plan(db_session, "free", tier=0)
    await _create_plan(db_session, "hidden", is_active=False)
    await db_session.commit()

    plans = await billing_service.list_plans(db_session)

    assert [p.id for p in plans] == ["free", "pro"]
    assert plans[0].priceMonthly == 499
    assert plans[0].popular is True


@pytest.mark.asyncio
async def test_create_subscription_happy_path(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    await db_session.commit()

    with patch.object(
        razorpay_service,
        "create_subscription",
        return_value={"id": "sub_new", "status": "created", "short_url": "https://rzp"},
    ), patch.object(type(razorpay_service), "key_id", new_callable=PropertyMock, return_value="rzp_test_key"):
        out = await billing_service.create_subscription("pro", "monthly", user, db_session)

    assert out.subscriptionId == "sub_new"
    assert out.keyId == "rzp_test_key"
    assert out.shortUrl == "https://rzp"

    sub = (
        await db_session.execute(select(Subscription).where(Subscription.user_id == user.id))
    ).scalar_one()
    assert sub.status == "created"
    assert sub.billing_period == "monthly"
    assert sub.plan_id == plan.id


@pytest.mark.asyncio
async def test_create_subscription_invalid_period(db_session: AsyncSession):
    user = await _create_user(db_session)
    await _create_plan(db_session)
    await db_session.commit()
    with pytest.raises(Exception) as exc:
        await billing_service.create_subscription("pro", "weekly", user, db_session)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_create_subscription_plan_not_found(db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()
    with pytest.raises(Exception) as exc:
        await billing_service.create_subscription("nope", "monthly", user, db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_subscription_missing_rzp_plan_id(db_session: AsyncSession):
    user = await _create_user(db_session)
    await _create_plan(db_session, rzp_monthly=None)
    await db_session.commit()
    with pytest.raises(Exception) as exc:
        await billing_service.create_subscription("pro", "monthly", user, db_session)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_create_subscription_blocks_duplicate_active(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_a")
    await db_session.commit()

    with patch.object(razorpay_service, "create_subscription") as m:
        with pytest.raises(Exception) as exc:
            await billing_service.create_subscription("pro", "monthly", user, db_session)
    assert exc.value.status_code == 409
    m.assert_not_called()


@pytest.mark.asyncio
async def test_create_subscription_razorpay_unconfigured(db_session: AsyncSession):
    user = await _create_user(db_session)
    await _create_plan(db_session)
    await db_session.commit()
    with patch.object(razorpay_service, "create_subscription", side_effect=RuntimeError("no keys")):
        with pytest.raises(Exception) as exc:
            await billing_service.create_subscription("pro", "monthly", user, db_session)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_create_subscription_razorpay_generic_error(db_session: AsyncSession):
    user = await _create_user(db_session)
    await _create_plan(db_session)
    await db_session.commit()
    with patch.object(razorpay_service, "create_subscription", side_effect=ValueError("boom")):
        with pytest.raises(Exception) as exc:
            await billing_service.create_subscription("pro", "monthly", user, db_session)
    assert exc.value.status_code == 502


# ── BillingService: verify / get_current / cancel ──────────────────────────────

@pytest.mark.asyncio
async def test_verify_payment_upgrades_created_to_authenticated(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    sub = await _create_subscription(db_session, user, plan, status="created", rzp_sub_id="sub_v")
    await db_session.commit()

    with patch.object(
        razorpay_service, "verify_subscription_payment_signature", return_value=True
    ):
        out = await billing_service.verify_payment("pay_1", "sub_v", "sig", user, db_session)

    assert out.status == "authenticated"
    assert out.planCode == "pro"
    assert out.billingPeriod == "monthly"
    await db_session.refresh(sub)
    assert sub.status == "authenticated"


@pytest.mark.asyncio
async def test_verify_payment_bad_signature(db_session: AsyncSession):
    user = await _create_user(db_session)
    await _create_plan(db_session)
    await db_session.commit()
    with patch.object(razorpay_service, "verify_subscription_payment_signature", return_value=False):
        with pytest.raises(Exception) as exc:
            await billing_service.verify_payment("pay_1", "sub_v", "sig", user, db_session)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_verify_payment_subscription_not_found(db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()
    with patch.object(razorpay_service, "verify_subscription_payment_signature", return_value=True):
        with pytest.raises(Exception) as exc:
            await billing_service.verify_payment("pay_1", "sub_missing", "sig", user, db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_current_returns_none_when_no_subscription(db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()
    out = await billing_service.get_current(user, db_session)
    assert out.status == "none"
    assert out.planCode is None


@pytest.mark.asyncio
async def test_get_current_returns_latest_subscription(db_session: AsyncSession):
    from datetime import datetime, timedelta, timezone
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    now = datetime.now(tz=timezone.utc)
    await _create_subscription(db_session, user, plan, status="created", rzp_sub_id="sub_old", created_at=now - timedelta(days=1))
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_new", created_at=now)
    await db_session.commit()
    out = await billing_service.get_current(user, db_session)
    assert out.status == "active"
    assert out.planCode == "pro"


@pytest.mark.asyncio
async def test_cancel_no_active_subscription(db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()
    with pytest.raises(Exception) as exc:
        await billing_service.cancel(user, db_session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_cancel_happy_path(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    sub = await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_c")
    await db_session.commit()

    with patch.object(razorpay_service, "cancel_subscription", return_value={}):
        out = await billing_service.cancel(user, db_session)

    assert out.cancelAtPeriodEnd is True
    await db_session.refresh(sub)
    assert sub.cancel_at_period_end is True


@pytest.mark.asyncio
async def test_cancel_razorpay_error(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_c")
    await db_session.commit()
    with patch.object(razorpay_service, "cancel_subscription", side_effect=RuntimeError("x")):
        with pytest.raises(Exception) as exc:
            await billing_service.cancel(user, db_session)
    assert exc.value.status_code == 502


# ── BillingService: entitlement ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_has_active_entitlement_true_with_active(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="active")
    await db_session.commit()
    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", True):
        assert await billing_service.has_active_entitlement(user, db_session) is True


@pytest.mark.asyncio
async def test_has_active_entitlement_false_without(db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()
    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", True):
        assert await billing_service.has_active_entitlement(user, db_session) is False


@pytest.mark.asyncio
async def test_has_active_entitlement_bypasses_when_flag_off(db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()
    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", False):
        assert await billing_service.has_active_entitlement(user, db_session) is True


# ── BillingService: plan limits / export gate ──────────────────────────────────

@pytest.mark.asyncio
async def test_get_plan_limits_reads_active_plan(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session, code="builder", tier=2)
    plan.workspace_limit = 3
    plan.survey_response_cap = 500
    plan.regeneration_limit = 5
    plan.export_enabled = True
    await _create_subscription(db_session, user, plan, status="active")
    await db_session.commit()

    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", True):
        limits = await billing_service.get_plan_limits(user, db_session)

    assert isinstance(limits, PlanLimits)
    assert limits.workspace_limit == 3
    assert limits.survey_response_cap == 500
    assert limits.regeneration_limit == 5
    assert limits.export_enabled is True


@pytest.mark.asyncio
async def test_get_plan_limits_admin_is_unlimited(db_session: AsyncSession):
    admin = await _create_user(db_session, username="admin@axiorapulse.com", role_name="admin")
    plan = await _create_plan(db_session, code="starter", tier=1)
    plan.export_enabled = False  # restrictive plan exists, but admin bypasses it
    await db_session.commit()

    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", True):
        limits = await billing_service.get_plan_limits(admin, db_session)

    assert limits == _UNLIMITED
    assert limits.export_enabled is True


@pytest.mark.asyncio
async def test_get_plan_limits_bypasses_when_flag_off(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session, code="starter", tier=1)
    plan.export_enabled = False
    await db_session.commit()

    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", False):
        limits = await billing_service.get_plan_limits(user, db_session)

    assert limits.export_enabled is True


@pytest.mark.asyncio
async def test_get_plan_limits_falls_back_to_lowest_tier_without_subscription(db_session: AsyncSession):
    user = await _create_user(db_session)
    starter = await _create_plan(db_session, code="starter", tier=1)
    starter.workspace_limit = 1
    starter.export_enabled = False
    builder = await _create_plan(db_session, code="builder", tier=2)
    builder.export_enabled = True
    await db_session.commit()

    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", True):
        limits = await billing_service.get_plan_limits(user, db_session)

    # No subscription → lowest active tier (starter) applies.
    assert limits.workspace_limit == 1
    assert limits.export_enabled is False


@pytest.mark.asyncio
async def test_assert_export_allowed_blocks_starter(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session, code="starter", tier=1)
    plan.export_enabled = False
    await _create_subscription(db_session, user, plan, status="active")
    await db_session.commit()

    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", True):
        with pytest.raises(Exception) as exc:
            await billing_service.assert_export_allowed(user, db_session)
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_assert_export_allowed_passes_for_builder(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session, code="builder", tier=2)
    plan.export_enabled = True
    await _create_subscription(db_session, user, plan, status="active")
    await db_session.commit()

    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", True):
        # Should not raise.
        await billing_service.assert_export_allowed(user, db_session)


@pytest.mark.asyncio
async def test_api_export_survey_blocked_for_starter(client: AsyncClient, db_session: AsyncSession):
    """The require_export_enabled dependency returns 402 before the route body runs."""
    user = await _create_user(db_session)
    plan = await _create_plan(db_session, code="starter", tier=1)
    plan.export_enabled = False
    await _create_subscription(db_session, user, plan, status="active")
    await db_session.commit()
    authenticate_as(user)

    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", True):
        resp = await client.get("/api/v1/surveys/1/export?format=json")

    assert resp.status_code == 402


# ── BillingService: webhook handling ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_webhook_processes_subscription_and_payment(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="created", rzp_sub_id="sub_w")
    await db_session.commit()

    payload = {
        "payload": {
            "subscription": {"entity": {"id": "sub_w", "status": "active", "current_start": "1700000000", "current_end": "1702592000"}},
            "payment": {"entity": {"id": "pay_w", "subscription_id": "sub_w", "invoice_id": "inv_1", "amount": 49900, "currency": "INR", "status": "captured", "method": "card"}},
        }
    }
    await billing_service.handle_webhook("evt_1", "subscription.charged", payload, db_session)

    event = (await db_session.execute(select(WebhookEvent).where(WebhookEvent.event_id == "evt_1"))).scalar_one()
    assert event.processed is True

    sub = (await db_session.execute(select(Subscription).where(Subscription.razorpay_subscription_id == "sub_w"))).scalar_one()
    assert sub.status == "active"

    pay = (await db_session.execute(select(Payment).where(Payment.razorpay_payment_id == "pay_w"))).scalar_one()
    assert pay.user_id == user.id
    assert pay.amount == 49900
    assert pay.status == "captured"

    assert user.has_role("member")


@pytest.mark.asyncio
async def test_handle_webhook_ignores_duplicate_event(db_session: AsyncSession):
    await db_session.execute(WebhookEvent.__table__.delete())
    existing = WebhookEvent(event_id="dup_1", event_type="x", payload={})
    db_session.add(existing)
    await db_session.flush()
    # Force a flush then rollback so a second insert of "dup_1" raises IntegrityError
    await db_session.commit()

    with patch.object(billing_service, "_apply_subscription_update") as apply_mock, patch.object(
        billing_service, "_record_payment"
    ) as rec_mock:
        await billing_service.handle_webhook("dup_1", "x", {"payload": {}}, db_session)

    apply_mock.assert_not_called()
    rec_mock.assert_not_called()


@pytest.mark.asyncio
async def test_apply_subscription_update_skips_unknown(db_session: AsyncSession):
    payload = {"payload": {"subscription": {"entity": {"id": "ghost", "status": "active"}}}}
    await billing_service.handle_webhook("evt_unk", "x", payload, db_session)
    event = (await db_session.execute(select(WebhookEvent).where(WebhookEvent.event_id == "evt_unk"))).scalar_one()
    assert event.processed is True


@pytest.mark.asyncio
async def test_record_payment_skips_duplicate_and_missing_id(db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_r")
    await db_session.commit()

    pay_ent = {"id": "pay_r", "subscription_id": "sub_r", "amount": 100, "status": "captured", "currency": "INR"}
    await billing_service._record_payment(pay_ent, db_session)
    await billing_service._record_payment(pay_ent, db_session)
    await billing_service._record_payment({"status": "captured"}, db_session)

    pay = (await db_session.execute(select(Payment).where(Payment.razorpay_payment_id == "pay_r"))).scalar_one()
    assert pay.user_id == user.id
    assert pay.subscription_id is not None


@pytest.mark.asyncio
async def test_apply_subscription_update_skips_missing_id(db_session: AsyncSession):
    await billing_service._apply_subscription_update({"status": "active"}, db_session)


# ── BillingService: role sync on entitlement (churn downgrade) ──────────────────

@pytest.mark.asyncio
async def test_webhook_upgrades_viewer_to_member_on_active(db_session: AsyncSession):
    user = await _create_user(db_session, username="up@axiorapulse.com", role_name="viewer")
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="created", rzp_sub_id="sub_up")
    await db_session.commit()

    await billing_service._apply_subscription_update({"id": "sub_up", "status": "active"}, db_session)

    await db_session.refresh(user)
    assert user.has_role("member")


@pytest.mark.asyncio
async def test_webhook_downgrades_member_to_viewer_on_churn(db_session: AsyncSession):
    user = await _create_user(db_session, username="churn@axiorapulse.com", role_name="member")
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_churn")
    await db_session.commit()

    # Subscription is cancelled → member should be revoked to viewer.
    await billing_service._apply_subscription_update({"id": "sub_churn", "status": "cancelled"}, db_session)

    await db_session.refresh(user)
    assert user.has_role("viewer")


@pytest.mark.asyncio
async def test_webhook_keeps_member_when_another_subscription_still_active(db_session: AsyncSession):
    user = await _create_user(db_session, username="multi@axiorapulse.com", role_name="member")
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_x")
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_y")
    await db_session.commit()

    # sub_x churns, but sub_y is still active → keep member.
    await billing_service._apply_subscription_update({"id": "sub_x", "status": "halted"}, db_session)

    await db_session.refresh(user)
    assert user.has_role("member")


@pytest.mark.asyncio
async def test_webhook_never_downgrades_admin(db_session: AsyncSession):
    admin = await _create_user(db_session, username="adminchurn@axiorapulse.com", role_name="admin")
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, admin, plan, status="active", rzp_sub_id="sub_admin")
    await db_session.commit()

    await billing_service._apply_subscription_update({"id": "sub_admin", "status": "cancelled"}, db_session)

    await db_session.refresh(admin)
    assert admin.has_role("admin")


# ── RazorpayService ────────────────────────────────────────────────────────────

def test_razorpay_key_id_and_total_count():
    assert razorpay_service.key_id == ""
    assert razorpay_service.total_count > 0


def test_razorpay_get_client_raises_when_not_configured():
    svc = RazorpayService()
    with patch("app.services.razorpay_service._KEY_ID", ""), patch("app.services.razorpay_service._KEY_SECRET", ""):
        with pytest.raises(RuntimeError):
            svc._get_client()


def test_razorpay_get_client_builds_client():
    svc = RazorpayService()
    fake_client = Mock()
    fake_client.subscription.create.return_value = {"id": "sub_1"}
    with patch("app.services.razorpay_service._KEY_ID", "rzp_test_id"), patch(
        "app.services.razorpay_service._KEY_SECRET", "secret"
    ), patch("razorpay.Client", return_value=fake_client) as client_cls:
        client = svc._get_client()
        assert client is fake_client
        client_cls.assert_called_once()
        fake_client.set_app_details.assert_called_once()


def test_razorpay_create_subscription():
    svc = RazorpayService()
    fake_client = Mock()
    fake_client.subscription.create.return_value = {"id": "sub_1"}
    svc._client = fake_client
    out = svc.create_subscription("rzp_plan", total_count=5, notes={"a": "b"}, customer_notify=False)
    assert out == {"id": "sub_1"}
    args, kwargs = fake_client.subscription.create.call_args
    assert args[0]["plan_id"] == "rzp_plan"
    assert args[0]["total_count"] == 5
    assert args[0]["customer_notify"] == 0


def test_razorpay_fetch_and_cancel_subscription():
    svc = RazorpayService()
    fake_client = Mock()
    fake_client.subscription.fetch.return_value = {"id": "sub_1"}
    fake_client.subscription.cancel.return_value = {"id": "sub_1"}
    svc._client = fake_client
    assert svc.fetch_subscription("sub_1") == {"id": "sub_1"}
    assert svc.cancel_subscription("sub_1", cancel_at_cycle_end=False) == {"id": "sub_1"}
    assert fake_client.subscription.cancel.call_args[0][1]["cancel_at_cycle_end"] == 0


def test_razorpay_verify_webhook_signature_no_secret_raises():
    svc = RazorpayService()
    svc._client = Mock()
    with patch("app.services.razorpay_service._WEBHOOK_SECRET", ""):
        with pytest.raises(RuntimeError):
            svc.verify_webhook_signature(b"body", "sig")


def test_razorpay_verify_webhook_signature_valid_and_invalid():
    svc = RazorpayService()
    fake_client = Mock()
    svc._client = fake_client
    with patch("app.services.razorpay_service._WEBHOOK_SECRET", "secret"):
        assert svc.verify_webhook_signature(b'{"a":1}', "sig") is True
        fake_client.utility.verify_webhook_signature.side_effect = Exception("bad")
        assert svc.verify_webhook_signature(b'{"a":1}', "sig") is False


def test_razorpay_verify_subscription_payment_signature_valid_and_invalid():
    svc = RazorpayService()
    fake_client = Mock()
    svc._client = fake_client
    assert svc.verify_subscription_payment_signature("p", "s", "sig") is True
    fake_client.utility.verify_subscription_payment_signature.side_effect = Exception("bad")
    assert svc.verify_subscription_payment_signature("p", "s", "sig") is False


# ── API endpoints ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_list_plans(client: AsyncClient, db_session: AsyncSession):
    await _create_plan(db_session)
    await db_session.commit()
    resp = await client.get("/api/billing/plans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]) == 1
    assert body["data"][0]["id"] == "pro"


@pytest.mark.asyncio
async def test_api_subscribe_requires_auth(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post("/api/billing/subscribe", json={"planId": "pro", "billingPeriod": "monthly"})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_api_subscribe_happy_path(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    await _create_plan(db_session)
    await db_session.commit()
    authenticate_as(user)
    with patch.object(
        razorpay_service,
        "create_subscription",
        return_value={"id": "sub_api", "status": "created", "short_url": "https://rzp"},
    ), patch.object(type(razorpay_service), "key_id", new_callable=PropertyMock, return_value="rzp_test_key"):
        resp = await client.post("/api/billing/subscribe", json={"planId": "pro", "billingPeriod": "monthly"})
    assert resp.status_code == 201
    assert resp.json()["data"]["subscriptionId"] == "sub_api"


@pytest.mark.asyncio
async def test_api_subscription_status(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_g")
    await db_session.commit()
    authenticate_as(user)
    resp = await client.get("/api/billing/subscription")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "active"


@pytest.mark.asyncio
async def test_api_subscription_status_none(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()
    authenticate_as(user)
    resp = await client.get("/api/billing/subscription")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "none"


@pytest.mark.asyncio
async def test_api_verify(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="created", rzp_sub_id="sub_verify_api")
    await db_session.commit()
    authenticate_as(user)
    with patch.object(razorpay_service, "verify_subscription_payment_signature", return_value=True):
        resp = await client.post(
            "/api/billing/verify",
            json={"razorpay_payment_id": "pay_a", "razorpay_subscription_id": "sub_verify_api", "razorpay_signature": "sig"},
        )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "authenticated"


@pytest.mark.asyncio
async def test_api_verify_bad_signature(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()
    authenticate_as(user)
    with patch.object(razorpay_service, "verify_subscription_payment_signature", return_value=False):
        resp = await client.post(
            "/api/billing/verify",
            json={"razorpay_payment_id": "pay_a", "razorpay_subscription_id": "sub_x", "razorpay_signature": "sig"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_cancel(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session)
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_cancel_api")
    await db_session.commit()
    authenticate_as(user)
    with patch.object(razorpay_service, "cancel_subscription", return_value={}):
        resp = await client.post("/api/billing/cancel")
    assert resp.status_code == 200
    assert resp.json()["data"]["cancelAtPeriodEnd"] is True


@pytest.mark.asyncio
async def test_api_cancel_no_subscription(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()
    authenticate_as(user)
    resp = await client.post("/api/billing/cancel")
    assert resp.status_code == 404


# ── API select free plan (issue #190) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_select_free_plan_creates_baseline(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    db_session.add(Plan(code="starter", name="Starter", price_monthly=0, price_yearly=0,
                        tier=0, is_active=True))
    await db_session.commit()
    authenticate_as(user)
    resp = await client.post("/api/billing/subscription/starter")
    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["planCode"] == "starter"
    assert data["allowedWorkspaces"] == 1
    assert data["allowedResponses"] == 100


@pytest.mark.asyncio
async def test_api_select_free_plan_is_idempotent(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    db_session.add(Plan(code="starter", name="Starter", price_monthly=0, price_yearly=0,
                        tier=0, is_active=True))
    await db_session.commit()
    authenticate_as(user)
    await client.post("/api/billing/subscription/starter")
    resp = await client.post("/api/billing/subscription/starter")
    assert resp.status_code == 201
    assert resp.json()["data"]["allowedWorkspaces"] == 1  # not doubled


@pytest.mark.asyncio
async def test_api_select_free_plan_rejects_paid(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    db_session.add(Plan(code="builder", name="Builder", price_monthly=299, price_yearly=0,
                        tier=2, is_active=True))
    await db_session.commit()
    authenticate_as(user)
    resp = await client.post("/api/billing/subscription/builder")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_select_free_plan_unknown_404(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    await db_session.commit()
    authenticate_as(user)
    resp = await client.post("/api/billing/subscription/nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_select_free_plan_requires_auth(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post("/api/billing/subscription/starter")
    assert resp.status_code in (401, 403)  # rejected unauthenticated


# ── Free trial + account status (issue #194 follow-up) ──────────────────────────

@pytest.mark.asyncio
async def test_select_free_plan_starts_trial(db_session: AsyncSession):
    from app.services.entitlements_service import entitlements_service
    user = await _create_user(db_session, role_name="viewer")
    db_session.add(Plan(code="starter", name="Starter", price_monthly=0, price_yearly=0,
                        tier=0, is_active=True))
    await db_session.commit()
    await billing_service.select_free_plan("starter", user, db_session)
    row = await entitlements_service.get(user.id, db_session)
    assert row.free_trial_expires_at is not None


@pytest.mark.asyncio
async def test_has_active_entitlement_true_during_free_trial(db_session: AsyncSession):
    from app.services.entitlements_service import entitlements_service
    user = await _create_user(db_session, role_name="viewer")
    await entitlements_service.start_free_trial(user.id, db_session)  # trial now + 7d
    await db_session.commit()
    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", True):
        assert await billing_service.has_active_entitlement(user, db_session) is True


@pytest.mark.asyncio
async def test_has_active_entitlement_false_after_trial_expired(db_session: AsyncSession):
    from datetime import timedelta
    from app.core.timezone import now_ist
    from app.db.models import UserAllowedWorkspaces
    user = await _create_user(db_session, role_name="viewer")
    db_session.add(UserAllowedWorkspaces(
        user_id=user.id, allowed_workspaces=1, allowed_responses=100,
        free_trial_expires_at=now_ist() - timedelta(days=1),
    ))
    await db_session.commit()
    with patch("app.services.billing_service.SUBSCRIPTION_ENFORCED", True):
        assert await billing_service.has_active_entitlement(user, db_session) is False


@pytest.mark.asyncio
async def test_api_account_status_trial(client: AsyncClient, db_session: AsyncSession):
    from app.services.entitlements_service import entitlements_service
    user = await _create_user(db_session, role_name="viewer")
    await entitlements_service.start_free_trial(user.id, db_session)
    await db_session.commit()
    authenticate_as(user)
    resp = await client.get("/api/billing/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "trial"
    assert data["plan"] == "starter"
    assert data["trialEndsAt"] is not None
    assert data["allowedWorkspaces"] == 1


@pytest.mark.asyncio
async def test_api_account_status_active_paid(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session, code="builder", tier=2)
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_status")
    await db_session.commit()
    authenticate_as(user)
    resp = await client.get("/api/billing/status")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "active"
    assert resp.json()["data"]["plan"] == "builder"


@pytest.mark.asyncio
async def test_api_account_status_active_paid_details(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session)
    plan = await _create_plan(db_session, code="builder", tier=2)
    plan.workspace_limit = 3
    plan.survey_response_cap = 500
    plan.storage_limit = 500
    plan.regeneration_limit = 5
    plan.stage_rerun = 3
    plan.survey_analytics = "Advanced"
    await _create_subscription(db_session, user, plan, status="active", rzp_sub_id="sub_status_dtl")
    await db_session.commit()
    authenticate_as(user)
    resp = await client.get("/api/billing/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "active"
    assert data["plan"] == "builder"
    assert data["priceMonthly"] == 499
    assert data["billingPeriod"] == "monthly"
    assert data["workspaceLimit"] == 3
    assert data["responseCap"] == 500
    assert data["storageLimitMB"] == 500
    assert data["regenerationLimit"] == 5
    assert data["stageRerun"] == 3
    assert data["surveyAnalytics"] == "Advanced"
    assert data["usedResponses"] == 0
    assert data["storageUsedMB"] == 0
    assert "trialEndsAt" not in data  # null fields are dropped


@pytest.mark.asyncio
async def test_api_account_status_none_excludes_missing(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session, role_name="viewer")
    await db_session.commit()
    authenticate_as(user)
    resp = await client.get("/api/billing/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "none"
    assert "priceMonthly" not in data
    assert "storageLimitMB" not in data
    assert "currentEnd" not in data
    assert data["usedResponses"] == 0


@pytest.mark.asyncio
async def test_api_account_status_none(client: AsyncClient, db_session: AsyncSession):
    user = await _create_user(db_session, role_name="viewer")
    await db_session.commit()
    authenticate_as(user)
    resp = await client.get("/api/billing/status")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "none"


# ── API webhook ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_api_webhook_missing_headers(client: AsyncClient):
    resp = await client.post("/api/billing/webhook", content=b"{}")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_webhook_no_secret_configured(client: AsyncClient):
    with patch.object(razorpay_service, "verify_webhook_signature", side_effect=RuntimeError("no secret")):
        resp = await client.post(
            "/api/billing/webhook",
            content=b"{}",
            headers={"X-Razorpay-Signature": "sig", "X-Razorpay-Event-Id": "evt"},
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_api_webhook_bad_signature(client: AsyncClient):
    with patch.object(razorpay_service, "verify_webhook_signature", return_value=False):
        resp = await client.post(
            "/api/billing/webhook",
            content=b"{}",
            headers={"X-Razorpay-Signature": "sig", "X-Razorpay-Event-Id": "evt"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_webhook_invalid_json(client: AsyncClient):
    with patch.object(razorpay_service, "verify_webhook_signature", return_value=True):
        resp = await client.post(
            "/api/billing/webhook",
            content=b"not-json",
            headers={"X-Razorpay-Signature": "sig", "X-Razorpay-Event-Id": "evt"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_webhook_success(client: AsyncClient):
    with patch.object(razorpay_service, "verify_webhook_signature", return_value=True), patch.object(
        billing_service, "handle_webhook", new=AsyncMock()
    ) as handle_mock:
        resp = await client.post(
            "/api/billing/webhook",
            content=json.dumps({"event": "subscription.charged", "payload": {}}).encode(),
            headers={"X-Razorpay-Signature": "sig", "X-Razorpay-Event-Id": "evt_ok"},
        )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    handle_mock.assert_awaited_once()
