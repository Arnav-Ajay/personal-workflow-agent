"""Build a Coinbase-only MIME email from a verified saved snapshot."""

from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, make_msgid
from typing import Any
from zoneinfo import ZoneInfo

from src.gmail import mailbox

from .reporting import render_coinbase_report, render_coinbase_text


def build_coinbase_email(snapshot: dict[str, Any], *, sender: str, recipients: tuple[str, ...],
                         report_timezone: str = "UTC") -> EmailMessage:
    if not recipients:
        raise ValueError("At least one Gmail recipient is required")
    html = render_coinbase_report(snapshot, email=True)
    captured = datetime.fromisoformat(snapshot["as_of"].replace("Z", "+00:00")).astimezone(ZoneInfo(report_timezone))
    message = EmailMessage(policy=SMTP)
    message["From"] = mailbox(sender)
    message["To"] = ", ".join(mailbox(address) for address in recipients)
    message["Subject"] = f"Coinbase portfolio report · {captured:%Y-%m-%d %H:%M %Z}"
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["Message-ID"] = make_msgid(domain="personal-workflow.local")
    message["X-Workflow-Source"] = "coinbase"
    message["X-Workflow-Snapshot-ID"] = snapshot["snapshot_id"]
    message.set_content(render_coinbase_text(snapshot))
    message.add_alternative(html, subtype="html")
    return message
