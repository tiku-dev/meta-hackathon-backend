from django.conf import settings
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.audit import audit
from core.exceptions import BusinessError
from core.permissions import IsAdmin, IsFinance, has_role

from .models import (
    FeeSchedule, Invoice, OfflinePaymentAdjustment, PaymentAttempt, Receipt,
    RegistrationFeeRecord,
)
from .providers.base import get_provider
from .serializers import (
    FeeScheduleSerializer, InvoiceSerializer,
    OfflinePaymentAdjustmentSerializer, PaymentAttemptSerializer,
    ReceiptSerializer, RegistrationFeeRecordSerializer,
)
from .services import (
    approve_offline_adjustment, ensure_invoice, ensure_registration_fee_invoice,
    find_attempt, handle_webhook, payment_options, reconcile_attempt,
    registration_fee_status, start_payment, waive_registration_fee,
)


def _get_student(request):
    student = getattr(request.user, "student_profile", None)
    if student is None:
        raise BusinessError("Your account is not linked to a student profile.",
                            code="NOT_A_STUDENT")
    return student


class FeeScheduleViewSet(viewsets.ModelViewSet):
    queryset = FeeSchedule.objects.all()
    serializer_class = FeeScheduleSerializer
    filterset_fields = ["program", "academic_unit", "level", "academic_year",
                        "semester", "fee_type", "is_active"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsFinance()]

    def perform_create(self, serializer):
        serializer.save()
        audit("finance.fee_schedule_saved", actor=self.request.user,
              entity=serializer.instance)

    def perform_update(self, serializer):
        serializer.save()
        audit("finance.fee_schedule_saved", actor=self.request.user,
              entity=serializer.instance)


class InvoiceViewSet(viewsets.ModelViewSet):
    """Admin/finance invoice management (students use /student/fees)."""

    serializer_class = InvoiceSerializer
    filterset_fields = ["student", "semester", "status", "currency"]
    search_fields = ["student__matricule", "student__first_name", "student__last_name"]

    def get_queryset(self):
        qs = Invoice.objects.select_related("student", "fee_schedule").all()
        user = self.request.user
        if has_role(user, "admin", "finance", "registrar"):
            return qs
        if has_role(user, "student"):
            return qs.filter(student__user=user)
        return qs.none()

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsFinance()]

    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        """Create the invoice for a student+semester from the fee schedule."""
        from accounts.models import StudentProfile
        from academics.models import Semester

        student = StudentProfile.objects.filter(
            id=request.data.get("student_id")).first()
        semester = Semester.objects.filter(id=request.data.get("semester_id")).first()
        if student is None or semester is None:
            raise BusinessError("student_id and semester_id are required.",
                                code="INVALID_REQUEST")
        invoice = ensure_invoice(student, semester, actor=request.user)
        return Response(InvoiceSerializer(invoice).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="waive-registration-fee")
    def waive_registration_fee(self, request, pk=None):
        """Waive the student's one-time registration fee (audited)."""
        invoice = self.get_object()
        record = waive_registration_fee(
            request.user, invoice.student,
            reason=request.data.get("reason", ""))
        return Response(RegistrationFeeRecordSerializer(record).data)

    @action(detail=True, methods=["post"])
    def waive(self, request, pk=None):
        invoice = self.get_object()
        invoice.status = "waived"
        invoice.save(update_fields=["status", "updated_at"])
        audit("invoice.waived", actor=request.user, entity=invoice,
              reason=request.data.get("reason", ""))
        return Response(InvoiceSerializer(invoice).data)


class StudentFeesView(APIView):
    """GET /api/student/fees – own invoices, balances, deadlines, eligibility."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from academics.services import current_semester, registration_eligibility

        student = _get_student(request)
        invoices = Invoice.objects.filter(student=student).select_related("fee_schedule")
        semester = current_semester()
        current_invoice = None
        if semester is not None:
            try:
                current_invoice = ensure_invoice(student, semester, actor=request.user)
            except BusinessError:
                current_invoice = invoices.filter(semester=semester).first()
        # One-time registration fee first: surface it (and its payable
        # invoice) ahead of tuition for NEW students who still owe it.
        reg_fee = registration_fee_status(student)
        if reg_fee["required"]:
            reg_invoice = ensure_registration_fee_invoice(
                student, actor=request.user)
            reg_fee["invoice"] = (
                InvoiceSerializer(reg_invoice).data if reg_invoice else None)
            invoices = Invoice.objects.filter(
                student=student).select_related("fee_schedule")
        eligibility = (
            registration_eligibility(student, semester) if semester else None
        )
        return Response({
            "invoices": InvoiceSerializer(invoices, many=True).data,
            "current_invoice": (
                InvoiceSerializer(current_invoice).data if current_invoice else None
            ),
            "registration_fee": reg_fee,
            "registration_deadline": semester.registration_deadline if semester else None,
            "fee_deadline": semester.fee_deadline if semester else None,
            "eligibility": eligibility,
            "payment_options": payment_options(),
        })


class InvoicePaymentCreateView(APIView):
    """POST /api/invoices/:id/payments – start a hosted-checkout payment."""

    permission_classes = [IsAuthenticated]

    def post(self, request, invoice_id):
        invoice = Invoice.objects.filter(id=invoice_id).first()
        if invoice is None:
            raise BusinessError("Invoice not found.", code="INVOICE_NOT_FOUND")
        user = request.user
        if not (has_role(user, "admin", "finance")
                or invoice.student.user_id == user.id):
            raise BusinessError("You can only pay your own invoice.",
                                code="NOT_YOUR_INVOICE")
        attempt = start_payment(
            user, invoice,
            amount=request.data.get("amount"),
            channel=request.data.get("channel", ""),
            provider_name=request.data.get("provider"),
            callback_url=request.data.get(
                "callback_url", f"{settings.FRONTEND_BASE_URL}/payments/return"),
        )
        return Response(PaymentAttemptSerializer(attempt).data,
                        status=status.HTTP_201_CREATED)


class PaymentStatusView(APIView):
    """GET /api/payments/:reference/status – server-verified status.

    ``reference`` may be the internal ``PAY-...`` id or the provider's
    transaction id (``trx...``/``cs_...``) carried by provider return URLs.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, reference):
        attempt = find_attempt(reference)
        if attempt is None:
            raise BusinessError("Payment reference not found.",
                                code="PAYMENT_NOT_FOUND")
        user = request.user
        if not (has_role(user, "admin", "finance")
                or attempt.invoice.student.user_id == user.id):
            raise BusinessError("You can only view your own payments.",
                                code="NOT_YOUR_PAYMENT")
        if attempt.status not in ("succeeded", "failed", "cancelled", "needs_review"):
            verified = get_provider(attempt.provider).verify(attempt=attempt)
            reconcile_attempt(attempt, verified_status=verified.status,
                              provider_reference=verified.provider_reference,
                              amount=verified.amount, currency=verified.currency,
                              source="status-check")
            attempt.refresh_from_db()
        return Response(PaymentAttemptSerializer(attempt).data)


class MockSimulateView(APIView):
    """Sandbox-only helper: simulate the provider confirming a payment.

    Stands in for the provider's server-side confirmation when no real
    Notch Pay sandbox credentials exist. Disabled outside DEBUG and
    clearly labelled; it never runs against real money.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, reference):
        if not getattr(settings, "PAYMENT_ALLOW_MOCK", False):
            raise BusinessError("Sandbox simulation is disabled.",
                                code="PROVIDER_DISABLED")
        attempt = find_attempt(reference)
        if attempt is None:
            raise BusinessError("Payment reference not found.",
                                code="PAYMENT_NOT_FOUND")
        if attempt.provider != "mock":
            raise BusinessError(
                "Sandbox simulation only applies to local mock payments.",
                code="PROVIDER_DISABLED")
        outcome = request.data.get("outcome", "succeeded")
        attempt.status = outcome  # what the 'provider' will report on verify
        attempt.save(update_fields=["status", "updated_at"])
        verified = get_provider("mock").verify(attempt=attempt)
        attempt.status = "pending"  # force reconciliation path to re-apply
        attempt.save(update_fields=["status", "updated_at"])
        reconcile_attempt(attempt, verified_status=verified.status,
                          amount=verified.amount, currency=verified.currency,
                          source="mock-simulation")
        attempt.refresh_from_db()
        audit("payment.mock_simulated", actor=request.user, entity=attempt,
              summary={"outcome": outcome})
        return Response(PaymentAttemptSerializer(attempt).data)


@api_view(["POST"])
@permission_classes([AllowAny])
def notchpay_webhook(request):
    """POST /api/webhooks/notchpay – deduplicated, re-verified, reconciled."""
    parsed = get_provider("notchpay").parse_webhook(request)
    event = handle_webhook("notchpay", parsed)
    return Response({"received": True, "outcome": event.outcome})


@api_view(["POST"])
@permission_classes([AllowAny])
def stripe_webhook(request):
    """POST /api/webhooks/stripe – only when an eligible account exists."""
    parsed = get_provider("stripe").parse_webhook(request)
    event = handle_webhook("stripe", parsed)
    return Response({"received": True, "outcome": event.outcome})


class OfflineAdjustmentViewSet(viewsets.ModelViewSet):
    queryset = OfflinePaymentAdjustment.objects.select_related("invoice").all()
    serializer_class = OfflinePaymentAdjustmentSerializer
    permission_classes = [IsFinance]
    filterset_fields = ["invoice", "status"]

    def perform_create(self, serializer):
        adjustment = serializer.save(recorded_by=self.request.user)
        audit("finance.offline_adjustment_recorded", actor=self.request.user,
              entity=adjustment, reason=adjustment.reason)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        adjustment = approve_offline_adjustment(
            request.user, self.get_object(), approve=True,
            reason=request.data.get("reason", ""))
        return Response(OfflinePaymentAdjustmentSerializer(adjustment).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        adjustment = approve_offline_adjustment(
            request.user, self.get_object(), approve=False,
            reason=request.data.get("reason", ""))
        return Response(OfflinePaymentAdjustmentSerializer(adjustment).data)


class ReceiptViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ReceiptSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Receipt.objects.select_related("invoice").all()
        user = self.request.user
        if has_role(user, "admin", "finance", "registrar"):
            return qs
        return qs.filter(invoice__student__user=user)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        from .receipts import build_receipt_pdf

        receipt = self.get_object()
        return build_receipt_pdf(receipt)


class ReconciliationView(APIView):
    """GET /api/admin/payments/reconciliation – pending/review queue."""

    permission_classes = [IsFinance]

    def get(self, request):
        qs = PaymentAttempt.objects.filter(
            status__in=["created", "pending", "needs_review"]
        ).order_by("created_at")
        cutoff = timezone.now()
        stale = [a for a in qs if (cutoff - a.created_at).total_seconds() > 900]
        return Response({
            "pending": PaymentAttemptSerializer(
                [a for a in qs if a.status in ("created", "pending")], many=True).data,
            "needs_review": PaymentAttemptSerializer(
                [a for a in qs if a.status == "needs_review"], many=True).data,
            "stale_pending_over_15min": [a.reference for a in stale],
        })


class StudentMeView(APIView):
    """GET /api/student/me – own profile + dashboard summary (blueprint SS8)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from academics.services import current_semester, registration_eligibility
        from accounts.serializers import StudentProfileSerializer

        student = _get_student(request)
        semester = current_semester()
        invoices = Invoice.objects.filter(student=student)
        return Response({
            "student": StudentProfileSerializer(student).data,
            "registration_fee": registration_fee_status(student),
            "semester": (
                {"id": semester.id, "label": str(semester),
                 "registration_deadline": semester.registration_deadline,
                 "fee_deadline": semester.fee_deadline,
                 "registration_open": semester.registration_open}
                if semester else None
            ),
            "total_due": str(sum((i.total_due for i in invoices), 0) or 0),
            "total_paid": str(sum((i.amount_paid for i in invoices), 0) or 0),
            "eligibility": (
                registration_eligibility(student, semester) if semester else None
            ),
        })
