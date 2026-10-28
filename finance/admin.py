from django.contrib import admin

from .models import (
    FeeSchedule, Invoice, OfflinePaymentAdjustment, PaymentAttempt,
    PaymentEvent, Receipt,
)


@admin.register(FeeSchedule)
class FeeScheduleAdmin(admin.ModelAdmin):
    list_display = ("fee_type", "amount", "currency", "program", "semester", "is_active")
    list_filter = ("fee_type", "currency", "is_active")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "total_due", "amount_paid", "status")
    list_filter = ("status", "currency")


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ("reference", "provider", "amount", "currency", "status")
    list_filter = ("provider", "status")
    readonly_fields = ("reference", "provider_reference", "status", "paid_at")

    def has_change_permission(self, request, obj=None):
        # Nobody may hand-edit a payment into 'succeeded' (blueprint SS9).
        return False


admin.site.register(PaymentEvent)
admin.site.register(Receipt)
admin.site.register(OfflinePaymentAdjustment)
