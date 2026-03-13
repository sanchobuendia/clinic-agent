from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from utils.logger import get_logger

logger = get_logger("EmailService")


class EmailServiceError(RuntimeError):
    pass


def _get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise EmailServiceError(f"Variavel {name} nao configurada.")
    return value


def send_email(to_email: str, subject: str, body: str) -> None:
    smtp_host = _get_required_env("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = _get_required_env("SMTP_USER")
    smtp_password = _get_required_env("SMTP_PASSWORD")
    smtp_from = os.getenv("SMTP_FROM_EMAIL", smtp_user).strip()
    smtp_from_name = os.getenv("SMTP_FROM_NAME", "").strip()
    smtp_use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes"}

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((smtp_from_name, smtp_from)) if smtp_from_name else smtp_from
    message["To"] = to_email
    message.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if smtp_use_tls:
            server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(message)

    logger.info("Email enviado para %s.", to_email)
