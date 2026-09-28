"""
app/services/email_service.py
────────────────────────────────────────────────────────────────────────────────
Email OTP service using Python's built-in smtplib (SMTP / STARTTLS).

Returns an OTPResult dataclass so callers get structured success/failure info
rather than catching raw exceptions.

All blocking SMTP I/O is offloaded to asyncio.to_thread() to keep the
FastAPI event loop non-blocking.

Configuration (from .env):
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
  SMTP_FROM_EMAIL, SMTP_FROM_NAME, OTP_EXPIRE_MINUTES,
  SUPPORT_EMAIL, CONTACT_EMAIL (contact-form recipient)
"""
import asyncio
import html
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from app.services.email_templates import render_button, render_email_shell

load_dotenv()

# ── SMTP environment variable helpers ─────────────────────────────────────────
_SMTP_HOST       = os.getenv("SMTP_HOST", "")
_SMTP_PORT       = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USER       = os.getenv("SMTP_USER") or os.getenv("SMTP_USERNAME", "")
_SMTP_PASSWORD   = os.getenv("SMTP_PASSWORD", "")
_SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL") or _SMTP_USER
_SMTP_FROM_NAME  = os.getenv("SMTP_FROM_NAME", "Axiora Pulse")
_OTP_EXPIRE_MINS = int(os.getenv("OTP_EXPIRE_MINUTES", "10"))
_SUPPORT_EMAIL   = os.getenv("SUPPORT_EMAIL", "no.reply@axiorapulse.com")
_CONTACT_RECIPIENT_EMAIL = os.getenv("CONTACT_EMAIL") or _SUPPORT_EMAIL
_DASHBOARD_LOGIN_URL = os.getenv("DASHBOARD_LOGIN_URL", "https://qa.axiorapulse.com/login")


def _resolve_email_timezone(name: str) -> timezone | ZoneInfo:
    """Resolve the IANA zone name, falling back to a fixed UTC+5:30 offset"""
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logging.getLogger(__name__).warning(
            "Timezone database not found for '%s' (is the 'tzdata' package installed?). "
            "Falling back to a fixed UTC+5:30 offset for email timestamps.", name
        )
        return timezone(timedelta(hours=5, minutes=30))


_EMAIL_TIMEZONE = _resolve_email_timezone(os.getenv("EMAIL_TIMEZONE", "Asia/Kolkata"))

logger = logging.getLogger(__name__)


async def send_ticket_update_email(to_email: str, ticket_id: str, subject: str,
                                   display_name: str, update: str, event_id: str) -> "OTPResult":
    """Send a user-visible ticket update using the existing SMTP transport."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Ticket {ticket_id}: update"
    msg["From"] = f"{_SMTP_FROM_NAME} <{_SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg["Message-ID"] = f"<ticket-{event_id}@axiorapulse.com>"
    plain = f"Hello {display_name},\n\nTicket: {ticket_id}\nSubject: {subject}\n\n{update}\n\nSign in to view your support ticket.\n\nThe Axiora Pulse Team"
    body = f'<tr><td><p>Hello {html.escape(display_name)},</p><p><strong>{html.escape(ticket_id)}</strong>: {html.escape(subject)}</p><p style="white-space:pre-wrap">{html.escape(update)}</p><p>Sign in to view your support ticket.</p></td></tr>'
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(render_email_shell(preheader="Your support ticket has been updated", body_html=body), "html", "utf-8"))
    try:
        await asyncio.to_thread(_smtp_send, to_email, msg)
        return OTPResult(success=True, channel="email")
    except Exception as exc:
        return OTPResult(success=False, channel="email", error=str(exc))


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class OTPResult:
    """Structured result returned from every OTP dispatch attempt."""
    success: bool
    channel: str            # "email" | "sms" (future)
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.success:
            return f"OTPResult(success=True, channel={self.channel!r})"
        return f"OTPResult(success=False, channel={self.channel!r}, error={self.error!r})"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _build_otp_email(to_email: str, otp: int) -> MIMEMultipart:
    """Construct the branded OTP email (plain-text + HTML multipart)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Axiora Pulse Verification Code"
    msg["From"] = f"{_SMTP_FROM_NAME} <{_SMTP_FROM_EMAIL}>"
    msg["To"] = to_email

    plain_body = (
        f"Hello,\n\n"
        f"Your Axiora Pulse verification code is: {otp}\n\n"
        f"This code is valid for {_OTP_EXPIRE_MINS} minutes.\n"
        f"If you did not request this, please ignore this email.\n\n"
        f"— The Axiora Pulse Team"
    )

    html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:40px 0;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:16px;padding:48px 40px;
                    box-shadow:0 4px 24px rgba(0,0,0,0.08);">
        <tr>
          <td style="text-align:center;padding-bottom:8px;">
            <span style="font-size:22px;font-weight:700;color:#1a1a2e;letter-spacing:-0.5px;">
              Axiora Pulse
            </span>
          </td>
        </tr>
        <tr>
          <td style="text-align:center;padding-top:8px;padding-bottom:32px;">
            <p style="margin:0;color:#555;font-size:15px;">
              Use the code below to verify your account.
            </p>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-bottom:32px;">
            <div style="display:inline-block;background:#f5f3ff;border-radius:12px;
                        padding:20px 40px;">
              <span style="font-size:48px;font-weight:800;letter-spacing:14px;color:#4f46e5;">
                {otp}
              </span>
            </div>
          </td>
        </tr>
        <tr>
          <td style="text-align:center;">
            <p style="margin:0;color:#888;font-size:13px;line-height:1.6;">
              This code expires in <strong>{_OTP_EXPIRE_MINS} minutes</strong>.<br>
              If you didn&rsquo;t request this, you can safely ignore this email.
            </p>
          </td>
        </tr>
        <tr>
          <td style="text-align:center;padding-top:32px;border-top:1px solid #f0f0f0;margin-top:32px;">
            <p style="margin:0;color:#bbb;font-size:12px;">
              &copy; 2025 Axiora Pulse. All rights reserved.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


def _smtp_send(to_email: str, msg: MIMEMultipart) -> None:
    """Blocking SMTP send supporting SSL (Port 465) and STARTTLS (Port 587/25)."""
    if _SMTP_PORT == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(_SMTP_HOST, _SMTP_PORT, context=context, timeout=30) as server:
            server.login(_SMTP_USER, _SMTP_PASSWORD)
            server.sendmail(_SMTP_FROM_EMAIL, [to_email], msg.as_string())
    else:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(_SMTP_USER, _SMTP_PASSWORD)
            server.sendmail(_SMTP_FROM_EMAIL, [to_email], msg.as_string())
    logger.info("OTP email dispatched via SMTP (%s:%s) → %s", _SMTP_HOST, _SMTP_PORT, to_email)


def _build_contact_email(name: str, email: str, topic: str, message: str) -> MIMEMultipart:
    """Construct the 'contact us' email forwarded to the support inbox."""
    safe_name = html.escape(name)
    safe_email = html.escape(email, quote=True)
    safe_topic = html.escape(topic)
    safe_message = html.escape(message)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"New Contact Request: {topic}"
    msg["From"] = f"{_SMTP_FROM_NAME} <{_SMTP_FROM_EMAIL}>"
    msg["To"] = _CONTACT_RECIPIENT_EMAIL
    msg["Reply-To"] = email

    plain_body = (
        f"A new contact request was submitted from the website.\n\n"
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Topic: {topic}\n\n"
        f"Message:\n{message}\n"
    )

    body_html = f"""\
        <tr>
          <td align="center" style="padding-bottom:8px;">
            <h1 class="text-primary" style="margin:0;font-size:22px;font-weight:700;color:#1a1a2e;letter-spacing:-0.5px;">
              New Contact Request
            </h1>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-top:8px;padding-bottom:20px;">
            <p class="text-secondary" style="margin:0;color:#555;font-size:15px;line-height:1.6;">
              A visitor submitted the following details from the website.
            </p>
          </td>
        </tr>
        <tr>
          <td style="padding-bottom:24px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                   style="border:1px solid #eeeeee;border-radius:12px;overflow:hidden;">
              <tr>
                <td style="padding:14px 20px;background:#f9fafb;width:120px;">
                  <span class="text-secondary" style="color:#555;font-size:13px;font-weight:700;">Name</span>
                </td>
                <td style="padding:14px 20px;"><span style="color:#1a1a2e;font-size:14px;">{safe_name}</span></td>
              </tr>
              <tr>
                <td style="padding:14px 20px;background:#f9fafb;">
                  <span class="text-secondary" style="color:#555;font-size:13px;font-weight:700;">Email</span>
                </td>
                <td style="padding:14px 20px;">
                  <a href="mailto:{safe_email}" style="color:#4f46e5;font-size:14px;">{safe_email}</a>
                </td>
              </tr>
              <tr>
                <td style="padding:14px 20px;background:#f9fafb;">
                  <span class="text-secondary" style="color:#555;font-size:13px;font-weight:700;">Topic</span>
                </td>
                <td style="padding:14px 20px;"><span style="color:#1a1a2e;font-size:14px;">{safe_topic}</span></td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-bottom:12px;">
            <h2 style="margin:0;font-size:15px;font-weight:700;color:#1a1a2e;">Message</h2>
          </td>
        </tr>
        <tr>
          <td style="padding-bottom:28px;">
            <div style="background:#f9fafb;border:1px solid #eeeeee;border-radius:12px;padding:16px 20px;">
              <p style="margin:0;color:#1a1a2e;font-size:14px;line-height:1.6;white-space:pre-wrap;">{safe_message}</p>
            </div>
          </td>
        </tr>"""

    html_body = render_email_shell(
        preheader=f"New contact request: {topic}",
        body_html=body_html,
    )

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


# ── Public async interface ─────────────────────────────────────────────────────

async def send_otp_email(to_email: str, otp: int) -> OTPResult:
    """Send a 6-digit OTP via email (async, non-blocking).

    Returns:
        OTPResult with success=True on delivery, or success=False + error string
        on SMTP failure. Never raises — callers decide how to handle failure.
    """
    msg = _build_otp_email(to_email, otp)
    try:
        await asyncio.to_thread(_smtp_send, to_email, msg)
        return OTPResult(success=True, channel="email")
    except smtplib.SMTPAuthenticationError as exc:
        error = "SMTP authentication failed — check SMTP_USER / SMTP_PASSWORD in .env"
        logger.error("OTP email auth error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except smtplib.SMTPException as exc:
        error = f"SMTP error: {exc}"
        logger.error("OTP email SMTP error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except Exception as exc:
        error = f"Unexpected error: {exc}"
        logger.error("OTP email unexpected error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)


def _build_password_reset_email(to_email: str, otp: int) -> MIMEMultipart:
    """Construct the branded password reset email (plain-text + HTML multipart)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Reset Your Axiora Pulse Password"
    msg["From"] = f"{_SMTP_FROM_NAME} <{_SMTP_FROM_EMAIL}>"
    msg["To"] = to_email

    plain_body = (
        f"Hello,\n\n"
        f"Your Axiora Pulse password reset code is: {otp}\n\n"
        f"This code is valid for {_OTP_EXPIRE_MINS} minutes.\n"
        f"If you did not request this, please ignore this email.\n\n"
        f"— The Axiora Pulse Team"
    )

    html_body = f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;padding:40px 0;">
    <tr><td align="center">
      <table width="480" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:16px;padding:48px 40px;
                    box-shadow:0 4px 24px rgba(0,0,0,0.08);">
        <tr>
          <td style="text-align:center;padding-bottom:8px;">
            <span style="font-size:22px;font-weight:700;color:#1a1a2e;letter-spacing:-0.5px;">
              Axiora Pulse
            </span>
          </td>
        </tr>
        <tr>
          <td style="text-align:center;padding-top:8px;padding-bottom:32px;">
            <p style="margin:0;color:#555;font-size:15px;">
              Use the code below to reset your password.
            </p>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-bottom:32px;">
            <div style="display:inline-block;background:#f5f3ff;border-radius:12px;
                        padding:20px 40px;">
              <span style="font-size:48px;font-weight:800;letter-spacing:14px;color:#4f46e5;">
                {otp}
              </span>
            </div>
          </td>
        </tr>
        <tr>
          <td style="text-align:center;">
            <p style="margin:0;color:#888;font-size:13px;line-height:1.6;">
              This code expires in <strong>{_OTP_EXPIRE_MINS} minutes</strong>.<br>
              If you didn&rsquo;t request this, you can safely ignore this email.
            </p>
          </td>
        </tr>
        <tr>
          <td style="text-align:center;padding-top:32px;border-top:1px solid #f0f0f0;margin-top:32px;">
            <p style="margin:0;color:#bbb;font-size:12px;">
              &copy; 2025 Axiora Pulse. All rights reserved.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


async def send_password_reset_email(to_email: str, otp: int) -> OTPResult:
    """Send a 6-digit password reset OTP via email (async, non-blocking).

    Returns:
        OTPResult with success=True on delivery, or success=False + error string
        on SMTP failure. Never raises — callers decide how to handle failure.
    """
    msg = _build_password_reset_email(to_email, otp)
    try:
        await asyncio.to_thread(_smtp_send, to_email, msg)
        return OTPResult(success=True, channel="email")
    except smtplib.SMTPAuthenticationError as exc:
        error = "SMTP authentication failed — check SMTP_USER / SMTP_PASSWORD in .env"
        logger.error("Password reset email auth error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except smtplib.SMTPException as exc:
        error = f"SMTP error: {exc}"
        logger.error("Password reset email SMTP error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except Exception as exc:
        error = f"Unexpected error: {exc}"
        logger.error("Password reset email unexpected error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)


def _build_login_otp_email(to_email: str, otp: int) -> MIMEMultipart:
    """Construct the branded login OTP email (plain-text + HTML multipart)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Axiora Pulse Login Verification Code"
    msg["From"] = f"{_SMTP_FROM_NAME} <{_SMTP_FROM_EMAIL}>"
    msg["To"] = to_email

    plain_body = (
        f"Hello,\n\n"
        f"Your Axiora Pulse login verification code is: {otp}\n\n"
        f"This code is valid for {_OTP_EXPIRE_MINS} minutes.\n"
        f"If you did not request this, please ignore this email.\n\n"
        f"— The Axiora Pulse Team"
    )

    html_body = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
</head>
<body style="margin:0;padding:0;background-color:#f9fafb;font-family:'Segoe UI',system-ui,sans-serif;-webkit-font-smoothing:antialiased;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#f9fafb;padding:48px 0;">
    <tr><td align="center">
      <table width="100%" max-width="500" border="0" cellspacing="0" cellpadding="0" 
             style="max-width:500px;background-color:#ffffff;border:1px solid #e5e7eb;
                    border-radius:16px;padding:40px;box-shadow:0 4px 6px -1px rgba(0,0,0,0.05);">
        <tr>
          <td style="text-align:center;padding-bottom:32px;border-bottom:1px solid #f0f0f0;">
            <h1 style="margin:0;font-size:24px;font-weight:800;color:#111827;letter-spacing:-0.5px;">
              Axiora Pulse
            </h1>
            <p style="margin:4px 0 0 0;font-size:14px;color:#6b7280;">Login Verification</p>
          </td>
        </tr>
        <tr>
          <td style="padding:32px 0;text-align:center;">
            <p style="margin:0 0 24px 0;font-size:16px;color:#374151;line-height:1.5;">
              Use the following verification code to complete your login:
            </p>
            <div style="display:inline-block;background:#eff6ff;border-radius:12px;
                        padding:20px 40px;">
              <span style="font-size:48px;font-weight:800;letter-spacing:14px;color:#2563eb;">
                {otp}
              </span>
            </div>
          </td>
        </tr>
        <tr>
          <td style="text-align:center;">
            <p style="margin:0;color:#888;font-size:13px;line-height:1.6;">
              This code expires in <strong>{_OTP_EXPIRE_MINS} minutes</strong>.<br>
              If you didn&rsquo;t request this, you can safely ignore this email.
            </p>
          </td>
        </tr>
        <tr>
          <td style="text-align:center;padding-top:32px;border-top:1px solid #f0f0f0;margin-top:32px;">
            <p style="margin:0;color:#bbb;font-size:12px;">
              &copy; 2025 Axiora Pulse. All rights reserved.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


async def send_login_otp_email(to_email: str, otp: int) -> OTPResult:
    """Send a 6-digit login OTP via email (async, non-blocking)."""
    msg = _build_login_otp_email(to_email, otp)
    try:
        await asyncio.to_thread(_smtp_send, to_email, msg)
        return OTPResult(success=True, channel="email")
    except smtplib.SMTPAuthenticationError as exc:
        error = "SMTP authentication failed — check SMTP_USER / SMTP_PASSWORD in .env"
        logger.error("Login OTP email auth error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except smtplib.SMTPException as exc:
        error = f"SMTP error: {exc}"
        logger.error("Login OTP email SMTP error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except Exception as exc:
        error = f"Unexpected error: {exc}"
        logger.error("Login OTP email unexpected error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)


# Registration success (welcome) email

def _build_registration_success_email(to_email: str, display_name: Optional[str] = None) -> MIMEMultipart:
    """Construct the branded 'account created' welcome email."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Welcome to Axiora Pulse. Your Account is Ready"
    msg["From"] = f"{_SMTP_FROM_NAME} <{_SMTP_FROM_EMAIL}>"
    msg["To"] = to_email

    greeting_name = display_name.strip() if display_name else to_email
    safe_name = html.escape(greeting_name)

    plain_body = (
        f"Hello {greeting_name},\n\n"
        f"Welcome to Axiora Pulse! Your account has been created successfully "
        f"and is ready to use.\n\n"
        f"You can sign in any time at {_DASHBOARD_LOGIN_URL}\n\n"
        f"If you did not create this account, please contact us at {_SUPPORT_EMAIL}.\n\n"
        f"— The Axiora Pulse Team"
    )

    body_html = f"""\
        <tr>
          <td align="center" style="padding-bottom:8px;">
            <h1 class="text-primary" style="margin:0;font-size:22px;font-weight:700;color:#1a1a2e;letter-spacing:-0.5px;">
              Welcome to Axiora Pulse!
            </h1>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-top:8px;padding-bottom:28px;">
            <p class="text-secondary" style="margin:0;color:#555;font-size:15px;line-height:1.6;">
              Hi {safe_name}, your account has been created successfully and is ready to go.
            </p>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-bottom:28px;">
            {render_button("Go to Dashboard", _DASHBOARD_LOGIN_URL)}
          </td>
        </tr>
        <tr>
          <td align="center">
            <p class="text-secondary" style="margin:0;color:#888;font-size:13px;line-height:1.6;">
              If you didn&rsquo;t create this account, please contact us at
              <a href="mailto:{html.escape(_SUPPORT_EMAIL, quote=True)}" style="color:#4f46e5;">{html.escape(_SUPPORT_EMAIL)}</a>.
            </p>
          </td>
        </tr>"""

    html_body = render_email_shell(
        preheader="Your Axiora Pulse account is ready.",
        body_html=body_html,
    )

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


async def send_registration_success_email(to_email: str, display_name: Optional[str] = None) -> OTPResult:
    """Send the welcome / account-created confirmation email (async, non-blocking).

    Best-effort — never raises. Returns OTPResult with success=False + error
    string on delivery failure so callers/background jobs can log or retry.
    """
    msg = _build_registration_success_email(to_email, display_name)
    try:
        await asyncio.to_thread(_smtp_send, to_email, msg)
        return OTPResult(success=True, channel="email")
    except smtplib.SMTPAuthenticationError as exc:
        error = "SMTP authentication failed — check SMTP_USER / SMTP_PASSWORD in .env"
        logger.error("Registration success email auth error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except smtplib.SMTPException as exc:
        error = f"SMTP error: {exc}"
        logger.error("Registration success email SMTP error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except Exception as exc:
        error = f"Unexpected error: {exc}"
        logger.error("Registration success email unexpected error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)


# Password reset success email 

def _build_password_reset_success_email(to_email: str, changed_at: Optional[datetime] = None) -> MIMEMultipart:
    """Construct the branded 'password changed' confirmation email."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Axiora Pulse Password Was Changed"
    msg["From"] = f"{_SMTP_FROM_NAME} <{_SMTP_FROM_EMAIL}>"
    msg["To"] = to_email

    when = changed_at or datetime.now(tz=timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    local_when = when.astimezone(_EMAIL_TIMEZONE)
    timestamp_str = local_when.strftime("%B %d, %Y at %I:%M %p %Z")
    safe_timestamp = html.escape(timestamp_str)

    plain_body = (
        f"Hello,\n\n"
        f"Your Axiora Pulse account password was changed successfully on {timestamp_str}.\n\n"
        f"If you made this change, no further action is needed.\n\n"
        f"If you did NOT make this change, your account may be compromised — "
        f"please contact us immediately at {_SUPPORT_EMAIL}.\n\n"
        f"— The Axiora Pulse Team"
    )

    body_html = f"""\
        <tr>
          <td align="center" style="padding-bottom:8px;">
            <h1 class="text-primary" style="margin:0;font-size:22px;font-weight:700;color:#1a1a2e;letter-spacing:-0.5px;">
              Password Changed Successfully
            </h1>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-top:8px;padding-bottom:28px;">
            <p class="text-secondary" style="margin:0;color:#555;font-size:15px;line-height:1.6;">
              Your account password was changed on<br><strong>{safe_timestamp}</strong>.
            </p>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-bottom:8px;">
            <div style="background:#fff7ed;border:1px solid #fdba74;border-radius:12px;padding:16px 20px;">
              <p style="margin:0;color:#9a3412;font-size:13px;line-height:1.6;">
                <strong>Didn&rsquo;t make this change?</strong> Your account may be compromised.
                Contact us immediately at
                <a href="mailto:{html.escape(_SUPPORT_EMAIL, quote=True)}" style="color:#9a3412;font-weight:700;">{html.escape(_SUPPORT_EMAIL)}</a>.
              </p>
            </div>
          </td>
        </tr>"""

    html_body = render_email_shell(
        preheader="Your Axiora Pulse password was changed.",
        body_html=body_html,
    )

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


async def send_password_reset_success_email(to_email: str, changed_at: Optional[datetime] = None) -> OTPResult:
    """Send the 'password changed successfully' confirmation email (async, non-blocking).

    Best-effort — never raises. Never includes the password itself.
    """
    msg = _build_password_reset_success_email(to_email, changed_at)
    try:
        await asyncio.to_thread(_smtp_send, to_email, msg)
        return OTPResult(success=True, channel="email")
    except smtplib.SMTPAuthenticationError as exc:
        error = "SMTP authentication failed — check SMTP_USER / SMTP_PASSWORD in .env"
        logger.error("Password reset success email auth error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except smtplib.SMTPException as exc:
        error = f"SMTP error: {exc}"
        logger.error("Password reset success email SMTP error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except Exception as exc:
        error = f"Unexpected error: {exc}"
        logger.error("Password reset success email unexpected error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)


# Website "Get in Touch" contact-form email

async def send_contact_email(name: str, email: str, topic: str, message: str) -> OTPResult:
    """Forward a website contact-form submission to the support inbox (async, non-blocking).

    Best-effort — never raises. Returns OTPResult with success=False + error
    string on delivery failure so callers/background jobs can log or retry.
    """
    msg = _build_contact_email(name, email, topic, message)
    try:
        await asyncio.to_thread(_smtp_send, _CONTACT_RECIPIENT_EMAIL, msg)
        return OTPResult(success=True, channel="email")
    except smtplib.SMTPAuthenticationError as exc:
        error = "SMTP authentication failed — check SMTP_USER / SMTP_PASSWORD in .env"
        logger.error("Contact email auth error: %s", exc)
        return OTPResult(success=False, channel="email", error=error)
    except smtplib.SMTPException as exc:
        error = f"SMTP error: {exc}"
        logger.error("Contact email SMTP error: %s", exc)
        return OTPResult(success=False, channel="email", error=error)
    except Exception as exc:
        error = f"Unexpected error: {exc}"
        logger.error("Contact email unexpected error: %s", exc)
        return OTPResult(success=False, channel="email", error=error)


# ── Survey Response Notification Email ─────────────────────────────────────────

def _build_survey_response_notification_email(
    to_email: str,
    workspace_name: str,
    workspace_id: int,
    survey_id: int,
    respondent_name: str,
    respondent_email: str,
    questions: list[dict],
    answers: list[dict],
    contact_number: Optional[str] = None,
    submitted_at: Optional[datetime] = None,
) -> MIMEMultipart:
    """Construct the branded email notifying an Axiora member of a new survey response."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"New Survey Response Received — {workspace_name}"
    msg["From"] = f"{_SMTP_FROM_NAME} <{_SMTP_FROM_EMAIL}>"
    msg["To"] = to_email

    when = submitted_at or datetime.now(tz=timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    local_when = when.astimezone(_EMAIL_TIMEZONE)
    timestamp_str = local_when.strftime("%B %d, %Y at %I:%M %p %Z")
    safe_timestamp = html.escape(timestamp_str)
    safe_workspace = html.escape(workspace_name)
    display_respondent_name = respondent_name.strip() if respondent_name else "Unknown Respondent"
    display_respondent = respondent_email.strip() if respondent_email else "Not provided"
    display_contact_number = contact_number.strip() if contact_number else "Not provided"
    safe_respondent_name = html.escape(display_respondent_name)
    safe_respondent = html.escape(display_respondent)
    safe_contact_number = html.escape(display_contact_number)

    # Build question mapping
    q_map: dict[Any, str] = {}
    for q in questions:
        if isinstance(q, dict):
            qid = q.get("id") or q.get("question_id")
            q_text = q.get("question") or q.get("question_text") or f"Question {qid}"
            if qid is not None:
                q_map[qid] = str(q_text)
                q_map[str(qid)] = str(q_text)
        elif hasattr(q, "id") and hasattr(q, "question"):
            q_map[q.id] = str(q.question)
            q_map[str(q.id)] = str(q.question)

    qa_cards_html = []
    plain_qa_lines = []
    for idx, item in enumerate(answers, start=1):
        qid = item.get("questionId") if isinstance(item, dict) else getattr(item, "questionId", None)
        ans = item.get("answer") if isinstance(item, dict) else getattr(item, "answer", "")
        q_label = q_map.get(qid, q_map.get(str(qid), f"Question #{qid}"))

        if isinstance(ans, list):
            ans_str = ", ".join(str(x) for x in ans)
        elif ans is None or ans == "":
            ans_str = "(No answer provided)"
        else:
            ans_str = str(ans)

        safe_q = html.escape(str(q_label))
        safe_a = html.escape(ans_str)
        plain_qa_lines.append(f"• Q{idx}. {q_label}\n  Answer: {ans_str}")

        qa_cards_html.append(f"""\
            <div class="qa-box" style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:16px 20px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,0.02);">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="padding-bottom:10px;">
                    <span style="display:inline-block;padding:2px 8px;background:#e0e7ff;color:#4338ca;font-size:11px;font-weight:800;border-radius:6px;margin-right:8px;vertical-align:middle;">Q{idx}</span>
                    <span class="text-primary" style="font-size:14px;font-weight:700;color:#1e293b;line-height:1.5;vertical-align:middle;">{safe_q}</span>
                  </td>
                </tr>
                <tr>
                  <td>
                    <div class="answer-box" style="padding:12px 16px;background:#f8fafc;border-left:4px solid #4f46e5;border-radius:0 8px 8px 0;">
                      <span style="display:block;font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Respondent Answer</span>
                      <span style="display:block;font-size:14px;font-weight:600;color:#312e81;line-height:1.6;white-space:pre-wrap;">{safe_a}</span>
                    </div>
                  </td>
                </tr>
              </table>
            </div>""")

    qa_content_html = "".join(qa_cards_html) if qa_cards_html else """\
            <div class="qa-box" style="background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;padding:20px;text-align:center;color:#6b7280;font-size:13px;">
              No question answers recorded for this submission.
            </div>"""

    public_app_url = os.getenv("PUBLIC_APP_URL", "")
    if public_app_url:
        cta_url = f"{public_app_url.rstrip('/')}/workspace/{workspace_id}/survey"
    else:
        cta_url = _DASHBOARD_LOGIN_URL

    plain_body = (
        f"Hello,\n\n"
        f"A new respondent just submitted feedback for your survey in '{workspace_name}'!\n\n"
        f"Submission Details:\n"
        f"• Workspace: {workspace_name}\n"
        f"• Survey ID: #{survey_id}\n"
        f"• Submitted At: {timestamp_str}\n"
        f"• Respondent Name: {display_respondent_name}\n"
        f"• Respondent Email: {display_respondent}\n"
        f"• Contact Number: {display_contact_number}\n\n"
        f"Submitted Answers:\n"
        f"{chr(10).join(plain_qa_lines)}\n\n"
        f"Ready to analyze? Log in to Axiora Pulse and run the Survey Intelligence Analysis:\n"
        f"{cta_url}\n\n"
        f"— The Axiora Pulse Team"
    )

    body_html = f"""\
        <!-- Header badge & title -->
        <tr>
          <td align="center" style="padding-bottom:6px;">
            <span style="display:inline-block;padding:4px 12px;background:#eef2ff;color:#4f46e5;font-size:11px;font-weight:800;border-radius:20px;text-transform:uppercase;letter-spacing:0.8px;">
              Survey Intelligence
            </span>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-bottom:8px;">
            <h1 class="text-primary responsive-header" style="margin:8px 0 0 0;font-size:24px;font-weight:800;color:#111827;letter-spacing:-0.5px;">
              New Survey Response Received
            </h1>
          </td>
        </tr>
        <tr>
          <td align="center" style="padding-bottom:24px;">
            <p class="text-secondary" style="margin:0;color:#6b7280;font-size:15px;line-height:1.5;">
              A respondent submitted new feedback for <strong style="color:#111827;">{safe_workspace}</strong>.
            </p>
          </td>
        </tr>

        <!-- Responsive Metadata Grid (Landscape 3-col / Mobile stacked) -->
        <tr>
          <td style="padding-bottom:24px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:separate;">
              <tr>
                <td class="meta-grid-item" width="33%" style="vertical-align:top;padding-right:6px;">
                  <div class="meta-box" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;padding:14px 16px;min-height:72px;">
                    <span style="display:block;font-size:11px;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Workspace</span>
                    <span class="text-primary" style="display:block;font-size:14px;font-weight:700;color:#111827;word-break:break-word;">{safe_workspace}</span>
                    <span style="display:inline-block;font-size:11px;font-weight:600;color:#6366f1;margin-top:3px;">Survey #{survey_id}</span>
                  </div>
                </td>
                <td class="meta-grid-item" width="34%" style="vertical-align:top;padding-right:3px;padding-left:3px;">
                  <div class="meta-box" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;padding:14px 16px;min-height:72px;">
                    <span style="display:block;font-size:11px;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Respondent</span>
                    <span class="text-primary" style="display:block;font-size:13px;font-weight:700;color:#111827;word-break:break-word;">{safe_respondent_name}</span>
                    <span style="display:block;font-size:12px;font-weight:600;color:#6b7280;word-break:break-all;margin-top:2px;">{safe_respondent}</span>
                    <span style="display:block;font-size:12px;font-weight:600;color:#6b7280;margin-top:2px;">{safe_contact_number}</span>
                  </div>
                </td>
                <td class="meta-grid-item" width="33%" style="vertical-align:top;padding-left:6px;">
                  <div class="meta-box" style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;padding:14px 16px;min-height:72px;">
                    <span style="display:block;font-size:11px;font-weight:700;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Submitted At</span>
                    <span class="text-primary" style="display:block;font-size:13px;font-weight:600;color:#111827;">{safe_timestamp}</span>
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Section header -->
        <tr>
          <td style="padding-bottom:12px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td>
                  <h2 class="text-primary" style="margin:0;font-size:16px;font-weight:800;color:#111827;letter-spacing:-0.3px;">
                    Response Breakdown
                  </h2>
                </td>
                <td align="right">
                  <span style="font-size:12px;font-weight:700;color:#6366f1;background:#eef2ff;padding:3px 10px;border-radius:12px;">
                    {len(answers)} {"Answer" if len(answers) == 1 else "Answers"}
                  </span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- QA Cards list -->
        <tr>
          <td style="padding-bottom:24px;">
            {qa_content_html}
          </td>
        </tr>

        <!-- Callout Banner & Action button -->
        <tr>
          <td style="padding-bottom:12px;">
            <div style="background:linear-gradient(135deg, #f5f3ff 0%, #ede9fe 100%);border:1px solid #ddd6fe;border-radius:14px;padding:24px 20px;text-align:center;">
              <h3 style="margin:0 0 6px 0;font-size:15px;font-weight:800;color:#4338ca;">
                Ready to Evaluate Customer Evidence?
              </h3>
              <p style="margin:0 0 18px 0;color:#5b21b6;font-size:13px;line-height:1.5;">
                Synthesize this submission with your problem & solution validation hypotheses in Axiora Pulse.
              </p>
              {render_button("View Survey & Run Analysis", cta_url)}
            </div>
          </td>
        </tr>"""

    html_body = render_email_shell(
        preheader=f"New response received for {workspace_name}.",
        body_html=body_html,
        max_width=680,
    )

    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


async def send_survey_response_notification_email(
    to_email: str,
    workspace_name: str,
    workspace_id: int,
    survey_id: int,
    respondent_name: str,
    respondent_email: str,
    questions: list[dict],
    answers: list[dict],
    contact_number: Optional[str] = None,
    submitted_at: Optional[datetime] = None,
) -> OTPResult:
    """Send a response submission notification email to the survey owner (async, non-blocking).

    Best-effort — never raises. Returns OTPResult with success=False + error
    string on delivery failure so callers can log or handle gracefully.
    """
    msg = _build_survey_response_notification_email(
        to_email=to_email,
        workspace_name=workspace_name,
        workspace_id=workspace_id,
        survey_id=survey_id,
        respondent_name=respondent_name,
        respondent_email=respondent_email,
        contact_number=contact_number,
        questions=questions,
        answers=answers,
        submitted_at=submitted_at,
    )
    try:
        await asyncio.to_thread(_smtp_send, to_email, msg)
        return OTPResult(success=True, channel="email")
    except smtplib.SMTPAuthenticationError as exc:
        error = "SMTP authentication failed — check SMTP_USER / SMTP_PASSWORD in .env"
        logger.error("Survey response notification email auth error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except smtplib.SMTPException as exc:
        error = f"SMTP error: {exc}"
        logger.error("Survey response notification email SMTP error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)
    except Exception as exc:
        error = f"Unexpected error: {exc}"
        logger.error("Survey response notification email unexpected error for %s: %s", to_email, exc)
        return OTPResult(success=False, channel="email", error=error)



