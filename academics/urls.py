from django.urls import include, path
from core.routers import OptionalSlashRouter as DefaultRouter

from .views import (
    AcademicUnitViewSet, AcademicYearViewSet, CourseOfferingViewSet,
    CourseViewSet, ExemptionViewSet, HodOverviewView, LecturerAttendanceView,
    LevelViewSet, ProgramCourseViewSet, ProgramViewSet, RegistrationViewSet,
    SemesterViewSet, TimetableSessionViewSet, TimetableView,
)

router = DefaultRouter(trailing_slash="/?")
router.register("admin/academic-units", AcademicUnitViewSet, basename="academic-units")
router.register("admin/programs", ProgramViewSet, basename="programs")
router.register("admin/levels", LevelViewSet, basename="levels")
router.register("admin/academic-years", AcademicYearViewSet, basename="academic-years")
router.register("admin/semesters", SemesterViewSet, basename="semesters")
router.register("admin/courses", CourseViewSet, basename="courses")
router.register("admin/program-courses", ProgramCourseViewSet, basename="program-courses")
router.register("admin/course-offerings", CourseOfferingViewSet, basename="course-offerings")
router.register("admin/timetable-sessions", TimetableSessionViewSet, basename="timetable-sessions")
router.register("admin/exemptions", ExemptionViewSet, basename="exemptions")
router.register("student/registrations", RegistrationViewSet, basename="student-registrations")
router.register("hod/course-offerings", CourseOfferingViewSet, basename="hod-course-offerings")

urlpatterns = [
    path("timetable", TimetableView.as_view(), name="timetable"),
    path("student/registration/available",
         RegistrationViewSet.as_view({"get": "available"}), name="registration-available"),
    path("student/registration/eligibility",
         RegistrationViewSet.as_view({"get": "eligibility"}), name="registration-eligibility"),
    path("hod/department/overview", HodOverviewView.as_view(), name="hod-overview"),
    path("lecturer/offerings/<int:offering_id>/attendance",
         LecturerAttendanceView.as_view(), name="lecturer-attendance"),
    path("", include(router.urls)),
]
