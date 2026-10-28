"""Sandbox provider configuration tests.

Offline contract tests pin the real Stripe/Notch Pay adapter behaviour
(endpoints, keys, currency handling) without any network calls. The key
test runs in a subprocess so the autouse mock fixture in conftest.py
cannot mask backend/.env.
"""
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_sandbox_keys_loaded_from_env():
    """backend/.env carries real test keys (never live keys)."""
    backend = Path(__file__).resolve().parents[1]
    if not (backend / ".env").exists():
        pytest.skip("backend/.env not present (CI runs without sandbox keys)")
    code = (
        "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings');"
        "import django; django.setup();"
        "from django.conf import settings;"
        "assert settings.PAYMENT_PROVIDER == 'notchpay', settings.PAYMENT_PROVIDER;"
        "assert settings.PAYMENT_ALLOW_MOCK is False;"
        "assert settings.NOTCHPAY_PUBLIC_KEY.startswith('pk_test.'), 'notchpay public';"
        "assert settings.NOTCHPAY_SECRET_KEY.startswith('sk_test.'), 'notchpay private';"
        "assert settings.NOTCHPAY_HASH_KEY.startswith('hsk_test.'), 'notchpay hash';"
        "assert settings.NOTCHPAY_SANDBOX is True;"
        "assert settings.STRIPE_PUBLISHABLE_KEY.startswith('pk_test_'), 'stripe public';"
        "assert settings.STRIPE_SECRET_KEY.startswith('sk_test_'), 'stripe secret';"
        "assert settings.STRIPE_SANDBOX is True;"
        "print('SANDBOX KEYS OK')"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=backend,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "SANDBOX KEYS OK" in proc.stdout


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.content = b"{}"

    def json(self):
        return self._payload


def test_notchpay_adapter_matches_live_docs(settings, monkeypatch):
    """Initialize/verify use the PUBLIC key on /payments endpoints (docs)."""
    import finance.providers.notchpay as notchpay_module
    from core.exceptions import BusinessError
    from finance.providers.notchpay import NotchPayProvider

    settings.NOTCHPAY_PUBLIC_KEY = "pk_test_dummy"
    settings.NOTCHPAY_SECRET_KEY = "sk_test_dummy_private"
    settings.NOTCHPAY_HASH_KEY = ""
    settings.NOTCHPAY_BASE_URL = "https://api.notchpay.co"
    settings.NOTCHPAY_WEBHOOK_URL = ""

    attempt = SimpleNamespace(
        amount=Decimal("60000"), currency="XAF", reference="PAY-TEST123",
        invoice_id=7, channel="", provider_reference="")
    captured = {}

    def fake_post(url, json, headers, timeout):  # noqa: A002 - mirrors requests API
        captured.update(url=url, json=json, headers=headers)
        return _FakeResp(201, {
            "status": "Accepted",
            "transaction": {"reference": "trx.test123", "status": "pending"},
            "authorization_url": "https://pay.notchpay.co/abc"})

    monkeypatch.setattr(notchpay_module.requests, "post", fake_post)
    provider = NotchPayProvider()
    result = provider.initialize(
        attempt=attempt, callback_url="http://localhost:5173/payments/return",
        customer={"name": "Sam Student", "email": "sam@test.edu", "phone": ""})
    assert captured["url"] == "https://api.notchpay.co/payments/initialize"
    assert captured["headers"]["Authorization"] == "pk_test_dummy"
    assert captured["json"]["reference"] == "PAY-TEST123"
    assert captured["json"]["amount"] == 60000
    assert isinstance(captured["json"]["amount"], int)  # XAF: no decimals
    assert captured["json"]["email"] == "sam@test.edu"
    assert result.provider_reference == "trx.test123"
    assert result.checkout_url == "https://pay.notchpay.co/abc"
    assert result.status == "pending"

    attempt.provider_reference = "trx.test123"

    def fake_get(url, headers, timeout):
        captured.update(verify_url=url, verify_headers=headers)
        return _FakeResp(200, {"payment": {
            "reference": "trx.test123", "status": "complete",
            "amount": 60000, "currency": "XAF"}})

    monkeypatch.setattr(notchpay_module.requests, "get", fake_get)
    verified = provider.verify(attempt=attempt)
    assert captured["verify_url"] == "https://api.notchpay.co/payments/trx.test123"
    assert verified.status == "succeeded"  # provider "complete" -> succeeded
    assert verified.amount == "60000"
    assert verified.currency == "XAF"

    # Webhook: headerless is tolerated only because callers re-verify;
    # merchant_reference (our PAY-...) wins so the attempt is found.
    body = json.dumps({
        "id": "whk.1", "event": "payment.complete",
        "data": {"reference": "trx.test123", "merchant_reference": "PAY-TEST123",
                 "status": "complete", "amount": 60000,
                 "currency": "XAF"}}).encode()
    parsed = provider.parse_webhook(SimpleNamespace(body=body, headers={}))
    assert parsed["event_id"] == "whk.1"
    assert parsed["reference"] == "PAY-TEST123"
    assert parsed["provider_reference"] == "trx.test123"
    assert parsed["status"] == "succeeded"

    # A present-but-wrong signature is rejected.
    settings.NOTCHPAY_HASH_KEY = "hsk_test_dummy"
    with pytest.raises(BusinessError):
        provider.parse_webhook(SimpleNamespace(
            body=body, headers={"X-Notchpay-Signature": "deadbeef"}))


def test_stripe_zero_decimal_xaf(monkeypatch):
    """XAF is zero-decimal: 60,000 XAF is unit_amount 60000, not x100."""
    import finance.providers.stripe as stripe_module
    from finance.providers.stripe import StripeProvider

    captured = {}
    session = SimpleNamespace(
        id="cs_test_123", url="https://checkout.stripe.com/pay/cs_test_123",
        payment_status="paid", amount_total=60000, currency="xaf",
        status="complete")

    def fake_create(**kwargs):
        captured.update(kwargs)
        return session

    fake_stripe = SimpleNamespace(checkout=SimpleNamespace(Session=SimpleNamespace(
        create=fake_create, retrieve=lambda session_id: session)))
    monkeypatch.setattr(stripe_module, "_stripe", lambda: fake_stripe)

    provider = StripeProvider()
    attempt = SimpleNamespace(
        amount=Decimal("60000"), currency="XAF", reference="PAY-TEST123",
        invoice_id=7, provider_reference="cs_test_123")
    result = provider.initialize(
        attempt=attempt, callback_url="http://localhost:5173/payments/return",
        customer={"email": "sam@test.edu"})
    price = captured["line_items"][0]["price_data"]
    assert price["currency"] == "xaf"
    assert price["unit_amount"] == 60000
    assert captured["client_reference_id"] == "PAY-TEST123"
    assert captured["metadata"]["reference"] == "PAY-TEST123"
    assert result.provider_reference == "cs_test_123"

    verified = provider.verify(attempt=attempt)
    assert verified.status == "succeeded"
    assert verified.amount == "60000"
    assert verified.currency == "XAF"

    # USD keeps cents: 10.50 -> 1050 both ways.
    usd_session = SimpleNamespace(
        id="cs_test_usd", url="https://checkout.stripe.com/pay/cs_test_usd",
        payment_status="paid", amount_total=1050, currency="usd",
        status="complete")

    def fake_create_usd(**kwargs):
        captured.update(kwargs)
        return usd_session

    fake_stripe.checkout.Session.create = fake_create_usd
    fake_stripe.checkout.Session.retrieve = lambda session_id: usd_session
    usd_attempt = SimpleNamespace(
        amount=Decimal("10.50"), currency="USD", reference="PAY-USD1",
        invoice_id=8, provider_reference="cs_test_usd")
    provider.initialize(attempt=usd_attempt,
                        callback_url="http://localhost:5173/payments/return",
                        customer={})
    assert captured["line_items"][0]["price_data"]["unit_amount"] == 1050
    assert provider.verify(attempt=usd_attempt).amount == "10.5"

