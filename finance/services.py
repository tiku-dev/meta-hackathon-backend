"""Finance services: invoicing, payment initiation and idempotent
reconciliation (blueprint SS9). Internal records are the source of truth.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.audit import audit
from core.exceptions import BusinessError

from .models import (
    FeeSchedule, Invoice, OfflinePaymentAdjustment, PaymentAttempt,
    PaymentEvent, Receipt,
)
from .providers.base import get_provider


def find_fee_schedule(student, semester) -> FeeSchedule | None:
    """Most specific active schedule: program+level > program > unit > any."""
    qs = FeeSchedule.objects.filter(is_active=True)
    if semester is not None:
        scoped = qs.filter(semester=semester)
        if scoped.exists():
            qs = scoped
    if student.program_id:
        for candidate in (
            qs.filter(program_id=student.program_id, level_id=student.current_level_id),
            qs.filter(program_id=student.program_id, level__isnull=True),
            qs.filter(academic_unit_id=student.program.department_id),
            qs.filter(program__isnull=True, academic_unit__isnull=True),
        ):
            found = candidate.order_by("-created_at").first()
            if found:
                return found
    return qs.order_by("-created_at").first()


def ensure_invoice(student, semester, *, actor=None) -> Invoice:
    """Get-or-create the student's invoice for a semester from the schedule."""
    invoice = Invoice.objects.filter(student=student, semester=semester).first()
    if invoice:
        return invoice
    schedule = find_fee_schedule(student, semester)
    if schedule is None:
        raise BusinessError(
            "No active fee schedule matches this student/semester. "
            "Ask finance to configure one.", code="NO_FEE_SCHEDULE")
    invoice = Invoice.objects.create(
        student=student, fee_schedule=schedule,
        academic_year=semester.academic_year if semester else schedule.academic_year,
        semester=semester, currency=schedule.currency, total_due=schedule.amount,
        due_date=semester.fee_deadline if semester else None,
    )
    audit("invoice.created", actor=actor, entity=invoice,
          summary={"student_id": student.id, "total_due": str(schedule.amount)})
    return invoice


def payment_options() -> dict:
    """Payment methods the UI may offer, based on server configuration.

    The browser must not guess: the local simulator is only shown when
    ``PAYMENT_ALLOW_MOCK`` is enabled, while real providers are only shown
    when their server-side credentials are configured.
    """
    default_provider = str(
        getattr(settings, "PAYMENT_PROVIDER", "mock") or "mock").lower()
    return {
        "default_provider": default_provider,
        "providers": {
            "mock": {
                "enabled": bool(getattr(settings, "PAYMENT_ALLOW_MOCK", False)),
                "sandbox": True,
            },
            "notchpay": {
                "enabled": bool(getattr(settings, "NOTCHPAY_PUBLIC_KEY", "")),
                "sandbox": bool(getattr(settings, "NOTCHPAY_SANDBOX", False)),
            },
            "stripe": {
                "enabled": bool(getattr(settings, "STRIPE_SECRET_KEY", "")),
                "sandbox": bool(getattr(settings, "STRIPE_SANDBOX", False)),
            },
        },
    }


def find_attempt(reference: str) -> PaymentAttempt | None:
    """Resolve an attempt by internal *or* provider reference.

    Provider return URLs carry the provider's own transaction id
    (``reference=trx...`` for Notch Pay, ``cs_...`` for Stripe), while our
    API, webhooks and UI use the internal ``PAY-...`` reference. Accepting
    both keeps status checks working no matter which id the caller holds;
    ownership is still enforced by the caller afterwards.
    """
    if not reference:
        return None
    attempt = PaymentAttempt.objects.filter(reference=reference).first()
    if attempt is None:
        attempt = PaymentAttempt.objects.filter(
            provider_reference=reference).first()
    return attempt


def payment_threshold_met(student, semester):
    """(bool, info) – has *verified* paid amount reached the gate?"""
    invoice = Invoice.objects.filter(student=student, semester=semester).first()
    if invoice is None:
        return False, {"invoice_id": None, "amount_paid": "0", "required": None,
                       "balance": None}
    schedule = invoice.fee_schedule
    required = (
        schedule.required_for_registration(invoice.total_due)
        if schedule else invoice.total_due
    )
    return invoice.amount_paid >= required, {
        "invoice_id": invoice.id,
        "amount_paid": str(invoice.amount_paid),
        "required": str(required),
        "total_due": str(invoice.total_due),
        "balance": str(invoice.balance),
        "currency": invoice.currency,
    }


@transaction.atomic
def start_payment(actor, invoice: Invoice, *, amount=None, channel="",
                  provider_name=None, callback_url=""):
    """Create a pending attempt *before* calling the provider (SS9 step 2-3)."""
    if invoice.status in ("paid", "waived", "cancelled"):
        raise BusinessError(f"Invoice is {invoice.status}; nothing to pay.",
                            code="INVOICE_NOT_PAYABLE")
    amount = Decimal(amount) if amount is not None else invoice.balance
    if amount <= 0:
        raise BusinessError("Payment amount must be positive.", code="INVALID_AMOUNT")
    if amount > invoice.balance:
        raise BusinessError("Payment amount exceeds the invoice balance.",
                            code="AMOUNT_EXCEEDS_BALANCE")
    provider = get_provider(provider_name)
    reference = f"PAY-{uuid.uuid4().hex[:16].upper()}"
    attempt = PaymentAttempt.objects.create(
        invoice=invoice, provider=provider.name, reference=reference,
        amount=amount, currency=invoice.currency, status="pending",
        channel=channel, initiated_by=actor,
    )
    result = provider.initialize(
        attempt=attempt,
        callback_url=callback_url,
        customer={"name": invoice.student.full_name, "email": invoice.student.email,
                  "phone": invoice.student.phone},
    )
    attempt.provider_reference = result.provider_reference
    attempt.checkout_url = result.checkout_url
    attempt.save(update_fields=["provider_reference", "checkout_url", "updated_at"])
    audit("payment.initiated", actor=actor, entity=attempt,
          summary={"invoice_id": invoice.id, "amount": str(amount),
                   "provider": provider.name})
    return attempt


@transaction.atomic
def reconcile_attempt(attempt: PaymentAttempt, *, verified_status: str,
                      provider_reference="", amount="", currency="",
                      source="verify") -> PaymentAttempt:
    """Apply a *server-verified* provider status to an attempt, idempotently.

    Credits the invoice exactly once and issues exactly one receipt.
    Amount/currency/reference mismatches park the attempt in needs_review.
    """
    attempt = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
    if attempt.status == "succeeded":
        return attempt  # already credited – duplicate webhook/verify is a no-op

    problems = []
    if provider_reference and attempt.provider_reference \
            and provider_reference != attempt.provider_reference:
        problems.append("provider reference mismatch")
    if amount:
        try:
            if Decimal(str(amount)) != attempt.amount:
                problems.append("amount mismatch")
        except Exception:  # noqa: BLE001
            problems.append("unparseable amount")
    if currency and currency.upper() != attempt.currency.upper():
        problems.append("currency mismatch")

    if verified_status == "succeeded" and problems:
        attempt.status = "needs_review"
        attempt.failure_reason = "; ".join(problems)
        attempt.save(update_fields=["status", "failure_reason", "updated_at"])
        audit("payment.needs_review", entity=attempt, summary={"problems": problems})
        return attempt

    if verified_status == "succeeded":
        invoice = Invoice.objects.select_for_update().get(pk=attempt.invoice_id)
        if invoice.status in ("paid", "waived", "cancelled") \
                or attempt.amount > invoice.balance:
            # A second *verified* payment against an invoice that is
            # already settled (e.g. the student completed two checkouts)
            # must never over-credit it: park for a finance refund review.
            attempt.status = "needs_review"
            attempt.failure_reason = (
                "Verified payment exceeds the invoice balance "
                "(possible duplicate payment).")
            attempt.save(update_fields=["status", "failure_reason", "updated_at"])
            audit("payment.needs_review", entity=attempt,
                  summary={"problems": ["exceeds remaining invoice balance"],
                           "invoice_id": invoice.id, "source": source})
            return attempt
        invoice.amount_paid += attempt.amount
        invoice.refresh_status()
        invoice.save(update_fields=["amount_paid", "status", "updated_at"])
        attempt.status = "succeeded"
        attempt.paid_at = timezone.now()
        attempt.save(update_fields=["status", "paid_at", "updated_at"])
        Receipt.objects.get_or_create(
            payment_attempt=attempt,
            defaults={
                "invoice": invoice,
                "receipt_number": f"RCT-{attempt.reference}",
                "amount": attempt.amount,
                "currency": attempt.currency,
            },
        )
        audit("payment.succeeded", entity=attempt,
              summary={"invoice_id": invoice.id, "amount": str(attempt.amount),
                       "source": source})
        from communication.services import notify

        notify(invoice.student.user, "payment", "Payment verified",
               f"Your payment of {attempt.amount} {attempt.currency} was verified. "
               f"Invoice balance: {invoice.balance} {invoice.currency}.")
    elif verified_status in ("failed", "cancelled"):
        attempt.status = verified_status
        attempt.save(update_fields=["status", "updated_at"])
        audit(f"payment.{verified_status}", entity=attempt, summary={"source": source})
    return attempt


@transaction.atomic
def handle_webhook(provider_name: str, parsed: dict) -> PaymentEvent:
    """Deduplicate then reconcile a provider event (SS9 steps 5-7)."""
    event_id = parsed.get("event_id") or f"{provider_name}-{parsed.get('reference', '')}"
    event, created = PaymentEvent.objects.get_or_create(
        provider=provider_name, event_id=event_id,
        defaults={"event_type": parsed.get("event_type", ""),
                  "reference": parsed.get("reference", ""),
                  "payload": parsed.get("payload", {})},
    )
    if not created and event.processed:
        event.outcome = "duplicate ignored"
        event.save(update_fields=["outcome", "updated_at"])
        return event
    attempt = find_attempt(parsed.get("reference", ""))
    if attempt is None and parsed.get("provider_reference"):
        attempt = PaymentAttempt.objects.filter(
            provider_reference=parsed["provider_reference"]).first()
    if attempt is None:
        event.outcome = "no matching attempt"
        event.processed = True
        event.save(update_fields=["outcome", "processed", "updated_at"])
        return event
    # Never trust the webhook body alone: verify server-side.
    verified = get_provider(provider_name).verify(attempt=attempt)
    reconcile_attempt(
        attempt, verified_status=verified.status,
        provider_reference=verified.provider_reference,
        amount=verified.amount, currency=verified.currency, source="webhook")
    event.processed = True
    event.outcome = f"attempt {attempt.reference} -> {attempt.status}"
    event.save(update_fields=["outcome", "processed", "updated_at"])
    return event


@transaction.atomic
def approve_offline_adjustment(actor, adjustment: OfflinePaymentAdjustment,
                               *, approve: bool, reason=""):
    if adjustment.status != "pending":
        raise BusinessError("This adjustment has already been decided.",
                            code="ALREADY_DECIDED")
    adjustment.status = "approved" if approve else "rejected"
    adjustment.approved_by = actor
    adjustment.approved_at = timezone.now()
    adjustment.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    if approve:
        invoice = Invoice.objects.select_for_update().get(pk=adjustment.invoice_id)
        invoice.amount_paid += adjustment.amount
        invoice.refresh_status()
        invoice.save(update_fields=["amount_paid", "status", "updated_at"])
        from communication.services import notify

        notify(invoice.student.user, "payment", "Offline payment recorded",
               f"An offline payment of {adjustment.amount} {adjustment.currency} "
               f"was approved. Balance: {invoice.balance} {invoice.currency}.")
    audit("finance.offline_adjustment_decided", actor=actor, entity=adjustment,
          reason=reason, summary={"approved": approve, "amount": str(adjustment.amount)})
    return adjustment
