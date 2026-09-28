"""
app/services/auth_service.py
────────────────────────────────────────────────────────────────────────────────
Authentication service — fully database-backed using async SQLAlchemy.

Replaces the previous in-memory user store.

Operations:
  register()    → hash password, generate OTP, persist user, dispatch OTP
  verify_otp()  → validate OTP + expiry, set registerMFA=True, return JWT
  resend_otp()  → generate new OTP, update DB, redispatch OTP
  login()       → verify credentials, return JWT

OTP delivery is routed through otp_dispatcher which detects email vs phone
automatically — auth_service never needs to know the channel used.
"""
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_otp,
    hash_password_async,
    otp_expiry,
    verify_password_async,
    create_password_reset_token,
    verify_password_reset_token,
    verify_refresh_token,
)
import os

from app.workers.background_jobs import enqueue_email_job

from dotenv import load_dotenv

load_dotenv()

_ACCESS_TOKEN_MINS = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES"))
from app.core.google_auth import GoogleTokenError, verify_google_id_token
from app.db.models import AuthActions, RefreshSession, Role, User, UserDetails
from app.models.auth_models import (
    AdminLoginResponse,
    AuthActionsData,
    GoogleAuthRequest,
    GoogleAuthResponse,
    RegisterAuthActionsData,
    LoginSuccessResponse,
    RegisterResponse,
    ResendOTPRequest,
    UserLoginRequest,
    UserRegisterRequest,
    VerifyOTPRequest,
    VerifyOTPResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ForgotPasswordVerifyRequest,
    ForgotPasswordVerifyResponse,
    ForgotPasswordResetRequest,
    ForgotPasswordResetResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginOTPResponse,
    VerifyLoginRequest,
    VerifyLoginResponse,
    RefreshTokenRequest,
    RefreshTokenData,
    RefreshTokenResponse,
    LogoutResponse,
)
from app.services.otp_dispatcher import dispatch_otp, dispatch_password_reset_otp, dispatch_login_otp

logger = logging.getLogger(__name__)


async def seed_admin_user(db: AsyncSession) -> None:
    """Ensure the default admin user exists with credentials admin@axiorapulse.com / Test@12345."""
    admin_email = "admin@axiorapulse.com"
    result = await db.execute(select(User).where(User.username == admin_email))
    admin_user = result.scalar_one_or_none()

    hashed_pw = await hash_password_async("Test@12345")

    admin_role = await _get_role(db, "admin")
    if admin_role is None:
        admin_role = Role(name="admin", description="Full platform access; bypasses subscription checks")
        db.add(admin_role)
        await db.flush()

    if admin_user is None:
        admin_user = User(
            role=admin_role,
            username=admin_email,
            password=hashed_pw,
            register_mfa=True,
        )
        db.add(admin_user)
        await db.commit()
        logger.info("Created default admin user: %s", admin_email)
    else:
        if not admin_user.has_role("admin"):
            admin_user.role = admin_role
        admin_user.password = hashed_pw
        admin_user.register_mfa = True
        await db.commit()
        logger.info("Ensured admin user credentials up to date for: %s", admin_email)



# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_user_by_id_or_none(db: AsyncSession, user_id: int) -> User | None:
    """Fetch a user by PK. Returns None if not found."""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def _get_user_by_id(db: AsyncSession, user_id: int) -> User:
    """Fetch a user by PK. Raises 404 if not found."""
    user = await _get_user_by_id_or_none(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


async def _get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """Fetch a user by username (email). Returns None if not found."""
    result = await db.execute(select(User).where(User.username == username.lower().strip()))
    return result.scalar_one_or_none()


async def _get_role(db: AsyncSession, role_name: str) -> Role | None:
    """Fetch a Role row by name (viewer | member | admin). Returns None if absent."""
    result = await db.execute(select(Role).where(Role.name == role_name))
    return result.scalar_one_or_none()


async def _get_user_by_identifier(
    db: AsyncSession,
    user_id: int | str | None = None,
    email_or_mobile: str | None = None,
) -> User:
    """Fetch a user by ID (int or numeric str) or by username/email (str).

    Raises 404 if user is not found or no identifier was provided.
    """
    user = None

    if user_id is not None:
        if isinstance(user_id, int):
            # Treat 0 as "not provided" — DB IDs start from 1
            if user_id > 0:
                user = await _get_user_by_id_or_none(db, user_id)
        elif isinstance(user_id, str):
            clean_id = user_id.strip()
            if clean_id.isdigit() and int(clean_id) > 0:
                user = await _get_user_by_id_or_none(db, int(clean_id))
            if user is None and clean_id:
                user = await _get_user_by_username(db, clean_id)

    if user is None and email_or_mobile is not None:
        user = await _get_user_by_username(db, email_or_mobile.strip())

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account not found."
        )

    return user


async def _assert_user_active(user: User, db: AsyncSession) -> None:
    """Block sign-in for accounts whose profile_status is Inactive or Suspended.

    Users without a user_details row yet are treated as active (the default).
    This keeps muted accounts from gaining access through any auth path —
    password, login OTP, verify_otp, Google SSO, refresh, or admin login.
    """
    result = await db.execute(select(UserDetails).where(UserDetails.user_id == user.id))
    details = result.scalar_one_or_none()
    if details is None:
        return

    if details.profile_status == "Inactive":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your account is inactive. "
                "Please contact the administrator to reactivate your account."
            ),
        )
    if details.profile_status == "Suspended":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Your account has been suspended. "
                "Please contact support for assistance."
            ),
        )


def _to_register_response(user: User) -> RegisterResponse:
    return RegisterResponse(
        userid=user.id,
        username=user.username,
        registerMFA=user.register_mfa,
    )


async def _issue_token_pair(user: User, db: AsyncSession) -> tuple[str, str]:
    """Create an access token and a rotating, server-revocable refresh session."""
    session_id = str(uuid4())
    token_data = {"sub": str(user.id), "username": user.username, "role": user._primary_role}
    access_token = create_access_token(data=token_data)
    refresh_token = create_refresh_token(data={**token_data, "sid": session_id})
    db.add(RefreshSession(id=session_id, user_id=user.id, expires_at=datetime.now(tz=timezone.utc) + timedelta(days=7)))
    await db.flush()
    return access_token, refresh_token


async def _get_or_create_auth_actions(user_id: int, db: AsyncSession) -> AuthActions:
    """Fetch the auth_actions row for a user, creating it with defaults on first login.

    Defaults:
        payment              = True   (assume payment completed)
        interactive_questions = True  (assume onboarding completed)
    """
    result = await db.execute(select(AuthActions).where(AuthActions.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        row = AuthActions(user_id=user_id, payment=True, interactive_questions=True)
        db.add(row)
        await db.flush()
        logger.info("Created auth_actions row for user_id=%s with defaults.", user_id)
    return row


async def _has_active_payment(user: User, db: AsyncSession) -> bool:
    """Live payment-gate state for the auth response's `payment` flag.

    Reads the single billing chokepoint (`has_active_entitlement`), which checks
    for an entitled Subscription — or returns True for everyone when
    SUBSCRIPTION_ENFORCED is disabled. This replaces the static, always-True
    `auth_actions.payment` column so the frontend's `hasActivePlan` reflects the
    user's real subscription: paid users land on the dashboard, unpaid users are
    routed to pricing. Imported lazily to avoid an import-time service cycle.
    """
    from app.services.billing_service import billing_service

    return await billing_service.has_active_entitlement(user, db)


# ── Auth Service ───────────────────────────────────────────────────────────────

class AuthService:
    """Handles all user authentication operations against the PostgreSQL database."""

    # ── Register ───────────────────────────────────────────────────────────────

    async def register(
        self, request: UserRegisterRequest, db: AsyncSession
    ) -> RegisterResponse:
        """Create a new user, generate OTP, and trigger the OTP email.

        If an account with the same email already exists but OTP verification
        has not been completed (register_mfa=False), a fresh OTP is generated
        and re-dispatched so the user can resume the verification flow.

        Raises:
            HTTPException 409 if the account is already fully registered
                          (register_mfa=True).
        """
        if isinstance(request, dict):
            request = UserRegisterRequest(**request)

        username = request.username.lower().strip()

        existing = await _get_user_by_username(db, username)
        if existing is not None:
            # ── Account exists but OTP verification is still pending ──────────
            if not existing.register_mfa:
                logger.info(
                    "Re-registration attempt for unverified account: %s (id=%s). "
                    "Refreshing OTP and redirecting to verification flow.",
                    username, existing.id,
                )
                # Regenerate a fresh OTP so the previous (possibly expired) one
                # is replaced and the user can complete verification.
                otp = generate_otp()
                expiry = otp_expiry()
                existing.register_otp = otp
                existing.register_otp_expiry = expiry
                existing.register_otp_attempts = 0
                await db.flush()

                result = await dispatch_otp(username, otp)
                if not result.success:
                    logger.warning(
                        "OTP re-dispatch failed for pending account %s: %s",
                        username, result.error,
                    )
                    # Do not raise — client can call /resendOTP.

                return _to_register_response(existing)

            # ── Account is fully registered ───────────────────────────────────
            logger.warning("Duplicate registration attempt for verified account: %s", username)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists.",
            )

        otp = generate_otp()
        expiry = otp_expiry()

        # Default role for new sign-ups is "viewer" (upgraded to "member" on payment)
        default_role = await _get_role(db, "viewer")
        user = User(
            role=default_role,
            username=username,
            password=await hash_password_async(request.password),
            register_otp=otp,
            register_otp_expiry=expiry,
            register_mfa=False,
        )
        db.add(user)
        await db.flush()        # Flush to get the auto-generated id
        await db.refresh(user)  # Populate id from DB

        logger.info("New user registered: %s (id=%s)", username, user.id)

        # Dispatch OTP — if delivery fails we still commit the user record
        # so they can use /resendOTP without re-registering.
        result = await dispatch_otp(username, otp)
        if not result.success:
            logger.warning(
                "OTP dispatch failed after registration for %s: %s",
                username, result.error
            )
            # Do not raise — user is saved; client can call /resendOTP.

        return _to_register_response(user)

    async def refresh(self, request: RefreshTokenRequest, db: AsyncSession) -> RefreshTokenResponse:
        """Rotate a valid refresh token and invalidate the token that was presented."""
        try:
            payload = verify_refresh_token(request.refresh_token)
            user_id = int(payload["sub"])
            session_id = str(payload["sid"])
        except (ValueError, TypeError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token.", headers={"WWW-Authenticate": "Bearer"})

        session = await db.get(RefreshSession, session_id)
        now = datetime.now(tz=timezone.utc)
        if session is None or session.user_id != user_id or session.revoked_at is not None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token is no longer valid.")
        expiry = session.expires_at.replace(tzinfo=timezone.utc) if session.expires_at.tzinfo is None else session.expires_at
        if expiry <= now:
            session.revoked_at = now
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token has expired.")

        user = await _get_user_by_id(db, user_id)
        if not user.register_mfa:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account is not active.")

        # Inactive / suspended accounts must not be able to keep refreshing tokens.
        await _assert_user_active(user, db)

        session.revoked_at = now
        access_token, refresh_token = await _issue_token_pair(user, db)
        return RefreshTokenResponse(data=RefreshTokenData(accessToken=access_token, refreshToken=refresh_token))

    async def logout(self, user: User, db: AsyncSession) -> LogoutResponse:
        """Revoke every refresh session owned by the authenticated user."""
        await db.execute(update(RefreshSession).where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None)).values(revoked_at=datetime.now(tz=timezone.utc)))
        logger.info("Logged out user id=%s and revoked all refresh sessions.", user.id)
        return LogoutResponse()

    # ── Verify OTP ─────────────────────────────────────────────────────────────

    async def verify_otp(
        self, request: VerifyOTPRequest, db: AsyncSession
    ) -> VerifyOTPResponse:
        """Validate the OTP for a given flow ("register" or "login").

        Returns a VerifyOTPResponse with status "success" or "failed".
        On success: sets registerMFA=True (for register) and includes a signed JWT.
        On failure: returns HTTP 200 with status="failed" and an error message
                    (keeps user on MFA page for retry).
        """
        user = await _get_user_by_identifier(
            db, user_id=request.id, email_or_mobile=request.emailOrMobile
        )

        flow = request.flow.lower().strip() if request.flow else "register"
        now = datetime.now(tz=timezone.utc)

        if flow == "login":
            if not user.register_mfa:
                return VerifyOTPResponse(
                    status="failed",
                    message="Account not verified. Please complete registration OTP verification."
                )

            expiry = user.login_otp_expiry
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            if user.login_otp is None:
                logger.warning("No active login OTP found for user id=%s", user.id)
                return VerifyOTPResponse(status="failed", message="OTP is wrong")

            if expiry is None or now > expiry:
                logger.warning("Expired login OTP attempt for user id=%s", user.id)
                user.login_otp = None
                user.login_otp_expiry = None
                return VerifyOTPResponse(status="failed", message="OTP is expired !")

            if user.login_otp != request.otp:
                logger.warning("Wrong login OTP attempt for user id=%s", user.id)
                return VerifyOTPResponse(status="failed", message="OTP is wrong")

            # Success
            user.login_otp = None
            user.login_otp_expiry = None

            # Inactive / suspended accounts must not be able to sign in.
            await _assert_user_active(user, db)

            access_token, refresh_token = await _issue_token_pair(user, db)
            logger.info("Login OTP verified via verify_otp for user id=%s (%s)", user.id, user.username)

            from app.services.user_details_service import user_details_service
            await user_details_service.touch_last_login(user.id, db)

            auth_actions_row = await _get_or_create_auth_actions(user.id, db)
            actions = ["dashboard"] if user.has_role("admin") else []
            return VerifyOTPResponse(
                status="success",
                message="Login OTP Validated Successfully !",
                access_token=access_token,
                refresh_token=refresh_token,
                token_type="bearer",
                expires_in_minutes=_ACCESS_TOKEN_MINS,
                role=user._primary_role,
                actions=actions,
                auth_actions=AuthActionsData(
                    payment=await _has_active_payment(user, db),
                    interactive_questions=auth_actions_row.interactive_questions,
                ),
            )

        else:  # flow == "register"
            expiry = user.register_otp_expiry
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            if user.register_otp is None:
                logger.warning("No active register OTP found for user id=%s", user.id)
                return VerifyOTPResponse(status="failed", message="OTP is wrong")

            if expiry is None or now > expiry:
                logger.warning("Expired register OTP attempt for user id=%s", user.id)
                user.register_otp = None
                user.register_otp_expiry = None
                user.register_otp_attempts = 0
                return VerifyOTPResponse(status="failed", message="OTP is expired !")

            if user.register_otp != request.otp:
                user.register_otp_attempts += 1
                logger.warning(
                    "Wrong register OTP attempt %s/3 for user id=%s",
                    user.register_otp_attempts, user.id
                )
                if user.register_otp_attempts >= 3:
                    user.register_otp = None
                    user.register_otp_expiry = None
                    user.register_otp_attempts = 0
                    return VerifyOTPResponse(
                        status="failed",
                        message="Too many failed attempts. OTP has been invalidated. Please resend OTP."
                    )
                return VerifyOTPResponse(status="failed", message="OTP is wrong")

            user.register_mfa = True
            user.register_otp = None
            user.register_otp_expiry = None
            user.register_otp_attempts = 0

            access_token, refresh_token = await _issue_token_pair(user, db)
            logger.info("Register OTP verified for user id=%s (%s)", user.id, user.username)

            # Fire-and-forget welcome email — never blocks or fails this request.
            enqueue_email_job(
                "registration_success",
                to_email=user.username,
                display_name=user.display_name,
            )

            auth_actions_row = await _get_or_create_auth_actions(user.id, db)
            actions = ["dashboard"] if user.has_role("admin") else []
            return VerifyOTPResponse(
                status="success",
                message="OTP Validated Successfully !",
                access_token=access_token,
                refresh_token=refresh_token,
                token_type="bearer",
                expires_in_minutes=_ACCESS_TOKEN_MINS,
                role=user._primary_role,
                actions=actions,
                auth_actions=RegisterAuthActionsData(
                    interactive_questions=auth_actions_row.interactive_questions,
                ),
            )

    # ── Resend OTP ─────────────────────────────────────────────────────────────

    async def resend_otp(
        self, request: ResendOTPRequest, db: AsyncSession
    ) -> RegisterResponse:
        """Generate a fresh OTP for register or login flow, update DB, and resend OTP.

        Flows:
          - "register": Resends registration MFA OTP (requires register_mfa=False).
          - "login":    Resends login MFA OTP (requires register_mfa=True).

        Raises:
            HTTPException 404 if the user is not found.
            HTTPException 400 if register MFA is already completed (for flow="register").
            HTTPException 403 if register MFA is NOT completed (for flow="login").
        """
        user = await _get_user_by_identifier(
            db, user_id=request.id, email_or_mobile=request.emailOrMobile
        )

        flow = request.flow.lower().strip() if request.flow else "register"

        if flow == "login":
            if not user.register_mfa:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Account not verified. Please complete registration OTP verification.",
                )

            otp = generate_otp()
            expiry = otp_expiry()
            user.login_otp = otp
            user.login_otp_expiry = expiry
            logger.info("Login OTP regenerated for user id=%s (%s)", user.id, user.username)
            await db.flush()

            result = await dispatch_login_otp(user.username, otp)
            if not result.success:
                logger.error(
                    "Resend login OTP dispatch failed for user id=%s: %s", user.id, result.error
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=result.error or "Failed to deliver login OTP. Please try again.",
                )

        else:  # flow == "register"
            if user.register_mfa:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="MFA already verified for this account.",
                )

            otp = generate_otp()
            expiry = otp_expiry()

            user.register_otp = otp
            user.register_otp_expiry = expiry
            user.register_otp_attempts = 0

            logger.info("Register OTP regenerated for user id=%s (%s)", user.id, user.username)
            await db.flush()

            result = await dispatch_otp(user.username, otp)
            if not result.success:
                logger.error(
                    "Resend register OTP dispatch failed for user id=%s: %s", user.id, result.error
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=result.error or "Failed to deliver OTP. Please try again.",
                )

        return _to_register_response(user)

    # ── Login ──────────────────────────────────────────────────────────────────

    async def login(
        self, request: UserLoginRequest, db: AsyncSession
    ) -> LoginSuccessResponse:
        """Authenticate a user and issue access/refresh tokens directly.

        Raises:
            HTTPException 401 if credentials are invalid.
            HTTPException 403 if MFA has not been completed yet.
        """
        username = request.username.lower().strip()
        user = await _get_user_by_username(db, username)

        # Deliberately generic error — do not reveal whether the username exists.
        if user is None or not await verify_password_async(request.password, user.password):
            logger.warning("Failed login attempt for username: %s", username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not user.register_mfa:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account not verified. Please complete OTP verification.",
            )

        # Inactive / suspended accounts must not be able to sign in.
        await _assert_user_active(user, db)

        access_token, refresh_token = await _issue_token_pair(user, db)

        from app.services.user_details_service import user_details_service
        await user_details_service.touch_last_login(user.id, db)

        logger.info("Login successful for user id=%s (%s). Tokens issued.", user.id, user.username)

        auth_actions_row = await _get_or_create_auth_actions(user.id, db)
        actions = ["dashboard"] if user.has_role("admin") else []
        return LoginSuccessResponse(
            status="success",
            message="Login successful.",
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in_minutes=_ACCESS_TOKEN_MINS,
            role=user._primary_role,
            actions=actions,
            auth_actions=AuthActionsData(
                payment=await _has_active_payment(user, db),
                interactive_questions=auth_actions_row.interactive_questions,
            ),
        )


    # ── Verify Login ───────────────────────────────────────────────────────────

    async def verify_login(
        self, request: VerifyLoginRequest, db: AsyncSession
    ) -> VerifyLoginResponse:
        """Verify the login OTP, invalidate it, and issue an access/refresh token pair.

        Raises:
            HTTPException 404 if the account is not found/unverified.
            HTTPException 400 if the code is invalid or expired.
        """
        username = request.emailOrMobile.lower().strip()
        user = await _get_user_by_username(db, username)

        if user is None or not user.register_mfa:
            logger.warning("Verify login OTP attempted for non-existent or unverified user: %s", username)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account not found."
            )

        # Check if OTP exists
        if user.login_otp is None:
            logger.warning("No active login OTP found for user id=%s", user.id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification code."
            )

        # Check expiration
        now = datetime.now(tz=timezone.utc)
        expiry = user.login_otp_expiry
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if expiry is None or now > expiry:
            logger.warning("Expired login OTP attempt for user id=%s", user.id)
            user.login_otp = None
            user.login_otp_expiry = None
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification code."
            )

        # Validate code match
        if user.login_otp != request.otp:
            logger.warning("Wrong login OTP entered for user id=%s", user.id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification code."
            )

        # Success! Clear OTP fields
        user.login_otp = None
        user.login_otp_expiry = None

        # Inactive / suspended accounts must not be able to sign in.
        await _assert_user_active(user, db)

        # Generate tokens
        access_token, refresh_token = await _issue_token_pair(user, db)

        from app.services.user_details_service import user_details_service
        await user_details_service.touch_last_login(user.id, db)

        logger.info("Login OTP successfully verified for user id=%s. Access and Refresh tokens issued.", user.id)

        auth_actions_row = await _get_or_create_auth_actions(user.id, db)
        actions = []
        return VerifyLoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in_minutes=_ACCESS_TOKEN_MINS,
            role=user._primary_role,
            actions=actions,
            auth_actions=AuthActionsData(
                payment=await _has_active_payment(user, db),
                interactive_questions=auth_actions_row.interactive_questions,
            ),
        )

    # ── Google Sign-In ───────────────────────────────────────────────────────

    async def google_sign_in(
        self, request: GoogleAuthRequest, db: AsyncSession
    ) -> GoogleAuthResponse:
        """Authenticate a user from a Google Identity Services ID token.

        Verifies the token against Google's keys, then resolves the account:
          1. by Google subject id (returning users),
          2. else by verified email → links Google to an existing local account,
          3. else creates a fresh Google-backed account (MFA pre-completed).

        Google has already verified the email, so no OTP step is required.

        Raises:
            HTTPException 401 if the Google credential is missing or invalid.
        """
        try:
            claims = await verify_google_id_token(request.credential)
        except GoogleTokenError as exc:
            logger.warning("Rejected Google sign-in: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            )

        google_sub = str(claims["sub"])
        email = str(claims["email"]).lower().strip()
        is_new_user = False

        # 1. Returning Google user — look up by the stable subject id.
        result = await db.execute(select(User).where(User.google_sub == google_sub))
        user = result.scalar_one_or_none()

        # 2. First Google sign-in for an email that already has a local account:
        #    link the two (Google has verified the email, so this is safe).
        if user is None:
            user = await _get_user_by_username(db, email)
            if user is not None:
                user.google_sub = google_sub
                if not user.register_mfa:
                    # Google verified the email — treat the account as active.
                    user.register_mfa = True
                logger.info("Linked Google identity to existing account id=%s (%s).", user.id, email)

        # 3. Brand-new user — provision a Google-backed account.
        if user is None:
            display_name = (claims.get("name") or "").strip() or None
            default_role = await _get_role(db, "viewer")
            user = User(
                role=default_role,
                username=email,
                display_name=display_name,
                password=None,
                auth_provider="google",
                google_sub=google_sub,
                register_mfa=True,
            )
            db.add(user)
            await db.flush()
            await db.refresh(user)
            await self._create_google_user_details(user, claims, db)
            is_new_user = True
            logger.info("Provisioned new Google account id=%s (%s).", user.id, email)

        # Inactive / suspended accounts must not be able to sign in.
        await _assert_user_active(user, db)

        access_token, refresh_token = await _issue_token_pair(user, db)

        from app.services.user_details_service import user_details_service
        await user_details_service.touch_last_login(user.id, db)

        auth_actions_row = await _get_or_create_auth_actions(user.id, db)
        logger.info("Google sign-in successful for user id=%s. Tokens issued.", user.id)

        return GoogleAuthResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in_minutes=_ACCESS_TOKEN_MINS,
            role=user._primary_role,
            actions=[],
            auth_actions=AuthActionsData(
                payment=await _has_active_payment(user, db),
                interactive_questions=auth_actions_row.interactive_questions,
            ),
            is_new_user=is_new_user,
        )

    @staticmethod
    async def _create_google_user_details(user: User, claims: dict, db: AsyncSession) -> None:
        """Seed a user_details profile from Google claims (mobile number left blank)."""
        from app.services.user_details_service import _generate_unique_profile_id

        email = str(claims["email"]).lower().strip()
        first_name = (claims.get("given_name") or "").strip()
        last_name = (claims.get("family_name") or "").strip()
        if not first_name:
            # Fall back to the display name, then the email local-part.
            full_name = (claims.get("name") or "").strip()
            if full_name:
                parts = full_name.split(None, 1)
                first_name = parts[0]
                last_name = last_name or (parts[1] if len(parts) > 1 else "")
            else:
                first_name = email.split("@", 1)[0]

        profile_id = await _generate_unique_profile_id(db)
        db.add(
            UserDetails(
                profile_id=profile_id,
                user_id=user.id,
                first_name=first_name[:100],
                last_name=(last_name or "")[:100],
                email=email,
                mobile_number=None,
            )
        )
        await db.flush()
        logger.info("Seeded user_details profile_id=%s for Google user_id=%s.", profile_id, user.id)

    # ── Admin Login ────────────────────────────────────────────────────────────

    async def admin_login(
        self, request: UserLoginRequest, db: AsyncSession
    ) -> AdminLoginResponse:
        """Authenticate an admin user and issue access/refresh tokens directly.

        Raises:
            HTTPException 401 if credentials are invalid.
            HTTPException 403 if user is not an admin.
        """
        username = request.username.lower().strip()
        user = await _get_user_by_username(db, username)

        if user is None or not await verify_password_async(request.password, user.password):
            logger.warning("Failed admin login attempt for username: %s", username)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not user.has_role("admin"):
            logger.warning("Non-admin user attempted admin login: %s", username)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied. Admin privileges required.",
            )

        # Inactive / suspended accounts must not be able to sign in.
        await _assert_user_active(user, db)

        access_token, refresh_token = await _issue_token_pair(user, db)

        from app.services.user_details_service import user_details_service
        await user_details_service.touch_last_login(user.id, db)

        logger.info("Admin user logged in successfully: %s (id=%s)", user.username, user.id)

        return AdminLoginResponse(
            status="success",
            message="Admin login successful.",
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in_minutes=_ACCESS_TOKEN_MINS,
            role="admin",
            actions=["dashboard"],
        )

    # ── Forgot Password Request ───────────────────────────────────────────────

    async def forgot_password_request(
        self, request: ForgotPasswordRequest, db: AsyncSession
    ) -> ForgotPasswordResponse:
        """Process a password reset request."""
        username = request.emailOrMobile.lower().strip()
        user = await _get_user_by_username(db, username)

        if user is None or not user.register_mfa:
            logger.info("Forgot password request for non-existent or unverified user: %s", username)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account not found."
            )

        otp = generate_otp()
        expiry = otp_expiry()

        user.forgot_password_otp = otp
        user.forgot_password_otp_expiry = expiry

        logger.info("Forgot password OTP generated for user id=%s (%s)", user.id, user.username)
        await db.flush()

        # Dispatch OTP
        result = await dispatch_password_reset_otp(username, otp)
        if not result.success:
            logger.warning(
                "Forgot password OTP dispatch failed for user id=%s: %s",
                user.id, result.error
            )

        return ForgotPasswordResponse()

    # ── Forgot Password Verify ────────────────────────────────────────────────

    async def forgot_password_verify(
        self, request: ForgotPasswordVerifyRequest, db: AsyncSession
    ) -> ForgotPasswordVerifyResponse:
        """Validate the password reset OTP and return a temporary reset token.

        Raises HTTP 404 if account not found/unverified, or HTTP 400 if OTP is invalid/expired.
        """
        username = request.emailOrMobile.lower().strip()
        user = await _get_user_by_username(db, username)

        if user is None or not user.register_mfa:
            logger.warning("Verify OTP attempted for non-existent or unverified user: %s", username)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account not found."
            )

        # Check if OTP exists
        if user.forgot_password_otp is None:
            logger.warning("No active forgot password OTP found for user id=%s", user.id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification code."
            )

        # Check expiration (timezone-aware comparison)
        now = datetime.now(tz=timezone.utc)
        expiry = user.forgot_password_otp_expiry
        if expiry is not None and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if expiry is None or now > expiry:
            logger.warning("Expired forgot password OTP attempt for user id=%s", user.id)
            # Invalidate/clear the expired code
            user.forgot_password_otp = None
            user.forgot_password_otp_expiry = None
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification code."
            )

        # Validate code match
        if user.forgot_password_otp != request.code:
            logger.warning("Wrong forgot password OTP entered for user id=%s", user.id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification code."
            )

        # Success! Clear code fields and generate temporary token
        user.forgot_password_otp = None
        user.forgot_password_otp_expiry = None

        token = create_password_reset_token(str(user.id), user.username)
        logger.info("Forgot password OTP successfully verified for user id=%s", user.id)

        return ForgotPasswordVerifyResponse(reset_token=token)

    # ── Forgot Password Reset ──────────────────────────────────────────────

    async def forgot_password_reset(
        self, request: ForgotPasswordResetRequest, db: AsyncSession
    ) -> ForgotPasswordResetResponse:
        """Verify the reset token, re-hash the new password, update the DB,
        and stamp password_changed_at to revoke all prior access tokens.

        Raises:
            HTTPException 401 — token invalid, expired, or wrong scope.
            HTTPException 404 — user ID in token not found.
        """
        # 1. Decode and validate the reset token
        try:
            payload = verify_password_reset_token(request.reset_token)
        except ValueError as exc:
            logger.warning("Password reset with invalid token: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired reset token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired reset token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 2. Load user from DB
        result = await db.execute(
            select(User).where(User.id == int(user_id))
        )
        user = result.scalar_one_or_none()
        if user is None or not user.register_mfa:
            logger.warning("Password reset token references non-existent or unverified user id=%s", user_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found."
            )

        token_iat = payload.get("iat")
        password_changed_at = user.password_changed_at
        if password_changed_at is not None and token_iat is not None:
            if password_changed_at.tzinfo is None:
                password_changed_at = password_changed_at.replace(tzinfo=timezone.utc)
            token_issued_at = datetime.fromtimestamp(token_iat, tz=timezone.utc)
            if token_issued_at <= password_changed_at:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired reset token.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        # 3. Hash new password and update record
        user.password = await hash_password_async(request.new_password)

        # 4. Stamp password_changed_at — invalidates all access tokens issued before this moment
        user.password_changed_at = datetime.now(tz=timezone.utc)

        # 5. Generate fresh access and refresh token pair for immediate login
        access_token, refresh_token = await _issue_token_pair(user, db)

        logger.info(
            "Password reset completed for user id=%s. All prior access tokens are now revoked.",
            user.id
        )

        # Fire-and-forget confirmation email — never blocks or fails this request.
        enqueue_email_job(
            "password_reset_success",
            to_email=user.username,
            changed_at=user.password_changed_at,
        )

        actions = ["dashboard"] if user.has_role("admin") else []
        return ForgotPasswordResetResponse(
            status="success",
            message="Password has been reset successfully.",
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in_minutes=_ACCESS_TOKEN_MINS,
            role=user._primary_role,
            actions=actions,
        )

    # ── Change Password (Authenticated/Logged In Users) ───────────────────────

    async def change_password(
        self, user: User, request: ChangePasswordRequest, db: AsyncSession
    ) -> ChangePasswordResponse:
        """Authenticate current password, hash the new password, update PostgreSQL,
        and update password_changed_at to revoke all existing access tokens.
        """
        # 1. Verify current password
        if not await verify_password_async(request.current_password, user.password):
            logger.warning("Failed change password attempt for user id=%s: incorrect current password", user.id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect current password.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 2. Ensure new password is not identical to current password
        if request.new_password == request.current_password or await verify_password_async(request.new_password, user.password):
            logger.warning("Failed change password attempt for user id=%s: new password is identical to current password", user.id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="New password cannot be the same as your current password.",
            )

        # 3. Hash new password
        user.password = await hash_password_async(request.new_password)

        # 4. Stamp password_changed_at — invalidates all other access tokens
        user.password_changed_at = datetime.now(tz=timezone.utc)

        logger.info(
            "Password changed successfully for user id=%s. All other access tokens are now revoked.",
            user.id
        )

        # Fire-and-forget confirmation email — never blocks or fails this request.
        enqueue_email_job(
            "password_reset_success",
            to_email=user.username,
            changed_at=user.password_changed_at,
        )

        return ChangePasswordResponse()


# ── Module-level singleton ─────────────────────────────────────────────
auth_service = AuthService()