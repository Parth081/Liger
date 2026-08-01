"""Payment gateway adapter (BR-PAY-01, BR-NOT-10-style isolation).

Business code sees only `PaymentGateway`. Razorpay specifics — including
webhook signature verification — live here and nowhere else.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class GatewayOrder:
    gateway: str
    gateway_order_id: str
    checkout_params: dict[str, Any]     # handed to the client SDK


class PaymentGateway(Protocol):
    name: str

    def create_order(self, *, amount_paise: int, receipt: str,
                     notes: dict[str, str]) -> GatewayOrder: ...

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool: ...

    def create_payment_link(self, *, amount_paise: int, reference: str,
                            description: str, expires_seconds: int) -> tuple[str, str]:
        """Returns (link_id, url)."""
        ...


class RazorpayGateway:
    """Real gateway. Requires RAZORPAY_KEY_ID / KEY_SECRET / WEBHOOK_SECRET env.
    HTTP calls go through httpx at the call sites that need them; webhook
    verification is pure HMAC and works without network."""

    name = "razorpay"

    def __init__(self) -> None:
        self.key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        self.key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
        self.webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

    def create_order(self, *, amount_paise: int, receipt: str,
                     notes: dict[str, str]) -> GatewayOrder:
        import httpx

        response = httpx.post(
            "https://api.razorpay.com/v1/orders",
            auth=(self.key_id, self.key_secret),
            json={"amount": amount_paise, "currency": "INR", "receipt": receipt,
                  "notes": notes},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        return GatewayOrder(
            gateway=self.name,
            gateway_order_id=data["id"],
            checkout_params={"key": self.key_id, "order_id": data["id"],
                             "amount": amount_paise, "currency": "INR"},
        )

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        if not self.webhook_secret:
            return False
        expected = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def create_payment_link(self, *, amount_paise: int, reference: str,
                            description: str, expires_seconds: int) -> tuple[str, str]:
        import time

        import httpx

        response = httpx.post(
            "https://api.razorpay.com/v1/payment_links",
            auth=(self.key_id, self.key_secret),
            json={"amount": amount_paise, "currency": "INR",
                  "reference_id": reference, "description": description,
                  "expire_by": int(time.time()) + expires_seconds},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        return data["id"], data["short_url"]


class FakeGateway:
    """Local/test gateway — deterministic, no network. Signature scheme mirrors
    Razorpay's HMAC so webhook tests exercise the real verification path."""

    name = "fake"
    webhook_secret = "fake-webhook-secret"

    def create_order(self, *, amount_paise: int, receipt: str,
                     notes: dict[str, str]) -> GatewayOrder:
        oid = f"order_fake_{secrets.token_hex(8)}"
        return GatewayOrder(gateway=self.name, gateway_order_id=oid,
                            checkout_params={"order_id": oid, "amount": amount_paise})

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        expected = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def sign(self, payload: dict[str, Any]) -> tuple[bytes, str]:
        """Test helper: produce a correctly signed webhook body."""
        body = json.dumps(payload, sort_keys=True).encode()
        return body, hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()

    def create_payment_link(self, *, amount_paise: int, reference: str,
                            description: str, expires_seconds: int) -> tuple[str, str]:
        lid = f"plink_fake_{secrets.token_hex(6)}"
        return lid, f"https://pay.fake.liger/{lid}"


_gateway: PaymentGateway | None = None


def get_gateway() -> PaymentGateway:
    """Razorpay when configured; Fake otherwise (local/tests)."""
    global _gateway
    if _gateway is None:
        if os.environ.get("RAZORPAY_KEY_ID", "").startswith("rzp_") and \
                os.environ.get("RAZORPAY_KEY_SECRET", "") not in ("", "your_razorpay_secret_key"):
            _gateway = RazorpayGateway()
        else:
            _gateway = FakeGateway()
    return _gateway


def reset_gateway() -> None:
    global _gateway
    _gateway = None
