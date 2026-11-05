from django.urls import include, path
from core.routers import OptionalSlashRouter as DefaultRouter

from .views import (
    FeeScheduleViewSet, InvoicePaymentCreateView, InvoiceViewSet,
    MockSimulateView, OfflineAdjustmentViewSet, PaymentCancelView, PaymentStatusView,
    ReceiptViewSet, ReconciliationView, StudentFeesView, StudentMeView,
    notchpay_webhook, stripe_webhook,
)

router = DefaultRouter(trailing_slash="/?")
router.register("admin/fee-schedules", FeeScheduleViewSet, basename="fee-schedules")
router.register("admin/invoices", InvoiceViewSet, basename="invoices")
router.register("admin/offline-adjustments", OfflineAdjustmentViewSet,
                basename="offline-adjustments")
router.register("student/receipts", ReceiptViewSet, basename="receipts")

urlpatterns = [
    path("student/me", StudentMeView.as_view(), name="student-me"),
    path("student/fees", StudentFeesView.as_view(), name="student-fees"),
    path("invoices/<int:invoice_id>/payments", InvoicePaymentCreateView.as_view(),
         name="invoice-payments"),
    path("payments/<str:reference>/cancel", PaymentCancelView.as_view(),
         name="payment-cancel"),
    path("payments/<str:reference>/status", PaymentStatusView.as_view(),
         name="payment-status"),
    path("payments/<str:reference>/mock-simulate", MockSimulateView.as_view(),
         name="payment-mock-simulate"),
    path("webhooks/notchpay", notchpay_webhook, name="webhook-notchpay"),
    path("webhooks/stripe", stripe_webhook, name="webhook-stripe"),
    path("admin/payments/reconciliation", ReconciliationView.as_view(),
         name="payment-reconciliation"),
    path("", include(router.urls)),
]
