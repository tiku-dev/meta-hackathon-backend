"""Finance: fee schedules, invoices, payment attempts/events, receipts."""
from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel

CURRENCIES = [("USD", "USD ($)"), ("XAF", "XAF (Central African CFA franc)")]
# One-time fee a NEW student must pay before any tuition/fee payment.
# Admin sets the amount on a FeeSchedule with this fee_type; paid once ever.
REGISTRATION_FEE_TYPE = "registration_fee"
PAYMENT_STATUSES = [
    ("created", "Created"), ("pending", "Pending"), ("succeeded", "Succeeded"),
    ("failed", "Failed"), ("cancelled", "Cancelled"), ("refunded", "Refunded"),
    ("needs_review", "Needs review"),
]


class FeeSchedule(TimeStampedModel):
    """Configurable fee for a scope + term (blueprint SS4). Never hard-coded."""

    academic_unit = models.ForeignKey(
        "academics.AcademicUnit", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="fee_schedules")
    program = models.ForeignKey(
        "academics.Program", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="fee_schedules")
    level = models.ForeignKey(
        "academics.Level", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="fee_schedules")
    academic_year = models.ForeignKey(
        "academics.AcademicYear", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="fee_schedules")
    semester = models.ForeignKey(
        "academics.Semester", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="fee_schedules")
    fee_type = models.CharField(max_length=40, default="registration")
    student_category = models.CharField(max_length=32, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="USD")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    # Enrollment gate: percent of the invoice that must be *verified paid*…
    registration_threshold_type = models.CharField(
        max_length=8, choices=[("percent", "Percent"), ("amount", "Fixed amount")],
        default="percent")
    registration_threshold_value = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("100"))
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        scope = self.program or self.academic_unit or "University"
        return f"{scope} {self.fee_type}: {self.amount} {self.currency}"

    def required_for_registration(self, total_due: Decimal) -> Decimal:
        if self.registration_threshold_type == "amount":
            return min(self.registration_threshold_value, total_due)
        return (total_due * self.registration_threshold_value / Decimal("100")).quantize(
            Decimal("0.01"))


class Invoice(TimeStampedModel):
    """A student's obligation. Amount/currency/FX are snapshotted at issue."""

    STATUS_CHOICES = [
        ("unpaid", "Unpaid"), ("partially_paid", "Partially paid"),
        ("paid", "Paid"), ("overdue", "Overdue"), ("waived", "Waived"),
        ("cancelled", "Cancelled"),
    ]

    student = models.ForeignKey(
        "accounts.StudentProfile", on_delete=models.CASCADE, related_name="invoices")
    fee_schedule = models.ForeignKey(
        FeeSchedule, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoices")
    academic_year = models.ForeignKey(
        "academics.AcademicYear", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoices")
    semester = models.ForeignKey(
        "academics.Semester", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="invoices")
    currency = models.CharField(max_length=3, choices=CURRENCIES, default="USD")
    total_due = models.DecimalField(max_digits=14, decimal_places=2)
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2,
                                      default=Decimal("0"))
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="unpaid")
    due_date = models.DateTimeField(null=True, blank=True)
    # Explicit alternate-currency quote (e.g. USD equivalent), admin-approved.
    alt_currency = models.CharField(max_length=3, blank=True)
    alt_amount = models.DecimalField(max_digits=14, decimal_places=2,
                                     null=True, blank=True)
    fx_rate = models.DecimalField(max_digits=18, decimal_places=6,
                                  null=True, blank=True)
    fx_quoted_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invoice#{self.id} {self.student} {self.total_due} {self.currency}"

    @property
    def balance(self) -> Decimal:
        return max(Decimal("0"), self.total_due - self.amount_paid)

    def refresh_status(self):
        if self.status in ("waived", "cancelled"):
            return
        if self.amount_paid >= self.total_due:
            self.status = "paid"
        elif self.amount_paid > 0:
            self.status = "partially_paid"
        else:
            self.status = "unpaid"


class PaymentAttempt(TimeStampedModel):
    """One attempt to pay an invoice through a provider. Internal source of truth."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                related_name="payment_attempts")
    provider = models.CharField(max_length=24, default="mock", db_index=True)
    reference = models.CharField(max_length=64, unique=True, db_index=True,
                                 help_text="Internal unique reference.")
    provider_reference = models.CharField(max_length=128, blank=True, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=16, choices=PAYMENT_STATUSES, default="created",
                              db_index=True)
    channel = models.CharField(max_length=32, blank=True,
                               help_text="e.g. cm.mtn, cm.orange, card")
    checkout_url = models.URLField(blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="payment_attempts")
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} {self.amount} {self.currency} ({self.status})"


class RegistrationFeeRecord(TimeStampedModel):
    """The one-time registration fee owed by a NEW student.

    One row per student, ever (``OneToOneField``): the DB itself answers
    "has this student paid the registration fee before?" — once ``status``
    is ``paid`` the student is never charged again. ``is_new_student``
    snapshots applicability at issue time: students who were already
    paying tuition when the fee was rolled out are grandfathered
    (``required=False``) and are never charged retroactively. Statuses:
    ``not_required`` (no admin fee configured), ``unpaid``, ``paid``,
    ``waived`` (finance waived it with a recorded reason).
    """

    STATUS_CHOICES = [
        ("not_required", "Not required"), ("unpaid", "Unpaid"),
        ("paid", "Paid"), ("waived", "Waived"),
    ]

    student = models.OneToOneField(
        "accounts.StudentProfile", on_delete=models.CASCADE,
        related_name="registration_fee_record")
    required = models.BooleanField(default=True)
    is_new_student = models.BooleanField(
        default=True,
        help_text="False for grandfathered students who never owed the fee.")
    amount = models.DecimalField(max_digits=14, decimal_places=2,
                                 default=Decimal("0"))
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES,
                              default="unpaid", db_index=True)
    fee_schedule = models.ForeignKey(
        FeeSchedule, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="registration_fee_records")
    paid_invoice = models.ForeignKey(
        Invoice, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="registration_fee_records",
        help_text="The (semester-less) invoice the fee was paid through.")
    paid_at = models.DateTimeField(null=True, blank=True)
    waived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="waived_registration_fees")
    waiver_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RegistrationFee {self.student} {self.amount} {self.currency} ({self.status})"

    @property
    def is_paid(self) -> bool:
        return self.status in ("paid", "waived", "not_required")


class PaymentEvent(TimeStampedModel):
    """Raw provider webhook/verification event. Deduped on (provider, event_id)."""

    provider = models.CharField(max_length=24, db_index=True)
    event_id = models.CharField(max_length=128, db_index=True)
    event_type = models.CharField(max_length=80, blank=True)
    reference = models.CharField(max_length=128, blank=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False)
    outcome = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("provider", "event_id")]
        ordering = ["-created_at"]


class Receipt(TimeStampedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="receipts")
    payment_attempt = models.OneToOneField(
        PaymentAttempt, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="receipt")
    receipt_number = models.CharField(max_length=40, unique=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    issued_at = models.DateTimeField(auto_now_add=True)
    file_path = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return self.receipt_number


class OfflinePaymentAdjustment(TimeStampedModel):
    """Approved offline payment (cash/bank) recorded by finance with evidence."""

    STATUS_CHOICES = [
        ("pending", "Pending approval"), ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                related_name="offline_adjustments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    method = models.CharField(max_length=40, help_text="cash, bank transfer, ...")
    evidence = models.CharField(max_length=255, blank=True,
                                help_text="Reference/path of supporting evidence.")
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="offline_adjustments")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="approved_offline_adjustments")
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
