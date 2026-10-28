"""One-off sandbox credential check (auth-only, creates NO payments).

Run:  py -3.14 verify_sandbox.py
Reads keys from backend/.env via Django settings; prints only masked keys.
"""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()
from django.conf import settings  # noqa: E402


def mask(value):
    return f"{value[:8]}...{value[-4:]} (len={len(value)})" if value else "<EMPTY>"


print("PAYMENT_PROVIDER    =", settings.PAYMENT_PROVIDER)
print("PAYMENT_ALLOW_MOCK  =", settings.PAYMENT_ALLOW_MOCK)
print("NOTCHPAY_SANDBOX    =", settings.NOTCHPAY_SANDBOX)
print("NOTCHPAY_BASE_URL   =", settings.NOTCHPAY_BASE_URL)
print("NOTCHPAY_PUBLIC_KEY =", mask(settings.NOTCHPAY_PUBLIC_KEY))
print("NOTCHPAY_SECRET_KEY =", mask(settings.NOTCHPAY_SECRET_KEY))
print("NOTCHPAY_HASH_KEY   =", mask(settings.NOTCHPAY_HASH_KEY))
print("NOTCHPAY_WEBHOOK_URL set:", bool(settings.NOTCHPAY_WEBHOOK_URL))
print("STRIPE_SANDBOX      =", settings.STRIPE_SANDBOX)
print("STRIPE_PUBLISHABLE  =", mask(settings.STRIPE_PUBLISHABLE_KEY))
print("STRIPE_SECRET_KEY   =", mask(settings.STRIPE_SECRET_KEY))
print("STRIPE_WEBHOOK_SECRET set:", bool(settings.STRIPE_WEBHOOK_SECRET))

from finance.providers.base import get_provider  # noqa: E402

for name in ("notchpay", "stripe", "mock"):
    try:
        provider = get_provider(name)
        print(f"provider {name}: OK ({type(provider).__name__})")
    except Exception as exc:  # noqa: BLE001
        print(f"provider {name}: ERROR {exc}")

# Stripe auth-only check: Balance/Account retrieve creates nothing.
try:
    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY
    balance = stripe.Balance.retrieve()
    account = stripe.Account.retrieve()
    print("Stripe Balance.retrieve OK: livemode =", balance.livemode,
          "| account:", account.id,
          "| charges_enabled:", account.charges_enabled,
          "| country:", account.country)
except Exception as exc:  # noqa: BLE001
    print("Stripe check FAILED:", type(exc).__name__, str(exc)[:300])

# Notch Pay auth-only check: merchant info + list payments (GET, no creates).
# Per docs, API calls authorize with the PUBLIC key; sandbox mode shows
# "env": "sandbox" on GET /.
try:
    import requests

    for path in ("/", "/payments"):
        resp = requests.get(
            f"{settings.NOTCHPAY_BASE_URL.rstrip('/')}{path}",
            headers={"Authorization": settings.NOTCHPAY_PUBLIC_KEY}, timeout=20)
        body = resp.text[:220].replace("\n", " ")
        print(f"NotchPay GET {path} (public key) -> HTTP {resp.status_code}: {body}")
except Exception as exc:  # noqa: BLE001
    print("NotchPay check FAILED (network?):", type(exc).__name__, str(exc)[:240])
