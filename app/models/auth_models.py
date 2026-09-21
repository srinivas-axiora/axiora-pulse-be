"""
app/models/auth_models.py
────────────────────────────────────────────────────────────────────────────────
Pydantic request/response models for all authentication endpoints.

Endpoints covered:
  POST /register    → UserRegisterRequest  → RegisterResponse
  POST /verifyOTP   → VerifyOTPRequest     → VerifyOTPResponse
  POST /resendOTP   → ResendOTPRequest     → RegisterResponse
  POST /login       → UserLoginRequest     → LoginSuccessResponse
"""
from datetime import date, datetime
from typing import List, Literal, Optional, Union

from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator, model_validator


# ── Request Models ─────────────────────────────────────────────────────────────

class UserRegisterRequest(BaseModel):
    """Payload for POST /api/v1/auth/register."""
    username: EmailStr = Field(validation_alias=AliasChoices("username", "email"))          # email address used as the unique username
    password: str

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        """Enforce password policy using standard Python str methods only."""
        errors = []

        if len(value) < 8:
            errors.append("at least 8 characters")

        if not any(c.isupper() for c in value):
            errors.append("at least one uppercase letter")

        if not any(c.islower() for c in value):
            errors.append("at least one lowercase letter")

        if not any(c.isdigit() for c in value):
            errors.append("at least one digit")

        _special = set("!@#$%^&*()_+-=[]{}|;':\",./<>?")
        if not any(c in _special for c in value):
            errors.append("at least one special character (!@#$%^&*...)")

        if errors:
            raise ValueError("Password must contain: " + ", ".join(errors))

        return value


class UserLoginRequest(BaseModel):
    """Payload for POST /api/v1/auth/login."""
    username: EmailStr = Field(validation_alias=AliasChoices("username", "email"))
    password: str


class VerifyOTPRequest(BaseModel):
    """Payload for POST /api/v1/auth/verifyOTP."""
    id: Optional[Union[int, str]] = Field(
        default=None,
        validation_alias=AliasChoices("id", "userid")
    )
    emailOrMobile: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("emailOrMobile", "email", "username", "mobile")
    )
    otp: int
    flow: Literal["register", "login"] = "register"

    @model_validator(mode="after")
    def require_identifier(self) -> "VerifyOTPRequest":
        """Ensure at least one valid identifier (non-zero id or emailOrMobile) is provided."""
        id_valid = self.id is not None and str(self.id).strip() not in ("", "0")
        email_valid = bool(self.emailOrMobile and self.emailOrMobile.strip())
        if not id_valid and not email_valid:
            raise ValueError(
                "Provide either 'id' (non-zero user ID) or 'emailOrMobile' to identify the user."
            )
        return self


class ResendOTPRequest(BaseModel):
    """Payload for POST /api/v1/auth/resendOTP."""
    id: Optional[Union[int, str]] = Field(
        default=None,
        validation_alias=AliasChoices("id", "userid")
    )
    emailOrMobile: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("emailOrMobile", "email", "username", "mobile")
    )
    flow: Literal["register", "login"] = "register"

    @model_validator(mode="after")
    def require_identifier(self) -> "ResendOTPRequest":
        """Ensure at least one valid identifier (non-zero id or emailOrMobile) is provided."""
        id_valid = self.id is not None and str(self.id).strip() not in ("", "0")
        email_valid = bool(self.emailOrMobile and self.emailOrMobile.strip())
        if not id_valid and not email_valid:
            raise ValueError(
                "Provide either 'id' (non-zero user ID) or 'emailOrMobile'/'email' to identify the user."
            )
        return self


# ── Response Models ────────────────────────────────────────────────────────────

class AuthActionsData(BaseModel):
    """Post-login gate flags returned on every successful regular-user login.

    payment              – True  when the user has completed payment (default: True)
    interactive_questions – True  when the user has answered onboarding questions (default: True)
    """
    payment: bool = False
    interactive_questions: bool = True


class RegisterAuthActionsData(BaseModel):
    """Post-registration gate flags returned on OTP verification for the register flow.

    interactive_questions – True  when the user has answered onboarding questions (default: True)
    """
    interactive_questions: bool = True


class RegisterResponse(BaseModel):
    """Returned after successful registration or OTP resend."""
    userid: int
    username: str
    registerMFA: bool


class VerifyOTPResponse(BaseModel):
    """Returned after OTP verification attempt."""
    status: str                              # "success" | "failed"
    message: str
    access_token: Optional[str] = None      # Present only on success
    refresh_token: Optional[str] = None     # Present only on success
    token_type: str = "bearer"
    expires_in_minutes: Optional[int] = None
    role: Optional[str] = None              # Present only on success
    actions: List[str] = Field(default_factory=list)  # Present only on success
    auth_actions: Optional[Union[AuthActionsData, RegisterAuthActionsData]] = None    # Post-login/register gate flags (regular users only)


class LoginSuccessResponse(BaseModel):
    """Returned on successful login."""
    status: str = "success"
    message: str = "Login successful."
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    role: str = "user"
    actions: List[str] = Field(default_factory=list)
    auth_actions: Optional[AuthActionsData] = None    # Post-login gate flags (regular users only)



class ForgotPasswordRequest(BaseModel):
    """Payload for POST /api/v1/auth/forgot-password/request."""
    emailOrMobile: str = Field(validation_alias=AliasChoices("emailOrMobile", "email", "username", "mobile"))


class ForgotPasswordResponse(BaseModel):
    """Returned after forgot password reset request is processed."""
    status: str = "success"
    message: str = "Password reset code has been sent."


class ForgotPasswordVerifyRequest(BaseModel):
    """Payload for POST /api/v1/auth/forgot-password/verify."""
    emailOrMobile: str = Field(validation_alias=AliasChoices("emailOrMobile", "email", "username", "mobile"))
    code: int


class ForgotPasswordVerifyResponse(BaseModel):
    """Returned after successful password reset verification."""
    status: str = "success"
    message: str = "Code verified successfully."
    reset_token: str


class ForgotPasswordResetRequest(BaseModel):
    """Payload for POST /api/v1/auth/forgot-password/reset."""
    reset_token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        """Enforce the same password policy as registration."""
        errors = []

        if len(value) < 8:
            errors.append("at least 8 characters")

        if not any(c.isupper() for c in value):
            errors.append("at least one uppercase letter")

        if not any(c.islower() for c in value):
            errors.append("at least one lowercase letter")

        if not any(c.isdigit() for c in value):
            errors.append("at least one digit")

        _special = set("!@#$%^&*()_+-=[]{}|;':\",.//<>?")
        if not any(c in _special for c in value):
            errors.append("at least one special character (!@#$%^&*...)")

        if errors:
            raise ValueError("Password must contain: " + ", ".join(errors))

        return value


class ForgotPasswordResetResponse(BaseModel):
    """Returned after a successful password reset."""
    status: str = "success"
    message: str = "Password has been reset successfully."
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in_minutes: Optional[int] = None
    role: Optional[str] = "user"
    actions: List[str] = Field(default_factory=list)


class ChangePasswordRequest(BaseModel):
    """Payload for POST /api/v1/auth/change-password."""
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, value: str) -> str:
        """Enforce standard password security requirements."""
        errors = []

        if len(value) < 8:
            errors.append("at least 8 characters")

        if not any(c.isupper() for c in value):
            errors.append("at least one uppercase letter")

        if not any(c.islower() for c in value):
            errors.append("at least one lowercase letter")

        if not any(c.isdigit() for c in value):
            errors.append("at least one digit")

        _special = set("!@#$%^&*()_+-=[]{}|;':\",.//<>?")
        if not any(c in _special for c in value):
            errors.append("at least one special character (!@#$%^&*...)")

        if errors:
            raise ValueError("Password must contain: " + ", ".join(errors))

        return value

    @model_validator(mode="after")
    def verify_passwords_differ(self) -> "ChangePasswordRequest":
        """Ensure the new password is not identical to the current password."""
        if self.current_password and self.new_password:
            if self.current_password == self.new_password:
                raise ValueError("New password cannot be the same as your current password.")
        return self


class ChangePasswordResponse(BaseModel):
    """Returned after a successful password change."""
    status: str = "success"
    message: str = "Password changed successfully. Your other sessions have been logged out."


class LoginOTPResponse(BaseModel):
    """Returned after credentials are validated and login OTP is dispatched."""
    status: str = "success"
    message: str = "A login verification code has been sent."
    userid: Optional[int] = None   # Returned so the client can use it for /resendOTP


class GoogleAuthRequest(BaseModel):
    """Payload for POST /api/v1/auth/google.

    ``credential`` is the ID token (a signed JWT) returned by Google Identity
    Services on the frontend. It is accepted under several common field names.
    """
    credential: str = Field(
        validation_alias=AliasChoices("credential", "id_token", "idToken", "token")
    )


class VerifyLoginRequest(BaseModel):
    """Payload for POST /api/v1/auth/verify-login."""
    emailOrMobile: str = Field(validation_alias=AliasChoices("emailOrMobile", "email", "username", "mobile"))
    otp: int


class VerifyLoginResponse(BaseModel):
    """Returned on successful login verification."""
    status: str = "success"
    message: str = "Login successful."
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    role: str = "user"
    actions: List[str] = Field(default_factory=list)
    auth_actions: Optional[AuthActionsData] = None    # Post-login gate flags (regular users only)


class GoogleAuthResponse(BaseModel):
    """Returned on successful Google sign-in.

    Mirrors VerifyLoginResponse so the frontend can treat both the same way,
    plus ``is_new_user`` to let the client tailor the post-auth experience
    (e.g. route a first-time user through onboarding).
    """
    status: str = "success"
    message: str = "Login successful."
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    role: str = "user"
    actions: List[str] = Field(default_factory=list)
    auth_actions: Optional[AuthActionsData] = None
    is_new_user: bool = False


class AdminLoginResponse(BaseModel):
    """Returned on successful admin login."""
    status: str = "success"
    message: str = "Admin login successful."
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    role: str = "admin"
    actions: List[str] = Field(default_factory=lambda: ["dashboard"])


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(validation_alias=AliasChoices("refreshToken", "refresh_token"))


class RefreshTokenData(BaseModel):
    accessToken: str
    refreshToken: str


class RefreshTokenResponse(BaseModel):
    data: RefreshTokenData


class LogoutResponse(BaseModel):
    status: str = "success"
    message: str = "Logged out successfully."


class CurrentUserResponse(BaseModel):
    id: str
    email: str
    name: str
    avatarUrl: Optional[str] = None
    role: str
    createdAt: datetime
    updatedAt: datetime
    # Extended profile fields from user_details — None when the user hasn't
    # created a profile yet (see POST /api/v1/user-details).
    profileId: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    mobileNumber: Optional[str] = None
    dateOfBirth: Optional[date] = None
    gender: Optional[str] = None
    profileStatus: Optional[str] = None
    nationality: Optional[str] = None
    communicationPreferences: Optional[List[str]] = None
    lastLoginDate: Optional[datetime] = None


class UpdateCurrentUserRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    avatarUrl: Optional[str] = None


class CurrentUserEnvelope(BaseModel):
    data: CurrentUserResponse





