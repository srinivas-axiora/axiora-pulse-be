import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from httpx import AsyncClient
from jose import jwt
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.security import hash_password_async, verify_password_async
from app.db.models import RefreshSession, Role, User, UserDetails
from app.models.auth_models import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ForgotPasswordResetRequest,
    ForgotPasswordVerifyRequest,
    ResendOTPRequest,
    UserLoginRequest,
    VerifyLoginRequest,
    VerifyOTPRequest,
)
from app.core.security import create_refresh_token
from app.db.models import RefreshSession
from app.models.auth_models import RefreshTokenRequest
from app.services.auth_service import auth_service, seed_admin_user
from app.services.email_service import OTPResult


def successful_otp_result() -> OTPResult:
    return OTPResult(success=True, channel="email")


async def create_user(
    db_session: AsyncSession,
    *,
    username: str = "verified@axiorapulse.com",
    password: str = "Test@12345",
    register_mfa: bool = True,
    role: str = "viewer",
) -> User:
    role_name = role if role != "user" else "viewer"
    role_row = (
        await db_session.execute(select(Role).where(Role.name == role_name))
    ).scalar_one_or_none()
    if role_row is None:
        role_row = Role(name=role_name, description=role_name)
        db_session.add(role_row)
        await db_session.flush()
    user = User(
        username=username,
        password=await hash_password_async(password),
        role=role_row,
        register_mfa=register_mfa,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def get_user_by_username(db_session: AsyncSession, username: str) -> User | None:
    result = await db_session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_register_success_persists_user_and_dispatches_otp(
    client: AsyncClient,
    db_session: AsyncSession,
):
    with patch(
        "app.services.auth_service.dispatch_otp",
        new=AsyncMock(return_value=successful_otp_result()),
    ) as dispatch_otp:
        response = await client.post(
            "/api/v1/auth/register",
            json={"username": "NewUser@AxioraPulse.com", "password": "Test@12345"},
        )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["username"] == "newuser@axiorapulse.com"
    assert data["registerMFA"] is False

    user = await get_user_by_username(db_session, "newuser@axiorapulse.com")
    assert user is not None
    assert user.register_otp is not None
    assert await verify_password_async("Test@12345", user.password)
    dispatch_otp.assert_awaited_once_with("newuser@axiorapulse.com", user.register_otp)


@pytest.mark.asyncio
async def test_register_existing_user_returns_conflict(
    client: AsyncClient,
    db_session: AsyncSession,
):
    await create_user(db_session, username="dupe@axiorapulse.com")

    with patch("app.services.auth_service.dispatch_otp", new=AsyncMock()) as dispatch_otp:
        response = await client.post(
            "/api/v1/auth/register",
            json={"username": "dupe@axiorapulse.com", "password": "Test@12345"},
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()["detail"] == "An account with this email already exists."
    dispatch_otp.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_invalid_email_is_rejected(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": "not-an-email", "password": "Test@12345"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_verify_registration_otp_success_returns_tokens(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(
        db_session,
        username="otp-success@axiorapulse.com",
        register_mfa=False,
    )
    user.register_otp = 123456
    user.register_otp_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/verifyOTP",
        json={
            "id": user.id,
            "otp": 123456,
            "flow": "register",
        },
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert data["access_token"]
    assert data["refresh_token"]

    await db_session.refresh(user)
    assert user.register_mfa is True
    assert user.register_otp is None


@pytest.mark.asyncio
async def test_verify_registration_otp_invalid_then_invalidates_after_three_attempts(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(
        db_session,
        username="otp-invalid@axiorapulse.com",
        register_mfa=False,
    )
    user.register_otp = 111111
    user.register_otp_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    for _ in range(2):
        response = await client.post(
            "/api/v1/auth/verifyOTP",
            json={"id": user.id, "otp": 999999, "flow": "register"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "OTP is wrong"

    response = await client.post(
        "/api/v1/auth/verifyOTP",
        json={"id": user.id, "otp": 999999, "flow": "register"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "failed"
    assert "Too many failed attempts" in response.json()["message"]
    await db_session.refresh(user)
    assert user.register_otp is None
    assert user.register_otp_attempts == 0


@pytest.mark.asyncio
async def test_verify_registration_otp_expired_clears_code(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(
        db_session,
        username="otp-expired@axiorapulse.com",
        register_mfa=False,
    )
    user.register_otp = 222222
    user.register_otp_expiry = datetime.now(tz=timezone.utc) - timedelta(minutes=1)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/verifyOTP",
        json={"id": user.id, "otp": 222222, "flow": "register"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["message"] == "OTP is expired !"
    await db_session.refresh(user)
    assert user.register_otp is None


@pytest.mark.asyncio
async def test_user_login_returns_tokens_directly_without_otp(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(db_session, username="login@axiorapulse.com")

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "Test@12345"},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert data["message"] == "Login successful."
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["role"] == "user"
    # `payment` now reflects real subscription entitlement (SUBSCRIPTION_ENFORCED
    # defaults on in tests). A freshly created user has no active subscription,
    # so the payment gate is closed → the frontend routes them to pricing.
    assert data["auth_actions"]["payment"] is False
    assert data["auth_actions"]["interactive_questions"] is True



@pytest.mark.asyncio
async def test_admin_login_returns_jwt_for_standard_login(
    client: AsyncClient,
    db_session: AsyncSession,
):
    await create_user(
        db_session,
        username="admin-login@axiorapulse.com",
        role="admin",
    )

    response = await client.post(
        "/api/v1/auth/admin/login",
        json={"username": "admin-login@axiorapulse.com", "password": "Test@12345"},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert data["role"] == "admin"
    assert data["access_token"]
    assert data["refresh_token"]


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(client: AsyncClient, db_session: AsyncSession):
    user = await create_user(db_session, username="wrong-password@axiorapulse.com")

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "Wrong@12345"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_login_rejects_unverified_account(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(
        db_session,
        username="unverified-login@axiorapulse.com",
        register_mfa=False,
    )

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "Test@12345"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN


async def _create_user_details(db_session: AsyncSession, user: User, *, profile_status: str) -> None:
    details = UserDetails(
        profile_id=f"AXR-{user.id:06d}",
        user_id=user.id,
        first_name="Test",
        last_name="User",
        email=user.username,
        profile_status=profile_status,
    )
    db_session.add(details)
    await db_session.commit()


async def _get_user_details(db_session: AsyncSession, user_id: int) -> UserDetails | None:
    result = await db_session.execute(
        select(UserDetails).where(UserDetails.user_id == user_id)
    )
    return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_login_allows_active_and_no_profile_users(
    client: AsyncClient, db_session: AsyncSession
):
    user = await create_user(db_session, username="active-login@axiorapulse.com")
    await _create_user_details(db_session, user, profile_status="Active")

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "Test@12345"},
    )

    assert response.status_code == status.HTTP_200_OK

    # A registered user with no profile row yet is treated as active.
    user_no_profile = await create_user(db_session, username="no-profile-login@axiorapulse.com")
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": user_no_profile.username, "password": "Test@12345"},
    )
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_login_rejects_inactive_user_with_meaningful_message(
    client: AsyncClient, db_session: AsyncSession
):
    user = await create_user(db_session, username="inactive-login@axiorapulse.com")
    await _create_user_details(db_session, user, profile_status="Inactive")

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "Test@12345"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "inactive" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_login_rejects_suspended_user_with_meaningful_message(
    client: AsyncClient, db_session: AsyncSession
):
    user = await create_user(db_session, username="suspended-login@axiorapulse.com")
    await _create_user_details(db_session, user, profile_status="Suspended")

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "Test@12345"},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "suspended" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_login_auto_creates_user_details_row(
    client: AsyncClient, db_session: AsyncSession
):
    user = await create_user(db_session, username="auto-profile@axiorapulse.com")
    assert await _get_user_details(db_session, user.id) is None

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "Test@12345"},
    )

    assert response.status_code == status.HTTP_200_OK
    details = await _get_user_details(db_session, user.id)
    assert details is not None
    assert details.email == user.username
    assert details.profile_status == "Active"
    assert details.last_login_date is not None


@pytest.mark.asyncio
async def test_verify_login_rejects_suspended_user(
    client: AsyncClient, db_session: AsyncSession
):
    user = await create_user(db_session, username="suspended-verify-login@axiorapulse.com")
    await _create_user_details(db_session, user, profile_status="Suspended")
    user.login_otp = 444444
    user.login_otp_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/verify-login",
        json={"emailOrMobile": user.username, "otp": 444444},
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "suspended" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_verify_login_success_returns_tokens_and_clears_otp(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(db_session, username="verify-login@axiorapulse.com")
    user.login_otp = 333333
    user.login_otp_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/verify-login",
        json={"emailOrMobile": user.username, "otp": 333333},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["access_token"]
    assert data["refresh_token"]
    await db_session.refresh(user)
    assert user.login_otp is None


@pytest.mark.asyncio
async def test_verify_login_rejects_incorrect_expired_and_empty_values(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(db_session, username="login-otp-cases@axiorapulse.com")
    user.login_otp = 444444
    user.login_otp_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    wrong_response = await client.post(
        "/api/v1/auth/verify-login",
        json={"emailOrMobile": user.username, "otp": 111111},
    )
    assert wrong_response.status_code == status.HTTP_400_BAD_REQUEST

    user.login_otp = 444444
    user.login_otp_expiry = datetime.now(tz=timezone.utc) - timedelta(minutes=1)
    await db_session.commit()
    expired_response = await client.post(
        "/api/v1/auth/verify-login",
        json={"emailOrMobile": user.username, "otp": 444444},
    )
    assert expired_response.status_code == status.HTTP_400_BAD_REQUEST

    empty_response = await client.post(
        "/api/v1/auth/verify-login",
        json={"emailOrMobile": "", "otp": ""},
    )
    assert empty_response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_forgot_password_request_is_successful_and_dispatches_without_network(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(db_session, username="forgot-request@axiorapulse.com")

    with patch(
        "app.services.auth_service.dispatch_password_reset_otp",
        new=AsyncMock(return_value=successful_otp_result()),
    ) as dispatch_reset_otp:
        response = await client.post(
            "/api/v1/auth/forgot-password/request",
            json={"emailOrMobile": user.username},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["status"] == "success"
    await db_session.refresh(user)
    assert user.forgot_password_otp is not None
    dispatch_reset_otp.assert_awaited_once_with(user.username, user.forgot_password_otp)


@pytest.mark.asyncio
async def test_forgot_password_verify_returns_signed_reset_token(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(db_session, username="forgot-verify@axiorapulse.com")
    user.forgot_password_otp = 555555
    user.forgot_password_otp_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/forgot-password/verify",
        json={"emailOrMobile": user.username, "code": 555555},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    payload = jwt.decode(
        data["reset_token"],
        os.getenv("JWT_SECRET_KEY"),
        algorithms=[os.getenv("JWT_ALGORITHM")],
    )
    assert payload["sub"] == str(user.id)
    assert payload["scope"] == "password_reset"
    await db_session.refresh(user)
    assert user.forgot_password_otp is None


@pytest.mark.asyncio
async def test_forgot_password_verify_rejects_incorrect_and_expired_codes(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(db_session, username="forgot-cases@axiorapulse.com")
    user.forgot_password_otp = 666666
    user.forgot_password_otp_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    wrong_response = await client.post(
        "/api/v1/auth/forgot-password/verify",
        json={"emailOrMobile": user.username, "code": 123456},
    )
    assert wrong_response.status_code == status.HTTP_400_BAD_REQUEST

    user.forgot_password_otp = 666666
    user.forgot_password_otp_expiry = datetime.now(tz=timezone.utc) - timedelta(minutes=1)
    await db_session.commit()
    expired_response = await client.post(
        "/api/v1/auth/forgot-password/verify",
        json={"emailOrMobile": user.username, "code": 666666},
    )
    assert expired_response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_forgot_password_reset_updates_hash_and_revokes_old_access_token(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(db_session, username="forgot-reset@axiorapulse.com")
    old_hash = user.password
    old_access_token = jwt.encode(
        {
            "sub": str(user.id),
            "username": user.username,
            "role": user.role,
            "iat": datetime.now(tz=timezone.utc) - timedelta(minutes=5),
            "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=30),
        },
        os.getenv("JWT_SECRET_KEY"),
        algorithm=os.getenv("JWT_ALGORITHM"),
    )
    reset_token = jwt.encode(
        {
            "sub": str(user.id),
            "username": user.username,
            "scope": "password_reset",
            "iat": datetime.now(tz=timezone.utc),
            "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=10),
        },
        os.getenv("JWT_SECRET_KEY"),
        algorithm=os.getenv("JWT_ALGORITHM"),
    )

    response = await client.post(
        "/api/v1/auth/forgot-password/reset",
        json={"reset_token": reset_token, "new_password": "NewPass@12345"},
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "success"
    assert data["message"] == "Password has been reset successfully."
    await db_session.refresh(user)
    assert user.password != old_hash
    assert await verify_password_async("NewPass@12345", user.password)
    assert user.password_changed_at is not None

    protected_response = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {old_access_token}"},
        json={"current_password": "NewPass@12345", "new_password": "Another@12345"},
    )
    assert protected_response.status_code == status.HTTP_401_UNAUTHORIZED

    reused_response = await client.post(
        "/api/v1/auth/forgot-password/reset",
        json={"reset_token": reset_token, "new_password": "Another@12345"},
    )
    assert reused_response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_forgot_password_reset_rejects_invalid_and_expired_tokens(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(db_session, username="forgot-reset-fail@axiorapulse.com")
    expired_reset_token = jwt.encode(
        {
            "sub": str(user.id),
            "username": user.username,
            "scope": "password_reset",
            "iat": datetime.now(tz=timezone.utc) - timedelta(minutes=20),
            "exp": datetime.now(tz=timezone.utc) - timedelta(minutes=1),
        },
        os.getenv("JWT_SECRET_KEY"),
        algorithm=os.getenv("JWT_ALGORITHM"),
    )

    invalid_response = await client.post(
        "/api/v1/auth/forgot-password/reset",
        json={"reset_token": "not-a-real-token", "new_password": "NewPass@12345"},
    )
    assert invalid_response.status_code == status.HTTP_401_UNAUTHORIZED

    expired_response = await client.post(
        "/api/v1/auth/forgot-password/reset",
        json={"reset_token": expired_reset_token, "new_password": "NewPass@12345"},
    )
    assert expired_response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_seed_admin_user_creates_and_updates_default_admin(db_session: AsyncSession):
    await seed_admin_user(db_session)
    admin = await get_user_by_username(db_session, "admin@axiorapulse.com")
    assert admin is not None
    assert admin.role == "admin"
    assert admin.register_mfa is True
    assert await verify_password_async("Test@12345", admin.password)

    admin.password = await hash_password_async("OldPass@12345")
    admin.role = "user"
    admin.register_mfa = False
    await db_session.commit()

    await seed_admin_user(db_session)
    await db_session.refresh(admin)
    assert admin.role == "admin"
    assert admin.register_mfa is True
    assert await verify_password_async("Test@12345", admin.password)


@pytest.mark.asyncio
async def test_register_keeps_user_when_otp_dispatch_fails(db_session: AsyncSession):
    with patch(
        "app.services.auth_service.dispatch_otp",
        new=AsyncMock(return_value=OTPResult(success=False, channel="email", error="smtp down")),
    ):
        response = await auth_service.register(
            request={"username": "dispatch-fail@axiorapulse.com", "password": "Test@12345"},
            db=db_session,
        )

    assert response.username == "dispatch-fail@axiorapulse.com"
    saved = await get_user_by_username(db_session, response.username)
    assert saved is not None
    assert saved.register_otp is not None


@pytest.mark.asyncio
async def test_verify_registration_otp_without_active_code_returns_failed(db_session: AsyncSession):
    user = await create_user(
        db_session,
        username="no-active-register-otp@axiorapulse.com",
        register_mfa=False,
    )

    response = await auth_service.verify_otp(
        VerifyOTPRequest(id=user.id, otp=123456, flow="register"),
        db_session,
    )

    assert response.status == "failed"
    assert response.message == "OTP is wrong"


@pytest.mark.asyncio
async def test_resend_otp_success_and_failure_paths(db_session: AsyncSession):
    user = await create_user(
        db_session,
        username="resend@axiorapulse.com",
        register_mfa=False,
    )

    with patch(
        "app.services.auth_service.dispatch_otp",
        new=AsyncMock(return_value=successful_otp_result()),
    ) as dispatch_otp:
        response = await auth_service.resend_otp(
            ResendOTPRequest(id=user.id, flow="register"),
            db_session,
        )

    assert response.userid == user.id
    await db_session.refresh(user)
    await db_session.refresh(user)
    assert user.register_otp is not None
    dispatch_otp.assert_awaited_once_with(user.username, user.register_otp)

    verified_user = await create_user(
        db_session,
        username="resend-verified@axiorapulse.com",
        register_mfa=True,
    )
    with pytest.raises(HTTPException) as verified_exc:
        await auth_service.resend_otp(
            ResendOTPRequest(id=verified_user.id, flow="register"),
            db_session,
        )
    assert verified_exc.value.status_code == status.HTTP_400_BAD_REQUEST

    failing_user = await create_user(
        db_session,
        username="resend-dispatch-fail@axiorapulse.com",
        register_mfa=False,
    )
    with patch(
        "app.services.auth_service.dispatch_otp",
        new=AsyncMock(return_value=OTPResult(success=False, channel="email", error="delivery failed")),
    ):
        with pytest.raises(HTTPException) as dispatch_exc:
            await auth_service.resend_otp(
                ResendOTPRequest(id=failing_user.id, flow="register"),
                db_session,
            )
    assert dispatch_exc.value.status_code == status.HTTP_502_BAD_GATEWAY


@pytest.mark.asyncio
async def test_auth_service_login_returns_tokens_directly(db_session: AsyncSession):
    user = await create_user(db_session, username="direct-login@axiorapulse.com")

    response = await auth_service.login(
        UserLoginRequest(username=user.username, password="Test@12345"),
        db_session,
    )

    assert response.status == "success"
    assert response.access_token is not None
    assert response.refresh_token is not None
    assert response.role == "user"
    # No subscription → payment gate closed (see entitlement note above).
    assert response.auth_actions.payment is False


@pytest.mark.asyncio
async def test_login_payment_true_with_active_subscription(db_session: AsyncSession):
    """An entitled subscription flips the auth-response `payment` flag to True,
    which routes the user to the dashboard instead of pricing."""
    from app.db.models import Subscription

    user = await create_user(db_session, username="subscribed@axiorapulse.com")
    db_session.add(
        Subscription(
            user_id=user.id,
            razorpay_subscription_id="sub_test_entitled",
            razorpay_plan_id="plan_test",
            status="active",  # one of billing_service._ENTITLED
        )
    )
    await db_session.flush()

    response = await auth_service.login(
        UserLoginRequest(username=user.username, password="Test@12345"),
        db_session,
    )

    assert response.status == "success"
    assert response.auth_actions.payment is True


@pytest.mark.asyncio
async def test_verify_login_not_found_and_no_active_otp(db_session: AsyncSession):
    with pytest.raises(HTTPException) as not_found_exc:
        await auth_service.verify_login(
            VerifyLoginRequest(emailOrMobile="missing@axiorapulse.com", otp=123456),
            db_session,
        )
    assert not_found_exc.value.status_code == status.HTTP_404_NOT_FOUND

    user = await create_user(db_session, username="login-no-otp@axiorapulse.com")
    with pytest.raises(HTTPException) as no_otp_exc:
        await auth_service.verify_login(
            VerifyLoginRequest(emailOrMobile=user.username, otp=123456),
            db_session,
        )
    assert no_otp_exc.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_admin_login_rejects_invalid_credentials_and_non_admin(db_session: AsyncSession):
    user = await create_user(db_session, username="not-admin@axiorapulse.com")

    with pytest.raises(HTTPException) as wrong_password_exc:
        await auth_service.admin_login(
            UserLoginRequest(username=user.username, password="Wrong@12345"),
            db_session,
        )
    assert wrong_password_exc.value.status_code == status.HTTP_401_UNAUTHORIZED

    with pytest.raises(HTTPException) as non_admin_exc:
        await auth_service.admin_login(
            UserLoginRequest(username=user.username, password="Test@12345"),
            db_session,
        )
    assert non_admin_exc.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_forgot_password_request_not_found_and_dispatch_failure(db_session: AsyncSession):
    with pytest.raises(HTTPException) as missing_exc:
        await auth_service.forgot_password_request(
            ForgotPasswordRequest(emailOrMobile="missing-reset@axiorapulse.com"),
            db_session,
        )
    assert missing_exc.value.status_code == status.HTTP_404_NOT_FOUND

    user = await create_user(db_session, username="reset-dispatch-fail@axiorapulse.com")
    with patch(
        "app.services.auth_service.dispatch_password_reset_otp",
        new=AsyncMock(return_value=OTPResult(success=False, channel="email", error="smtp failed")),
    ):
        response = await auth_service.forgot_password_request(
            ForgotPasswordRequest(emailOrMobile=user.username),
            db_session,
        )
    assert response.status == "success"
    await db_session.refresh(user)
    assert user.forgot_password_otp is not None


@pytest.mark.asyncio
async def test_forgot_password_verify_not_found_and_no_active_code(db_session: AsyncSession):
    with pytest.raises(HTTPException) as missing_exc:
        await auth_service.forgot_password_verify(
            ForgotPasswordVerifyRequest(emailOrMobile="missing-verify@axiorapulse.com", code=123456),
            db_session,
        )
    assert missing_exc.value.status_code == status.HTTP_404_NOT_FOUND

    user = await create_user(db_session, username="reset-no-code@axiorapulse.com")
    with pytest.raises(HTTPException) as no_code_exc:
        await auth_service.forgot_password_verify(
            ForgotPasswordVerifyRequest(emailOrMobile=user.username, code=123456),
            db_session,
        )
    assert no_code_exc.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_forgot_password_reset_rejects_token_without_subject_and_unknown_user(
    db_session: AsyncSession,
):
    no_subject_token = jwt.encode(
        {
            "username": "no-sub@axiorapulse.com",
            "scope": "password_reset",
            "iat": datetime.now(tz=timezone.utc),
            "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=10),
        },
        os.getenv("JWT_SECRET_KEY"),
        algorithm=os.getenv("JWT_ALGORITHM"),
    )
    with pytest.raises(HTTPException) as no_subject_exc:
        await auth_service.forgot_password_reset(
            ForgotPasswordResetRequest(reset_token=no_subject_token, new_password="NewPass@12345"),
            db_session,
        )
    assert no_subject_exc.value.status_code == status.HTTP_401_UNAUTHORIZED

    unknown_user_token = jwt.encode(
        {
            "sub": "999999",
            "username": "unknown@axiorapulse.com",
            "scope": "password_reset",
            "iat": datetime.now(tz=timezone.utc),
            "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=10),
        },
        os.getenv("JWT_SECRET_KEY"),
        algorithm=os.getenv("JWT_ALGORITHM"),
    )
    with pytest.raises(HTTPException) as unknown_user_exc:
        await auth_service.forgot_password_reset(
            ForgotPasswordResetRequest(reset_token=unknown_user_token, new_password="NewPass@12345"),
            db_session,
        )
    assert unknown_user_exc.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_change_password_success_and_failure(db_session: AsyncSession):
    user = await create_user(db_session, username="change-password@axiorapulse.com")

    # Reject identical current and new passwords at model validation level
    with pytest.raises(ValidationError) as same_pw_model_exc:
        ChangePasswordRequest(current_password="Test@12345", new_password="Test@12345")
    assert "New password cannot be the same as your current password." in str(same_pw_model_exc.value)

    # Reject incorrect current password
    with pytest.raises(HTTPException) as wrong_current_exc:
        await auth_service.change_password(
            user,
            ChangePasswordRequest(current_password="Wrong@12345", new_password="NewPass@12345"),
            db_session,
        )
    assert wrong_current_exc.value.status_code == status.HTTP_401_UNAUTHORIZED

    # Service layer defense-in-depth: rejected if model validation is bypassed
    bypassed_request = ChangePasswordRequest.model_construct(
        current_password="Test@12345",
        new_password="Test@12345",
    )
    with pytest.raises(HTTPException) as same_pw_service_exc:
        await auth_service.change_password(
            user,
            bypassed_request,
            db_session,
        )
    assert same_pw_service_exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert same_pw_service_exc.value.detail == "New password cannot be the same as your current password."

    response = await auth_service.change_password(
        user,
        ChangePasswordRequest(current_password="Test@12345", new_password="NewPass@12345"),
        db_session,
    )
    assert response.status == "success"
    assert await verify_password_async("NewPass@12345", user.password)
    assert user.password_changed_at is not None


@pytest.mark.asyncio
async def test_change_password_api_rejects_identical_passwords(
    client: AsyncClient,
    db_session: AsyncSession,
):
    user = await create_user(db_session, username="change-pw-api@axiorapulse.com")
    access_token = jwt.encode(
        {
            "sub": str(user.id),
            "username": user.username,
            "role": user._primary_role,
            "iat": datetime.now(tz=timezone.utc),
            "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=15),
        },
        os.getenv("JWT_SECRET_KEY"),
        algorithm=os.getenv("JWT_ALGORITHM"),
    )

    response = await client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"current_password": "Test@12345", "new_password": "Test@12345"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "New password cannot be the same as your current password." in response.text


@pytest.mark.asyncio
async def test_verify_registration_otp_success_enqueues_welcome_email(
    db_session: AsyncSession,
    stub_enqueue_email_job,
):
    user = await create_user(
        db_session,
        username="welcome-email@axiorapulse.com",
        register_mfa=False,
    )
    user.register_otp = 654321
    user.register_otp_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    response = await auth_service.verify_otp(
        VerifyOTPRequest(id=user.id, otp=654321, flow="register"),
        db_session,
    )

    assert response.status == "success"
    stub_enqueue_email_job.assert_called_once_with(
        "registration_success",
        to_email=user.username,
        display_name=user.display_name,
    )


@pytest.mark.asyncio
async def test_verify_registration_otp_failure_does_not_enqueue_welcome_email(
    db_session: AsyncSession,
    stub_enqueue_email_job,
):
    user = await create_user(
        db_session,
        username="welcome-email-fail@axiorapulse.com",
        register_mfa=False,
    )
    user.register_otp = 654321
    user.register_otp_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    await db_session.commit()

    response = await auth_service.verify_otp(
        VerifyOTPRequest(id=user.id, otp=111111, flow="register"),
        db_session,
    )

    assert response.status == "failed"
    stub_enqueue_email_job.assert_not_called()


@pytest.mark.asyncio
async def test_forgot_password_reset_enqueues_password_changed_email(
    client: AsyncClient,
    db_session: AsyncSession,
    stub_enqueue_email_job,
):
    user = await create_user(db_session, username="reset-email@axiorapulse.com")
    reset_token = jwt.encode(
        {
            "sub": str(user.id),
            "username": user.username,
            "scope": "password_reset",
            "iat": datetime.now(tz=timezone.utc),
            "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=10),
        },
        os.getenv("JWT_SECRET_KEY"),
        algorithm=os.getenv("JWT_ALGORITHM"),
    )

    response = await client.post(
        "/api/v1/auth/forgot-password/reset",
        json={"reset_token": reset_token, "new_password": "NewPass@12345"},
    )

    assert response.status_code == status.HTTP_200_OK
    stub_enqueue_email_job.assert_called_once()
    call_args, call_kwargs = stub_enqueue_email_job.call_args
    assert call_args == ("password_reset_success",)
    assert call_kwargs["to_email"] == user.username
    assert call_kwargs["changed_at"] is not None


@pytest.mark.asyncio
async def test_change_password_enqueues_password_changed_email(
    db_session: AsyncSession,
    stub_enqueue_email_job,
):
    user = await create_user(db_session, username="change-password-email@axiorapulse.com")

    response = await auth_service.change_password(
        user,
        ChangePasswordRequest(current_password="Test@12345", new_password="NewPass@12345"),
        db_session,
    )

    assert response.status == "success"
    stub_enqueue_email_job.assert_called_once_with(
        "password_reset_success",
        to_email=user.username,
        changed_at=user.password_changed_at,
    )


@pytest.mark.asyncio
async def test_get_current_user_validates_tokens_and_revocation(db_session: AsyncSession):
    user = await create_user(db_session, username="dependency@axiorapulse.com")
    token = jwt.encode(
        {
            "sub": str(user.id),
            "username": user.username,
            "iat": datetime.now(tz=timezone.utc),
            "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=30),
        },
        os.getenv("JWT_SECRET_KEY"),
        algorithm=os.getenv("JWT_ALGORITHM"),
    )
    assert (
        await get_current_user(
            auth_credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
            db=db_session,
        )
    ).id == user.id

    with pytest.raises(HTTPException):
        await get_current_user(
            auth_credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt"),
            db=db_session,
        )

    reset_scoped_token = jwt.encode(
        {
            "sub": str(user.id),
            "scope": "password_reset",
            "iat": datetime.now(tz=timezone.utc),
            "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=30),
        },
        os.getenv("JWT_SECRET_KEY"),
        algorithm=os.getenv("JWT_ALGORITHM"),
    )
    with pytest.raises(HTTPException):
        await get_current_user(
            auth_credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=reset_scoped_token),
            db=db_session,
        )

    revoked_token = jwt.encode(
        {
            "sub": str(user.id),
            "iat": datetime.now(tz=timezone.utc) - timedelta(minutes=10),
            "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=30),
        },
        os.getenv("JWT_SECRET_KEY"),
        algorithm=os.getenv("JWT_ALGORITHM"),
    )
    user.password_changed_at = datetime.now(tz=timezone.utc)
    await db_session.commit()
    with pytest.raises(HTTPException) as revoked_exc:
        await get_current_user(
            auth_credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=revoked_token),
            db=db_session,
        )
    assert revoked_exc.value.status_code == status.HTTP_401_UNAUTHORIZED


async def issue_refresh_session(db_session: AsyncSession, user: User, *, expires_in_days: int = 7) -> str:
    session_id = "test-session-id"
    db_session.add(RefreshSession(
        id=session_id,
        user_id=user.id,
        expires_at=datetime.now(tz=timezone.utc) + timedelta(days=expires_in_days),
    ))
    await db_session.commit()
    return create_refresh_token(data={"sub": str(user.id), "username": user.username, "sid": session_id})


@pytest.mark.asyncio
async def test_refresh_rotates_token_and_revokes_old_session(db_session: AsyncSession):
    user = await create_user(db_session, username="refresh-ok@axiorapulse.com")
    token = await issue_refresh_session(db_session, user)

    response = await auth_service.refresh(RefreshTokenRequest(refresh_token=token), db_session)

    assert response.data.accessToken
    assert response.data.refreshToken
    old_session = await db_session.get(RefreshSession, "test-session-id")
    assert old_session.revoked_at is not None


@pytest.mark.asyncio
async def test_refresh_rejects_malformed_token(db_session: AsyncSession):
    with pytest.raises(HTTPException) as exc:
        await auth_service.refresh(RefreshTokenRequest(refresh_token="not-a-jwt"), db_session)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_refresh_rejects_unknown_session(db_session: AsyncSession):
    user = await create_user(db_session, username="refresh-no-session@axiorapulse.com")
    token = create_refresh_token(data={"sub": str(user.id), "username": user.username, "sid": "does-not-exist"})

    with pytest.raises(HTTPException) as exc:
        await auth_service.refresh(RefreshTokenRequest(refresh_token=token), db_session)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_refresh_rejects_already_revoked_session(db_session: AsyncSession):
    user = await create_user(db_session, username="refresh-revoked@axiorapulse.com")
    token = await issue_refresh_session(db_session, user)
    session = await db_session.get(RefreshSession, "test-session-id")
    session.revoked_at = datetime.now(tz=timezone.utc)
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await auth_service.refresh(RefreshTokenRequest(refresh_token=token), db_session)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_refresh_rejects_expired_session_and_marks_it_revoked(db_session: AsyncSession):
    user = await create_user(db_session, username="refresh-expired@axiorapulse.com")
    token = await issue_refresh_session(db_session, user, expires_in_days=-1)

    with pytest.raises(HTTPException) as exc:
        await auth_service.refresh(RefreshTokenRequest(refresh_token=token), db_session)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED

    session = await db_session.get(RefreshSession, "test-session-id")
    assert session.revoked_at is not None


@pytest.mark.asyncio
async def test_refresh_rejects_unverified_account(db_session: AsyncSession):
    user = await create_user(db_session, username="refresh-unverified@axiorapulse.com", register_mfa=False)
    token = await issue_refresh_session(db_session, user)

    with pytest.raises(HTTPException) as exc:
        await auth_service.refresh(RefreshTokenRequest(refresh_token=token), db_session)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_logout_revokes_all_active_sessions(db_session: AsyncSession):
    user = await create_user(db_session, username="logout@axiorapulse.com")
    db_session.add(RefreshSession(id="s1", user_id=user.id, expires_at=datetime.now(tz=timezone.utc) + timedelta(days=7)))
    db_session.add(RefreshSession(id="s2", user_id=user.id, expires_at=datetime.now(tz=timezone.utc) + timedelta(days=7)))
    await db_session.commit()

    response = await auth_service.logout(user, db_session)

    assert response.status == "success"
    s1 = await db_session.get(RefreshSession, "s1")
    s2 = await db_session.get(RefreshSession, "s2")
    assert s1.revoked_at is not None
    assert s2.revoked_at is not None


@pytest.mark.asyncio
async def test_verify_otp_login_flow_rejects_unverified_account(db_session: AsyncSession):
    user = await create_user(db_session, username="verify-login-unverified@axiorapulse.com", register_mfa=False)

    response = await auth_service.verify_otp(
        VerifyOTPRequest(id=user.id, otp=123456, flow="login"), db_session
    )
    assert response.status == "failed"
    assert "not verified" in response.message.lower()


@pytest.mark.asyncio
async def test_resend_otp_by_numeric_string_id(db_session: AsyncSession):
    user = await create_user(db_session, username="resend-string-id@axiorapulse.com", register_mfa=False)

    with patch("app.services.auth_service.dispatch_otp", new=AsyncMock(return_value=successful_otp_result())):
        response = await auth_service.resend_otp(
            ResendOTPRequest(id=str(user.id)), db_session
        )
    assert response.userid == user.id


@pytest.mark.asyncio
async def test_resend_otp_by_username_fallback_when_id_not_digits(db_session: AsyncSession):
    user = await create_user(db_session, username="resend-fallback@axiorapulse.com", register_mfa=False)

    with patch("app.services.auth_service.dispatch_otp", new=AsyncMock(return_value=successful_otp_result())):
        response = await auth_service.resend_otp(
            ResendOTPRequest(id="not-a-number", emailOrMobile=user.username), db_session
        )
    assert response.userid == user.id


@pytest.mark.asyncio
async def test_resend_otp_login_flow_rejects_unverified_account(db_session: AsyncSession):
    user = await create_user(db_session, username="resend-login-unverified@axiorapulse.com", register_mfa=False)

    with pytest.raises(HTTPException) as exc:
        await auth_service.resend_otp(
            ResendOTPRequest(id=user.id, flow="login"), db_session
        )
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_resend_otp_login_flow_success_dispatches_login_otp(db_session: AsyncSession):
    user = await create_user(db_session, username="resend-login-ok@axiorapulse.com")

    with patch(
        "app.services.auth_service.dispatch_login_otp",
        new=AsyncMock(return_value=successful_otp_result()),
    ) as dispatch_login_otp:
        response = await auth_service.resend_otp(
            ResendOTPRequest(id=user.id, flow="login"), db_session
        )
    assert response.userid == user.id
    dispatch_login_otp.assert_awaited_once()


@pytest.mark.asyncio
async def test_resend_otp_login_flow_dispatch_failure_raises_502(db_session: AsyncSession):
    user = await create_user(db_session, username="resend-login-fail@axiorapulse.com")

    with patch(
        "app.services.auth_service.dispatch_login_otp",
        new=AsyncMock(return_value=OTPResult(success=False, channel="email", error="smtp down")),
    ):
        with pytest.raises(HTTPException) as exc:
            await auth_service.resend_otp(ResendOTPRequest(id=user.id, flow="login"), db_session)
    assert exc.value.status_code == status.HTTP_502_BAD_GATEWAY