from django.urls import include, path, re_path
from core.routers import OptionalSlashRouter as DefaultRouter

from .views import (
    AnnouncementViewSet, DeadlineNotificationsView,
    EmergencyContactViewSet, EmergencyContactsDirectoryView,
    EmergencyContactsSummaryView, EventViewSet, NotificationViewSet,
    StudentEmergencyContactReadView, StudentEmergencyContactsView,
)

router = DefaultRouter(trailing_slash="/?")
router.register("admin/announcements", AnnouncementViewSet, basename="announcements")
router.register("announcements", AnnouncementViewSet, basename="announcements-public")
router.register("notifications", NotificationViewSet, basename="notifications")
router.register("events", EventViewSet, basename="events")
router.register("admin/emergency-contacts", EmergencyContactViewSet,
                basename="admin-emergency-contacts")

urlpatterns = [
    path("student/deadlines", DeadlineNotificationsView.as_view(), name="student-deadlines"),
    # Plain path() routes are exact-match (no trailing slash). Accept the
    # frontend's trailing-slash style too — otherwise these 404. The
    # summary path must precede router.urls: otherwise it is swallowed
    # by the viewset detail route as pk="summary".
    re_path(r"^student/emergency-directory/?$",
            EmergencyContactsDirectoryView.as_view(),
            name="student-emergency-directory"),
    re_path(r"^student/emergency-contacts/?$",
            StudentEmergencyContactsView.as_view(),
            name="student-emergency-contacts"),
    re_path(r"^student/emergency-contacts/(?P<contact_id>\d+)/read/?$",
            StudentEmergencyContactReadView.as_view(),
            name="student-emergency-contact-read"),
    re_path(r"^admin/emergency-contacts/summary/?$",
            EmergencyContactsSummaryView.as_view(),
            name="admin-emergency-contacts-summary"),
    path("", include(router.urls)),
]
