"""Provider adapters (BR-NOT-10) — swapping vendors never touches business code.

WhatsApp/SMS providers activate when T-EXT credentials land in env; until then
ConsoleProvider records sends locally so every flow is testable end to end.
"""
from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("liger.notifications")


@dataclass
class SendResult:
    ok: bool
    provider_msg_id: str | None = None
    error: str | None = None


class NotificationProvider(Protocol):
    channel: str

    def send(self, *, recipient: str, body: str,
             template_id: str | None = None) -> SendResult: ...


class ConsoleProvider:
    """Local/test provider — always succeeds, keeps a log for assertions."""

    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.sent: list[dict] = []

    def send(self, *, recipient: str, body: str,
             template_id: str | None = None) -> SendResult:
        msg_id = f"{self.channel}_{secrets.token_hex(6)}"
        self.sent.append({"recipient": recipient, "body": body, "msg_id": msg_id})
        logger.info("[%s -> %s] %s", self.channel, recipient, body[:120])
        return SendResult(ok=True, provider_msg_id=msg_id)


class WhatsAppBSPProvider:
    """WhatsApp Business API via a BSP (AiSensy/Gupshup/Interakt).
    Needs WHATSAPP_API_URL + WHATSAPP_API_KEY (T-EXT)."""

    channel = "whatsapp"

    def __init__(self) -> None:
        self.api_url = os.environ["WHATSAPP_API_URL"]
        self.api_key = os.environ["WHATSAPP_API_KEY"]

    def send(self, *, recipient: str, body: str,
             template_id: str | None = None) -> SendResult:
        import httpx

        try:
            response = httpx.post(
                f"{self.api_url}/messages",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"to": recipient, "type": "template" if template_id else "text",
                      "template_id": template_id, "body": body},
                timeout=15,
            )
            response.raise_for_status()
            return SendResult(ok=True, provider_msg_id=response.json().get("message_id"))
        except Exception as exc:
            return SendResult(ok=False, error=f"{type(exc).__name__}: {exc}")


class DLTSMSProvider:
    """DLT-registered SMS (MSG91-style). Needs MSG91_API_KEY (T-EXT)."""

    channel = "sms"

    def __init__(self) -> None:
        self.api_key = os.environ["MSG91_API_KEY"]
        self.sender_id = os.environ.get("MSG91_SENDER_ID", "LIGER")

    def send(self, *, recipient: str, body: str,
             template_id: str | None = None) -> SendResult:
        import httpx

        try:
            response = httpx.post(
                "https://api.msg91.com/api/v5/flow/",
                headers={"authkey": self.api_key},
                json={"sender": self.sender_id, "mobiles": recipient,
                      "flow_id": template_id, "body": body},
                timeout=15,
            )
            response.raise_for_status()
            return SendResult(ok=True, provider_msg_id=str(response.json().get("request_id")))
        except Exception as exc:
            return SendResult(ok=False, error=f"{type(exc).__name__}: {exc}")


_providers: dict[str, NotificationProvider] = {}


def _real_creds(var: str) -> bool:
    value = os.environ.get(var, "")
    return bool(value) and not value.startswith("your_")


def get_provider(channel: str) -> NotificationProvider:
    if channel not in _providers:
        if channel == "whatsapp" and _real_creds("WHATSAPP_API_KEY"):
            _providers[channel] = WhatsAppBSPProvider()
        elif channel == "sms" and _real_creds("MSG91_API_KEY"):
            _providers[channel] = DLTSMSProvider()
        else:
            _providers[channel] = ConsoleProvider(channel)
    return _providers[channel]


def reset_providers() -> None:
    _providers.clear()
