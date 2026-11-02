from django.urls import include, path
from core.routers import OptionalSlashRouter as DefaultRouter

from .views import (
    HodCreateView,
    HodDepartmentDetailView,
    HodDepartmentsView,
    HodListView,
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
    path("admin/hods/departments", HodDepartmentsView.as_view(),
         name="admin-hod-departments"),
    path("admin/hods/departments/<int:department_id>",
         HodDepartmentDetailView.as_view(), name="admin-hod-department"),
    path("admin/hods", HodListView.as_view(), name="admin-hods"),
    path("admin/hods/create", HodCreateView.as_view(), name="admin-hod-create"),
    path("", include(router.urls)),
]
