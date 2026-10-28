from django.urls import include, path
from core.routers import OptionalSlashRouter as DefaultRouter

from .views import AdminOverviewView, AuditLogViewSet, ReportView

router = DefaultRouter(trailing_slash="/?")
router.register("admin/audit-logs", AuditLogViewSet, basename="audit-logs")

urlpatterns = [
    path("admin/overview", AdminOverviewView.as_view(), name="admin-overview"),
    path("admin/reports/<str:report>", ReportView.as_view(), name="admin-report"),
    path("", include(router.urls)),
]
