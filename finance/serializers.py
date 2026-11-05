from rest_framework import serializers

from .models import (
    FeeSchedule, Invoice, OfflinePaymentAdjustment, PaymentAttempt,
    PaymentEvent, Receipt, RegistrationFeeRecord,
)


class FeeScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeSchedule
        fields = ["id", "academic_unit", "program", "level", "academic_year",
                  "semester", "fee_type", "student_category", "currency", "amount",
                  "registration_threshold_type", "registration_threshold_value",
                  "effective_from", "effective_to", "is_active",
                  "created_at", "updated_at"]


class PaymentAttemptSerializer(serializers.ModelSerializer):
    invoice_id = serializers.IntegerField(source="invoice.id", read_only=True)

    class Meta:
        model = PaymentAttempt
        fields = ["id", "invoice", "invoice_id", "provider", "reference",
                  "provider_reference", "amount", "currency", "status", "channel",
                  "checkout_url", "failure_reason", "paid_at",
                  "created_at", "updated_at"]


class InvoiceSerializer(serializers.ModelSerializer):
    balance = serializers.ReadOnlyField()
    student_matricule = serializers.CharField(source="student.matricule", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    required_for_registration = serializers.SerializerMethodField()
    pending_attempt = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = ["id", "student", "student_matricule", "student_name",
                  "fee_schedule", "academic_year", "semester", "currency",
                  "total_due", "amount_paid", "balance", "status", "due_date",
                  "alt_currency", "alt_amount", "fx_rate", "fx_quoted_at",
                  "notes", "required_for_registration", "pending_attempt",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "amount_paid", "status", "created_at", "updated_at"]

    def get_required_for_registration(self, obj):
        if obj.fee_schedule_id:
            return str(obj.fee_schedule.required_for_registration(obj.total_due))
        return str(obj.total_due)

    def get_pending_attempt(self, obj):
        attempt = obj.payment_attempts.filter(status="pending").order_by("-created_at").first()
        if attempt:
            return PaymentAttemptSerializer(attempt).data
        return None


class PaymentEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentEvent
        fields = ["id", "provider", "event_id", "event_type", "reference",
                  "processed", "outcome", "created_at"]


class RegistrationFeeRecordSerializer(serializers.ModelSerializer):
    student_matricule = serializers.CharField(source="student.matricule", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)

    class Meta:
        model = RegistrationFeeRecord
        fields = ["id", "student", "student_matricule", "student_name",
                  "required", "is_new_student", "amount", "currency", "status",
                  "fee_schedule", "paid_invoice", "paid_at", "waived_by",
                  "waiver_reason", "created_at", "updated_at"]
        read_only_fields = fields


class ReceiptSerializer(serializers.ModelSerializer):
    invoice_id = serializers.IntegerField(source="invoice.id", read_only=True)

    class Meta:
        model = Receipt
        fields = ["id", "invoice", "invoice_id", "payment_attempt", "receipt_number",
                  "amount", "currency", "issued_at", "file_path", "created_at"]


class OfflinePaymentAdjustmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = OfflinePaymentAdjustment
        fields = ["id", "invoice", "amount", "currency", "method", "evidence",
                  "reason", "status", "recorded_by", "approved_by", "approved_at",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "status", "recorded_by", "approved_by",
                            "approved_at", "created_at", "updated_at"]
