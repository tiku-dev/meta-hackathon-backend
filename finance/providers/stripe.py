"""Stripe adapter – Checkout Sessions created server-side.

Runs in test/sandbox mode when a ``sk_test_...`` secret key is configured
(``STRIPE_SANDBOX``). Live mode still requires the university to confirm an
eligible merchant account (blueprint SS9: Stripe does not list Cameroon as
a supported business location). Webhook signatures are verified against
the raw body using ``STRIPE_WEBHOOK_SECRET``.
"""
import json
from decimal import Decimal

from django.conf import settings

from core.exceptions import BusinessError

from .base import InitResult, PaymentProvider, VerifyResult


def _stripe():
    try:
        import stripe
    except ImportError as exc:  # pragma: no cover
        raise BusinessError("The stripe package is not installed.",
                            code="PROVIDER_NOT_CONFIGURED") from exc
    if not settings.STRIPE_SECRET_KEY:
        raise BusinessError(
            "Stripe is disabled: confirm merchant eligibility and set "
            "STRIPE_SECRET_KEY first.", code="PROVIDER_DISABLED")
    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


# Currencies with no minor unit: Stripe expects unit_amount in the major
# unit for these (XAF 60,000 is 60000, not 6000000).
ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF",
    "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}


def _multiplier(currency: str) -> int:
    return 1 if currency.upper() in ZERO_DECIMAL_CURRENCIES else 100


class StripeProvider(PaymentProvider):
    name = "stripe"

    def initialize(self, *, attempt, callback_url, customer):
        stripe = _stripe()
        unit_amount = int(
            (Decimal(str(attempt.amount)) * _multiplier(attempt.currency)).to_integral_value())
        try:
            session = stripe.checkout.Session.create(
                mode="payment",
                success_url=callback_url,
                cancel_url=callback_url,
                client_reference_id=attempt.reference,
                customer_email=customer.get("email") or None,
                metadata={"reference": attempt.reference,
                          "invoice_id": str(attempt.invoice_id)},
                line_items=[{
                    "quantity": 1,
                    "price_data": {
                        "currency": attempt.currency.lower(),
                        "unit_amount": unit_amount,
                        "product_data": {"name": f"Fee invoice #{attempt.invoice_id}"},
                    },
                }],
            )
        except Exception as exc:  # stripe.error.StripeError and friends
            raise BusinessError(f"Stripe rejected the payment: {exc}",
                                code="PROVIDER_REJECTED") from exc
        return InitResult(provider_reference=session.id,
                          checkout_url=session.url or "", status="pending",
                          raw={"session_id": session.id})

    def verify(self, *, attempt):
        stripe = _stripe()
        if not attempt.provider_reference:
            raise BusinessError("Payment has no Stripe session to verify.",
                                code="PROVIDER_NOT_CONFIGURED")
        try:
            session = stripe.checkout.Session.retrieve(attempt.provider_reference)
        except Exception as exc:
            raise BusinessError(f"Could not verify with Stripe: {exc}",
                                code="PROVIDER_UNREACHABLE") from exc
        if session.payment_status == "paid":
            status = "succeeded"
        elif getattr(session, "status", "") == "expired":
            status = "failed"
        else:
            status = "pending"
        amount_total = Decimal(session.amount_total or 0) / _multiplier(
            session.currency or attempt.currency)
        return VerifyResult(
            status=status, reference=attempt.reference,
            provider_reference=attempt.provider_reference,
            amount=str(amount_total),
            currency=(session.currency or "").upper(), raw={})

    def parse_webhook(self, request):
        stripe = _stripe()
        signature = request.headers.get("Stripe-Signature", "")
        try:
            event = stripe.Webhook.construct_event(
                request.body, signature, settings.STRIPE_WEBHOOK_SECRET)
        except Exception as exc:  # signature / payload errors
            raise BusinessError("Invalid Stripe webhook signature.",
                                code="WEBHOOK_SIGNATURE_INVALID") from exc
        obj = event["data"]["object"]
        status = ""
        if event["type"] == "checkout.session.completed":
            status = "succeeded" if obj.get("payment_status") == "paid" else "pending"
        payload = json.loads(request.body.decode() or "{}")
        return {
            "event_id": event["id"],
            "event_type": event["type"],
            "reference": (obj.get("metadata") or {}).get(
                "reference", obj.get("client_reference_id", "")),
            "status": status,
            "payload": payload,
        }
