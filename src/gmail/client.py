"""Authenticated Gmail SMTP over verified implicit TLS, without automatic retries."""

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.headerregistry import Address
from email.errors import HeaderParseError
from email.message import EmailMessage

from src.observability import trace

logger = logging.getLogger(__name__)


class GmailClientError(RuntimeError):
    """A sanitized Gmail delivery error; raw SMTP responses may contain PII."""


def mailbox(value: str) -> str:
    """Accept a single bare mailbox and reject header injection or display names."""
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Gmail addresses must be valid bare email addresses")
    try:
        address = Address(addr_spec=value.strip())
        if not address.username or not address.domain:
            raise ValueError("Mailbox needs a username and domain")
        return address.addr_spec
    except (ValueError, TypeError, HeaderParseError) as exc:
        raise ValueError("Gmail addresses must be valid bare email addresses") from exc


def recipients(value: str) -> tuple[str, ...]:
    """Read comma-separated bare recipients, preserving order and removing duplicates."""
    return tuple(dict.fromkeys(mailbox(part) for part in value.split(",")))


@dataclass(frozen=True)
class GmailDeliveryResult:
    accepted_count: int
    refused_count: int


class GmailClient:
    def __init__(self, username: str, app_password: str) -> None:
        self._username = mailbox(username)
        if not isinstance(app_password, str) or not app_password.strip():
            raise ValueError("GMAIL_APP_PASSWORD is required for sending")
        self._password = "".join(app_password.split())

    @trace("gmail.send")
    def send(self, message: EmailMessage) -> GmailDeliveryResult:
        senders = message.get_all("From", [])
        addresses = message.get_all("To", [])
        if len(senders) != 1 or mailbox(str(senders[0])) != self._username:
            raise ValueError("Message sender must match the authenticated Gmail account")
        if len(addresses) != 1 or message.get_all("Cc") or message.get_all("Bcc"):
            raise ValueError("Message must have one To header and no hidden or additional recipients")
        targets = recipients(str(addresses[0]))
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
                smtp.login(self._username, self._password)
                refused = smtp.send_message(message, from_addr=self._username, to_addrs=list(targets))
        except smtplib.SMTPAuthenticationError as exc:
            raise GmailClientError("Gmail authentication failed; check the app password and account settings") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise GmailClientError("Gmail refused all recipients") from exc
        except (smtplib.SMTPException, OSError) as exc:
            # A network failure after DATA can have an uncertain outcome. Do not retry.
            raise GmailClientError("Gmail delivery failed; acceptance may be uncertain. Check Sent mail before retrying") from exc
        result = GmailDeliveryResult(len(targets) - len(refused), len(refused))
        logger.info("Gmail accepted %d recipients; refused %d", result.accepted_count, result.refused_count)
        return result
