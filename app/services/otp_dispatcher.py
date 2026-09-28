"""
app/services/otp_dispatcher.py
────────────────────────────────────────────────────────────────────────────────
Smart OTP dispatcher — reads the user's registration identifier (username) and
routes OTP delivery to the correct channel:

  Channel detection:
    • Email  → contains "@"           → SMTP via email_service
    • Phone  → matches E.164 pattern  → NOT YET IMPLEMENTED (returns failure)
                (future: Twilio / AWS SNS)

  Return value:
    OTPResult(success, channel, error)

  Callers (auth_service) import ONLY dispatch_otp() and never need to know
  which channel was used — the dispatcher handles all routing logic.

Acceptance Criteria (from spec):
  ✅ Read the user's registration identifier (Email or Mobile)
  ✅ If email → SMTP dispatch
  ✅ If mobile → stub (returns failure with clear message; ready for Twilio)
  ✅ Return success/failure status indicating whether dispatch completed
"""
import logging
import re

from app.services.email_service import (
    OTPResult,
    send_otp_email,
    send_password_reset_email,
    send_login_otp_email,
)

logger = logging.getLogger(__name__)

# ── Channel detection ──────────────────────────────────────────────────────────
# E.164 phone pattern: optional +, 7–15 digits, no spaces or dashes
_PHONE_RE = re.compile(r"^\+?\d{7,15}$")


def _detect_channel(username: str) -> str:
    """Return 'email' or 'phone' based on the username format."""
    if "@" in username:
        return "email"
    if _PHONE_RE.match(username.strip()):
        return "phone"
    # Default to email if ambiguous (validation should catch bad formats earlier)
    return "email"


# ── Public dispatcher ──────────────────────────────────────────────────────────

async def dispatch_otp(username: str, otp: int) -> OTPResult:
    """Dispatch a 6-digit OTP to the user via the appropriate channel.

    Args:
        username: The user's registration identifier (email or E.164 phone).
        otp:      The 6-digit OTP to send.

    Returns:
        OTPResult(success, channel, error)
        - success=True  → OTP was delivered successfully
        - success=False → delivery failed; `error` contains the reason

    Notes:
        - SMS channel is stubbed — returns a clear failure message until
          Twilio / AWS SNS integration is added.
        - Email failures are non-fatal for registration (user can resend).
        - The caller decides whether to surface the failure to the client.
    """
    channel = _detect_channel(username)
    logger.info("==================================================")
    logger.info("🔑 DEV MODE OTP CODE for %s: %s", username, otp)
    logger.info("==================================================")
    logger.info("OTP dispatch → channel=%s for identifier=%s", channel, _mask(username))

    if channel == "email":
        result = await send_otp_email(username, otp)

    elif channel == "phone":
        # ── SMS stub ────────────────────────────────────────────────────────────
        # Replace this block with Twilio / AWS SNS integration when ready.
        logger.warning(
            "SMS OTP dispatch is not yet configured. "
            "Phone username: %s", _mask(username)
        )
        result = OTPResult(
            success=False,
            channel="sms",
            error=(
                "SMS delivery is not yet configured. "
                "Please use an email address to register."
            ),
        )

    else:
        result = OTPResult(
            success=False,
            channel="unknown",
            error=f"Unrecognised identifier format: {_mask(username)}",
        )

    if result.success:
        logger.info("OTP dispatch SUCCESS → channel=%s", result.channel)
    else:
        logger.warning(
            "OTP dispatch FAILED → channel=%s | error=%s", result.channel, result.error
        )

    return result


# ── Utility ────────────────────────────────────────────────────────────────────

def _mask(identifier: str) -> str:
    """Mask sensitive identifiers for safe logging (show first 3 + last 2 chars)."""
    if len(identifier) <= 6:
        return "***"
    return identifier[:3] + "***" + identifier[-2:]


async def dispatch_password_reset_otp(username: str, otp: int) -> OTPResult:
    """Dispatch a 6-digit password reset OTP via the appropriate channel."""
    channel = _detect_channel(username)
    logger.info("==================================================")
    logger.info("🔑 DEV MODE OTP CODE for %s: %s", username, otp)
    logger.info("==================================================")
    logger.info("Password reset OTP dispatch → channel=%s for identifier=%s", channel, _mask(username))

    if channel == "email":
        result = await send_password_reset_email(username, otp)

    elif channel == "phone":
        # ── SMS stub ────────────────────────────────────────────────────────────
        logger.warning(
            "SMS OTP dispatch is not yet configured. "
            "Phone username: %s", _mask(username)
        )
        result = OTPResult(
            success=False,
            channel="sms",
            error=(
                "SMS delivery is not yet configured. "
                "Please use an email address."
            ),
        )

    else:
        result = OTPResult(
            success=False,
            channel="unknown",
            error=f"Unrecognised identifier format: {_mask(username)}",
        )

    if result.success:
        logger.info("Password reset OTP dispatch SUCCESS → channel=%s", result.channel)
    else:
        logger.warning(
            "Password reset OTP dispatch FAILED → channel=%s | error=%s", result.channel, result.error
        )

    return result


async def dispatch_login_otp(username: str, otp: int) -> OTPResult:
    """Dispatch a 6-digit login OTP via the appropriate channel."""
    channel = _detect_channel(username)
    logger.info("==================================================")
    logger.info("🔑 DEV MODE OTP CODE for %s: %s", username, otp)
    logger.info("==================================================")
    logger.info("Login OTP dispatch → channel=%s for identifier=%s", channel, _mask(username))

    if channel == "email":
        result = await send_login_otp_email(username, otp)

    elif channel == "phone":
        # ── SMS stub ────────────────────────────────────────────────────────────
        logger.warning(
            "SMS OTP dispatch is not yet configured. Phone username: %s", _mask(username)
        )
        result = OTPResult(
            success=False,
            channel="sms",
            error=(
                "SMS delivery is not yet configured. Please use an email address."
            ),
        )

    else:
        result = OTPResult(
            success=False,
            channel="unknown",
            error=f"Unrecognised identifier format: {_mask(username)}",
        )

    if result.success:
        logger.info("Login OTP dispatch SUCCESS → channel=%s", result.channel)
    else:
        logger.warning(
            "Login OTP dispatch FAILED → channel=%s | error=%s", result.channel, result.error
        )

    return result

