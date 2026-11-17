"""Callable Gmail SMTP delivery; credentials are supplied by entry-point scripts."""

from .client import GmailClient, GmailClientError, GmailDeliveryResult, mailbox, recipients

__all__ = ["GmailClient", "GmailClientError", "GmailDeliveryResult", "mailbox", "recipients"]
