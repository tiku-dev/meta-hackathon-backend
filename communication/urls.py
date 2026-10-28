from django.urls import include, path
from core.routers import OptionalSlashRouter as DefaultRouter

from .views import (
    AnnouncementViewSet, DeadlineNotificationsView, EventViewSet,
    NotificationViewSet,
)

router = DefaultRouter(trailing_slash="/?")
router.register("admin/announcements", AnnouncementViewSet, basename="announcements")
router.register("announcements", AnnouncementViewSet, basename="announcements-public")
router.register("notifications", NotificationViewSet, basename="notifications")
router.register("events", EventViewSet, basename="events")

urlpatterns = [
    path("student/deadlines", DeadlineNotificationsView.as_view(), name="student-deadlines"),
    path("", include(router.urls)),
]
