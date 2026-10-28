from django.urls import include, path
from core.routers import OptionalSlashRouter as DefaultRouter

from .views import (
    LecturerAdminViewSet,
    MeViewSet,
    StudentAdminViewSet,
    activate_view,
)

router = DefaultRouter(trailing_slash="/?")
router.register("admin/students", StudentAdminViewSet, basename="admin-students")
router.register("admin/lecturers", LecturerAdminViewSet, basename="admin-lecturers")
router.register("me", MeViewSet, basename="me")

urlpatterns = [
    path("auth/activate", activate_view, name="auth-activate"),
    path("", include(router.urls)),
]
