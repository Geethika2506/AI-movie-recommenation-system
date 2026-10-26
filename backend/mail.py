"""Deliver account verification codes by email."""

import smtplib
from email.message import EmailMessage
from typing import Literal

from backend.config import Settings

Delivery = Literal["email", "preview"]


class VerificationDeliveryError(Exception):
    """The configured mail server rejected or could not accept the message."""


def deliver_verification_code(settings: Settings, recipient: str, code: str) -> Delivery:
    if not settings.smtp_host:
        return "preview"
    message = EmailMessage()
    message["Subject"] = "Your CineMatch verification code"
    message["From"] = settings.smtp_from or settings.smtp_username or "noreply@localhost"
    message["To"] = recipient
    message.set_content(
        f"Your CineMatch verification code is {code}. It expires in 15 minutes.\n\n"
        "If you did not request this change, you can ignore this email."
    )
    try:
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                _login_and_send(smtp, settings, message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                if settings.smtp_tls:
                    smtp.starttls()
                _login_and_send(smtp, settings, message)
    except (OSError, smtplib.SMTPException) as exc:
        raise VerificationDeliveryError("could not send the verification email") from exc
    return "email"


def _login_and_send(
    smtp: smtplib.SMTP, settings: Settings, message: EmailMessage
) -> None:
    if settings.smtp_username:
        smtp.login(settings.smtp_username, settings.smtp_password)
    smtp.send_message(message)
