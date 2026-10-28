"""Payment provider abstraction (blueprint SS9).

The application never trusts a browser redirect: a payment only counts when
``verify`` (server-to-server) or a validated webhook confirms it, and the
amount/currency/reference match the internal attempt. ``MockProvider`` is a
clearly labelled sandbox used when real credentials are unavailable.
"""
from dataclasses import dataclass, field

from django.conf import settings

from core.exceptions import BusinessError


@dataclass
class InitResult:
    provider_reference: str
    checkout_url: str
    status: str = "pending"
    raw: dict = field(default_factory=dict)


@dataclass
class VerifyResult:
    status: str  # succeeded | pending | failed | cancelled
    reference: str = ""
    provider_reference: str = ""
    amount: str = ""
    currency: str = ""
    raw: dict = field(default_factory=dict)


class PaymentProvider:
    name = "base"

    def initialize(self, *, attempt, callback_url: str, customer: dict) -> InitResult:
        raise NotImplementedError

    def verify(self, *, attempt) -> VerifyResult:
        raise NotImplementedError

    def parse_webhook(self, request) -> dict:
        """Return {event_id, event_type, reference, status} or raise."""
        raise NotImplementedError


class MockProvider(PaymentProvider):
    """Sandbox only. Payments stay 'pending' until the sandbox confirm
    endpoint simulates a provider callback; never usable in production."""

    name = "mock"

    def initialize(self, *, attempt, callback_url, customer):
        if not getattr(settings, "PAYMENT_ALLOW_MOCK", False):
            raise BusinessError(
                "The mock payment provider is disabled "
                "(set PAYMENT_ALLOW_MOCK=1 for sandbox/demo only).",
                code="PROVIDER_DISABLED")
        return InitResult(
            provider_reference=f"mock_{attempt.reference}",
            checkout_url=f"{settings.FRONTEND_BASE_URL}/pay/mock?reference={attempt.reference}",
            status="pending",
            raw={"sandbox": True, "note": "Simulated provider - no real money moves."},
        )

    def verify(self, *, attempt):
        # Status is whatever the sandbox simulation last set on the attempt.
        return VerifyResult(
            status=attempt.status if attempt.status in
            ("succeeded", "failed", "cancelled") else "pending",
            reference=attempt.reference,
            provider_reference=attempt.provider_reference,
            amount=str(attempt.amount),
            currency=attempt.currency,
            raw={"sandbox": True},
        )

    def parse_webhook(self, request):
        import json

        try:
            payload = json.loads(request.body.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = {}
        return {
            "event_id": payload.get("event_id") or payload.get("id") or "",
            "event_type": payload.get("event") or payload.get("type") or "payment.updated",
            "reference": payload.get("reference") or "",
            "status": payload.get("status") or "",
            "payload": payload,
        }


PROVIDERS: dict[str, PaymentProvider] = {}


def get_provider(name: str | None = None) -> PaymentProvider:
    key = (name or settings.PAYMENT_PROVIDER or "mock").lower()
    if key not in PROVIDERS:
        if key == "mock":
            PROVIDERS[key] = MockProvider()
        elif key == "notchpay":
            from .notchpay import NotchPayProvider

            PROVIDERS[key] = NotchPayProvider()
        elif key == "stripe":
            from .stripe import StripeProvider

            PROVIDERS[key] = StripeProvider()
        else:
            raise BusinessError(f"Unknown payment provider '{key}'.",
                                code="PROVIDER_UNKNOWN")
    return PROVIDERS[key]
