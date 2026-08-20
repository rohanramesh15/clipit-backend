import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.core.config import settings


def generate_token() -> str:
    """Generate a secure random token."""
    return secrets.token_urlsafe(32)


def send_email(to: str, subject: str, html: str) -> bool:
    """Send an email using Resend API."""
    if not settings.RESEND_API_KEY:
        print(f"[Email] Resend API key not configured. Would send to {to}: {subject}")
        return True  # Return True in dev mode so flow continues

    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": settings.EMAIL_FROM,
                "to": [to],
                "subject": subject,
                "html": html,
            },
        )
        if response.status_code == 200:
            print(f"[Email] Sent email to {to}")
            return True
        else:
            print(f"[Email] Failed to send email: {response.text}")
            return False
    except Exception as e:
        print(f"[Email] Failed to send email: {e}")
        return False


def send_password_reset_email(to: str, token: str) -> bool:
    """Send password reset link."""
    reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background-color: #0a0a0b; color: #ffffff; padding: 40px 20px;">
        <div style="max-width: 480px; margin: 0 auto; background-color: #141416; border-radius: 16px; padding: 40px; border: 1px solid rgba(255,255,255,0.1);">
            <h1 style="color: #FA8072; margin: 0 0 24px 0; font-size: 24px;">Reset your password</h1>
            <p style="color: #a1a1aa; line-height: 1.6; margin: 0 0 24px 0;">
                We received a request to reset your password. Click the button below to choose a new password.
            </p>
            <a href="{reset_url}" style="display: inline-block; background-color: #FA8072; color: #0a0a0b; text-decoration: none; padding: 12px 24px; border-radius: 8px; font-weight: 600;">
                Reset Password
            </a>
            <p style="color: #71717a; font-size: 14px; margin: 24px 0 0 0;">
                This link will expire in 1 hour. If you didn't request a password reset, you can safely ignore this email.
            </p>
        </div>
    </body>
    </html>
    """

    return send_email(to, "Reset your password - Clip It", html)


def get_reset_token_expiry() -> datetime:
    """Get expiry time for password reset token (1 hour from now)."""
    return datetime.now(timezone.utc) + timedelta(hours=1)


def is_token_expired(expires: Optional[datetime]) -> bool:
    """Check if a token has expired."""
    if not expires:
        return True
    # Make sure we compare timezone-aware datetimes
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expires
