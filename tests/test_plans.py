import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.models import Plan, User


async def _authenticate(user: User):
    from main import app

    async def _mock_current_user():
        return user

    app.dependency_overrides[get_current_user] = _mock_current_user


async def seed_plan(
    db_session: AsyncSession,
    *,
    code: str,
    name: str,
    tier: int = 1,
    price_monthly: int = 299,
    is_active: bool = True,
) -> Plan:
    plan = Plan(
        code=code,
        name=name,
        tier=tier,
        price_monthly=price_monthly,
        is_active=is_active,
    )
    db_session.add(plan)
    await db_session.flush()
    await db_session.refresh(plan)
    return plan


# ── Auth gating ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_plans_require_admin(client: AsyncClient, normal_user: User):
    await _authenticate(normal_user)
    assert (await client.get("/api/v1/plans")).status_code == status.HTTP_403_FORBIDDEN
    assert (
        await client.get("/api/v1/plan/1")
    ).status_code == status.HTTP_403_FORBIDDEN
    assert (
        await client.post("/api/v1/plan", json={"code": "x", "name": "X"})
    ).status_code == status.HTTP_403_FORBIDDEN
    assert (
        await client.put("/api/v1/plan/1", json={})
    ).status_code == status.HTTP_403_FORBIDDEN


# ── Create ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_plan_success(client: AsyncClient, admin_user: User):
    await _authenticate(admin_user)
    response = await client.post(
        "/api/v1/plan",
        json={
            "code": "builder",
            "name": "Builder",
            "description": "3 workspaces",
            "price_monthly": 299,
            "tier": 1,
            "workspace_limit": 3,
            "survey_response_cap": 500,
            "export_enabled": True,
            "stage_rerun": 5,
            "survey_analytics": "Advanced",
            "storage_limit": 2000,
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["code"] == "builder"
    assert data["workspace_limit"] == 3
    assert data["stage_rerun"] == 5
    assert data["survey_analytics"] == "Advanced"
    assert data["storage_limit"] == 2000
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_create_plan_defaults(client: AsyncClient, admin_user: User):
    await _authenticate(admin_user)
    response = await client.post(
        "/api/v1/plan",
        json={"code": "pro", "name": "Pro", "price_monthly": 799},
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["survey_analytics"] == "Basic"
    assert data["storage_limit"] is None


@pytest.mark.asyncio
async def test_create_plan_duplicate_code(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    await seed_plan(db_session, code="pro", name="Pro")
    await db_session.commit()
    await _authenticate(admin_user)
    response = await client.post(
        "/api/v1/plan",
        json={"code": "pro", "name": "Pro Again"},
    )
    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_create_plan_invalid_analytics(
    client: AsyncClient, admin_user: User
):
    await _authenticate(admin_user)
    response = await client.post(
        "/api/v1/plan",
        json={"code": "pro", "name": "Pro", "survey_analytics": "Pro"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ── List / Get ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_plans_includes_inactive(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    await seed_plan(db_session, code="prod", name="Legacy", tier=0, is_active=False)
    await seed_plan(db_session, code="pro", name="Pro", tier=2)
    await db_session.commit()
    await _authenticate(admin_user)
    response = await client.get("/api/v1/plans")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    codes = {plan["code"] for plan in data["plans"]}
    assert codes == {"prod", "pro"}


@pytest.mark.asyncio
async def test_get_plan_success(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    plan = await seed_plan(db_session, code="builder", name="Builder", tier=1)
    await db_session.commit()
    await _authenticate(admin_user)
    response = await client.get(f"/api/v1/plan/{plan.id}")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["code"] == "builder"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_get_plan_not_found(client: AsyncClient, admin_user: User):
    await _authenticate(admin_user)
    response = await client.get("/api/v1/plan/999")
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ── Create with Razorpay ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_plan_with_razorpay_success(
    client: AsyncClient, admin_user: User, monkeypatch
):
    calls = {}

    def _fake_create(**kwargs):
        calls[kwargs["notes"]["billing_period"]] = kwargs
        return {"id": f"rzp_plan_{kwargs['notes']['billing_period']}"}

    monkeypatch.setattr(
        "app.services.plan_service.razorpay_service.create_plan", _fake_create
    )
    await _authenticate(admin_user)

    response = await client.post(
        "/api/v1/plan/with-razorpay",
        json={
            "code": "pro",
            "name": "Pro",
            "description": "Full features",
            "price_monthly": 799,
            "price_yearly": 7990,
            "currency": "INR",
            "tier": 1,
            "workspace_limit": 10,
            "survey_response_cap": 2000,
            "regeneration_limit": 20,
            "export_enabled": True,
            "stage_rerun": 5,
            "survey_analytics": "Advanced",
            "storage_limit": 5000,
            "popular": True,
        },
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["code"] == "pro"
    assert data["razorpay_plan_id_monthly"] == "rzp_plan_monthly"
    assert data["razorpay_plan_id_yearly"] == "rzp_plan_yearly"
    assert data["workspace_limit"] == 10
    assert data["survey_response_cap"] == 2000
    assert data["survey_analytics"] == "Advanced"
    assert data["storage_limit"] == 5000

    monthly, yearly = calls["monthly"], calls["yearly"]
    assert monthly["period"] == "monthly"
    assert monthly["amount_paise"] == 79900
    assert yearly["period"] == "yearly"
    assert yearly["amount_paise"] == 799000
    assert monthly["name"] == "Pro (Monthly)"
    assert yearly["name"] == "Pro (Yearly)"
    assert monthly["currency"] == "INR"
    assert monthly["notes"] == {"plan_code": "pro", "billing_period": "monthly"}
    assert monthly["key_id"] is None
    assert monthly["key_secret"] is None


@pytest.mark.asyncio
async def test_create_plan_with_razorpay_missing_credentials_uses_env(
    client: AsyncClient, admin_user: User, monkeypatch
):
    captured = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)
        return {"id": "rzp_plan_env_monthly"}

    monkeypatch.setattr(
        "app.services.plan_service.razorpay_service.create_plan", _fake_create
    )
    await _authenticate(admin_user)

    response = await client.post(
        "/api/v1/plan/with-razorpay",
        json={"code": "env", "name": "Env", "price_monthly": 100},
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert captured["key_id"] is None
    assert captured["key_secret"] is None


@pytest.mark.asyncio
async def test_create_plan_with_razorpay_free_tier_skips_razorpay(
    client: AsyncClient, admin_user: User, monkeypatch, db_session: AsyncSession
):
    import app.services.plan_service as plan_module

    def _fail(**kwargs):
        raise AssertionError("Razorpay should not be called for a free tier")

    monkeypatch.setattr(plan_module.razorpay_service, "create_plan", _fail)
    await _authenticate(admin_user)

    response = await client.post(
        "/api/v1/plan/with-razorpay",
        json={"code": "free", "name": "Free"},
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["razorpay_plan_id_monthly"] is None
    assert data["razorpay_plan_id_yearly"] is None

    stored = (
        await db_session.execute(select(Plan).where(Plan.code == "free"))
    ).scalar_one()
    assert stored.razorpay_plan_id_monthly is None
    assert stored.razorpay_plan_id_yearly is None


@pytest.mark.asyncio
async def test_create_plan_with_razorpay_failure_rolls_back(
    client: AsyncClient, admin_user: User, monkeypatch, db_session: AsyncSession
):
    import app.services.plan_service as plan_module

    seen = []

    def _failing_create(**kwargs):
        seen.append(kwargs["notes"]["billing_period"])
        if kwargs["notes"]["billing_period"] == "yearly":
            raise RuntimeError("Razorpay is down")
        return {"id": "rzp_plan_monthly"}

    monkeypatch.setattr(plan_module.razorpay_service, "create_plan", _failing_create)
    await _authenticate(admin_user)

    response = await client.post(
        "/api/v1/plan/with-razorpay",
        json={
            "code": "enterprise",
            "name": "Enterprise",
            "price_monthly": 1499,
            "price_yearly": 14990,
        },
    )
    assert response.status_code == status.HTTP_502_BAD_GATEWAY

    assert seen == ["monthly", "yearly"]
    stored = (await db_session.execute(
        select(Plan).where(Plan.code == "enterprise")
    )).scalar_one_or_none()
    assert stored is None


@pytest.mark.asyncio
async def test_create_plan_with_razorpay_requires_admin(
    client: AsyncClient, normal_user: User
):
    await _authenticate(normal_user)
    response = await client.post(
        "/api/v1/plan/with-razorpay",
        json={"code": "pro", "name": "Pro"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── Update / activate-deactivate ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_plan_deactivate(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    plan = await seed_plan(db_session, code="pro", name="Pro", is_active=True)
    await db_session.commit()
    await _authenticate(admin_user)
    response = await client.put(
        f"/api/v1/plan/{plan.id}", json={"is_active": False}
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["is_active"] is False


@pytest.mark.asyncio
async def test_update_plan_fields(
    client: AsyncClient, admin_user: User, db_session: AsyncSession
):
    plan = await seed_plan(db_session, code="builder", name="Builder", price_monthly=299)
    await db_session.commit()
    await _authenticate(admin_user)
    response = await client.put(
        f"/api/v1/plan/{plan.id}",
        json={
            "price_monthly": 499,
            "survey_response_cap": 1000,
            "survey_analytics": "Advanced",
            "storage_limit": 500,
            "stage_rerun": 10,
        },
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["price_monthly"] == 499
    assert data["survey_response_cap"] == 1000
    assert data["survey_analytics"] == "Advanced"
    assert data["storage_limit"] == 500
    assert data["stage_rerun"] == 10


@pytest.mark.asyncio
async def test_update_plan_not_found(client: AsyncClient, admin_user: User):
    await _authenticate(admin_user)
    response = await client.put("/api/v1/plan/999", json={"is_active": False})
    assert response.status_code == status.HTTP_404_NOT_FOUND