"""Notch Pay adapter – hosted checkout + server-side verification.

Docs: https://developer-guide.vercel.app/docs/payments/collect ,
/verify and /getting-started/webhooks

NOTE (blueprint SS9): the public docs describe a hash for webhook
validation but do not fully specify a stable signing algorithm. Webhook
requests are therefore *never* trusted on their own – every event is
re-verified against the provider API (``GET /payments/{reference}``)
before any credit is granted. Confirm the exact webhook-hash procedure
with the current provider documentation before production.
"""
import hashlib
import hmac
import json

import requests
from django.conf import settings

from core.exceptions import BusinessError

from .base import InitResult, PaymentProvider, VerifyResult

STATUS_MAP = {
    "complete": "succeeded", "completed": "succeeded", "succeeded": "succeeded",
    "successful": "succeeded", "paid": "succeeded",
    "pending": "pending", "processing": "pending", "created": "pending",
    "failed": "failed", "canceled": "cancelled", "cancelled": "cancelled",
    "expired": "failed",
}


class NotchPayProvider(PaymentProvider):
    name = "notchpay"

    def _headers(self):
        # Per Notch Pay docs, payment initialize/verify calls authenticate
        # with the PUBLIC key. The private key (NOTCHPAY_SECRET_KEY) is
        # reserved for high-risk endpoints (e.g. transfers) and is never
        # sent for hosted-checkout payments.
        if not settings.NOTCHPAY_PUBLIC_KEY:
            raise BusinessError(
                "Notch Pay is not configured (NOTCHPAY_PUBLIC_KEY missing).",
                code="PROVIDER_NOT_CONFIGURED")
        return {"Authorization": settings.NOTCHPAY_PUBLIC_KEY,
                "Content-Type": "application/json"}

    def initialize(self, *, attempt, callback_url, customer):
        # XAF (and other zero-decimal currencies) must be sent as integers.
        amount = attempt.amount
        amount = int(amount) if amount == amount.to_integral_value() else float(amount)
        payload = {
            "email": customer.get("email", ""),
            "amount": amount,
            "currency": attempt.currency,
            "reference": attempt.reference,
            "callback": callback_url,
            "description": f"Fee payment invoice #{attempt.invoice_id}",
        }
        if customer.get("name"):
            payload["name"] = customer["name"]
        if customer.get("phone"):
            payload["phone"] = customer["phone"]
        if settings.NOTCHPAY_WEBHOOK_URL:
            payload["webhook_url"] = settings.NOTCHPAY_WEBHOOK_URL
        if attempt.channel:
            payload["channel"] = attempt.channel
        try:
            resp = requests.post(
                f"{settings.NOTCHPAY_BASE_URL.rstrip('/')}/payments/initialize",
                json=payload, headers=self._headers(), timeout=30)
            data = resp.json() if resp.content else {}
        except requests.RequestException as exc:
            raise BusinessError(f"Could not reach Notch Pay: {exc}",
                                code="PROVIDER_UNREACHABLE") from exc
        if resp.status_code >= 400:
            raise BusinessError(
                f"Notch Pay rejected the payment: {data.get('message', resp.status_code)}",
                code="PROVIDER_REJECTED")
        transaction = data.get("transaction", {}) or data
        return InitResult(
            provider_reference=transaction.get("reference", attempt.reference),
            checkout_url=data.get("authorization_url", ""),
            status=STATUS_MAP.get(str(transaction.get("status", "")).lower(), "pending"),
            raw=data,
        )

    def verify(self, *, attempt):
        reference = attempt.provider_reference or attempt.reference
        try:
            resp = requests.get(
                f"{settings.NOTCHPAY_BASE_URL.rstrip('/')}/payments/{reference}",
                headers=self._headers(), timeout=30)
            data = resp.json() if resp.content else {}
        except requests.RequestException as exc:
            raise BusinessError(f"Could not verify with Notch Pay: {exc}",
                                code="PROVIDER_UNREACHABLE") from exc
        if resp.status_code >= 400:
            raise BusinessError(
                f"Notch Pay could not verify the payment: "
                f"{data.get('message', resp.status_code)}",
                code="PROVIDER_REJECTED")
        # Verify responses wrap the object as "payment"/"transaction";
        # list responses expose the fields directly - support every shape.
        transaction = (
            data.get("transaction") or data.get("payment") or data
        )
        amounts = transaction.get("amounts") or {}
        amount = transaction.get("amount", amounts.get("total", ""))
        currency = transaction.get("currency", amounts.get("currency", ""))
        return VerifyResult(
            status=STATUS_MAP.get(str(transaction.get("status", "")).lower(), "pending"),
            reference=transaction.get("merchant_reference", attempt.reference),
            provider_reference=transaction.get("reference", reference),
            amount=str(amount),
            currency=str(currency),
            raw=data,
        )

    def parse_webhook(self, request):
        # Best-effort authenticity check, never the sole line of defence:
        # if Notch Pay sends an HMAC-SHA256 signature header it must match
        # the hash key; a missing header is accepted only because callers
        # always re-verify via ``verify`` before crediting (module docstring).
        if settings.NOTCHPAY_HASH_KEY:
            signature = (
                request.headers.get("X-Notchpay-Signature")
                or request.headers.get("Notchpay-Signature")
                or ""
            )
            if signature:
                expected = hmac.new(
                    settings.NOTCHPAY_HASH_KEY.encode(),
                    request.body,
                    hashlib.sha256,
                ).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise BusinessError("Invalid Notch Pay webhook signature.",
                                        code="WEBHOOK_SIGNATURE_INVALID")
        try:
            payload = json.loads(request.body.decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = {}
        data = payload.get("data", {}) or {}
        return {
            "event_id": payload.get("id") or data.get("trx_id") or data.get("reference", ""),
            "event_type": payload.get("event", "payment.updated"),
            "reference": data.get("merchant_reference") or data.get("reference", ""),
            "provider_reference": data.get("reference", ""),
            "status": STATUS_MAP.get(str(data.get("status", "")).lower(), ""),
            "payload": payload,
        }
