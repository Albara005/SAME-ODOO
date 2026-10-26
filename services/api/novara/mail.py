"""Outgoing email: verification, password reset and invitations.

The API sends after its transaction commits; a failed send is logged and never undoes the
change (the user can ask for the email again). Tests use MemoryMailer.
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

log = logging.getLogger("novara.mail")


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str


class Mailer(Protocol):
    def send(self, message: Message) -> None: ...


@dataclass
class MemoryMailer:
    """Keeps every message (tests and local development)."""

    sent: list[Message] = field(default_factory=list)

    def send(self, message: Message) -> None:
        self.sent.append(message)


class LogMailer(MemoryMailer):
    """Development default: writes each email to the log instead of sending it."""

    def send(self, message: Message) -> None:
        super().send(message)
        log.warning("email to %s: %s\n%s", message.to, message.subject, message.text)


@dataclass
class SmtpMailer:
    host: str
    port: int
    sender: str
    user: str = ""
    password: str = ""
    starttls: bool = True
    timeout_s: float = 15.0

    def send(self, message: Message) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, message.to, message.subject
        msg.set_content(message.text)
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout_s) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


def mailer_from_settings(settings) -> Mailer:
    if settings.smtp_host:
        return SmtpMailer(host=settings.smtp_host, port=settings.smtp_port, sender=settings.mail_from,
                          user=settings.smtp_user, password=settings.smtp_password, starttls=settings.smtp_starttls)
    return LogMailer()


def deliver(mailer: Mailer, message: Message) -> bool:
    """Send without letting a mail problem fail the request that triggered it."""
    try:
        mailer.send(message)
        return True
    except Exception:  # noqa: BLE001 - any transport error; the action itself already succeeded
        log.exception("could not send email to %s", message.to)
        return False
