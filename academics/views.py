from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.audit import audit
from core.exceptions import BusinessError
from core.permissions import IsAcademicsAdmin, IsAdmin, IsHOD, has_role

from .models import (
    AcademicUnit, AcademicYear, Course, CourseOffering, CourseRegistration,
    Level, Program, ProgramCourse, RegistrationExemption, Semester,
    TimetableSession,
)
from .serializers import (
    AcademicUnitSerializer, AcademicYearSerializer, CourseOfferingSerializer,
    CourseRegistrationSerializer, CourseSerializer, LevelSerializer,
    ProgramCourseSerializer, ProgramSerializer, RegistrationExemptionSerializer,
    SemesterSerializer, TimetableSessionSerializer,
)
from .services import (
    check_timetable_conflicts, current_semester, drop_registration,
    offering_unavailability, register_courses, registration_eligibility,
)


def _hod_department_id(user):
    profile = getattr(user, "lecturer_profile", None)
    return profile.department_id if profile and profile.is_hod else None


def _get_student(request):
    student = getattr(request.user, "student_profile", None)
    if student is None:
        raise BusinessError("Your account is not linked to a student profile.",
                            code="NOT_A_STUDENT")
    return student


class ReadOnlyOrAcademicsAdmin(viewsets.ModelViewSet):
    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAcademicsAdmin()]


class AcademicUnitViewSet(ReadOnlyOrAcademicsAdmin):
    queryset = AcademicUnit.objects.all()
    serializer_class = AcademicUnitSerializer
    filterset_fields = ["unit_type", "parent", "is_active"]
    search_fields = ["name", "code"]


class ProgramViewSet(ReadOnlyOrAcademicsAdmin):
    queryset = Program.objects.select_related("department").all()
    serializer_class = ProgramSerializer
    filterset_fields = ["department", "is_active"]
    search_fields = ["name"]


class LevelViewSet(ReadOnlyOrAcademicsAdmin):
    queryset = Level.objects.all()
    serializer_class = LevelSerializer
    filterset_fields = ["program"]


class AcademicYearViewSet(ReadOnlyOrAcademicsAdmin):
    queryset = AcademicYear.objects.all()
    serializer_class = AcademicYearSerializer
    filterset_fields = ["status"]


class SemesterViewSet(ReadOnlyOrAcademicsAdmin):
    queryset = Semester.objects.select_related("academic_year").all()
    serializer_class = SemesterSerializer
    filterset_fields = ["academic_year", "semester_type", "status"]

    def get_permissions(self):
        if self.action in ("open_registration", "close_registration"):
            return [IsAdmin()]
        return super().get_permissions()

    @action(detail=True, methods=["post"], url_path="open-registration")
    def open_registration(self, request, pk=None):
        semester = self.get_object()
        semester.status = "open"
        semester.registration_open = True
        semester.save(update_fields=["status", "registration_open", "updated_at"])
        audit("semester.registration_opened", actor=request.user, entity=semester)
        return Response(SemesterSerializer(semester).data)

    @action(detail=True, methods=["post"], url_path="close-registration")
    def close_registration(self, request, pk=None):
        semester = self.get_object()
        semester.status = "closed"
        semester.registration_open = False
        semester.save(update_fields=["status", "registration_open", "updated_at"])
        audit("semester.registration_closed", actor=request.user, entity=semester)
        return Response(SemesterSerializer(semester).data)


class CourseViewSet(ReadOnlyOrAcademicsAdmin):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    filterset_fields = ["department", "is_active"]
    search_fields = ["code", "title"]


class ProgramCourseViewSet(ReadOnlyOrAcademicsAdmin):
    queryset = ProgramCourse.objects.select_related("course").all()
    serializer_class = ProgramCourseSerializer
    filterset_fields = ["program", "level", "semester_type"]


class CourseOfferingViewSet(viewsets.ModelViewSet):
    serializer_class = CourseOfferingSerializer
    filterset_fields = ["course", "program", "level", "semester", "lecturer", "status"]
    search_fields = ["course__code", "course__title"]

    def get_queryset(self):
        qs = CourseOffering.objects.select_related(
            "course", "semester", "lecturer").all()
        user = self.request.user
        if has_role(user, "admin", "registrar"):
            return qs
        if has_role(user, "hod"):
            dept_id = _hod_department_id(user)
            return qs.filter(course__department_id=dept_id)
        if has_role(user, "lecturer") and not has_role(user, "hod"):
            profile = getattr(user, "lecturer_profile", None)
            # Lecturers see published offerings + their own (roster endpoints
            # restrict further).
            return qs.filter(status="published") | qs.filter(lecturer=profile)
        return qs.filter(status="published")

    def get_permissions(self):
        if self.action in ("list", "retrieve", "conflicts", "roster"):
            return [IsAuthenticated()]
        if self.action == "create":
            return [IsAcademicsAdmin() | IsHOD()]
        return [IsAcademicsAdmin()]

    def perform_create(self, serializer):
        user = self.request.user
        if has_role(user, "hod") and not has_role(user, "admin", "registrar"):
            dept_id = _hod_department_id(user)
            course = serializer.validated_data.get("course")
            if course and course.department_id != dept_id:
                raise BusinessError(
                    "HODs may only propose offerings for their own department.",
                    code="OUT_OF_DEPARTMENT_SCOPE")
            serializer.save(status="draft")
        else:
            serializer.save()
        audit("offering.created", actor=user, entity=serializer.instance)

    @action(detail=True, methods=["get"])
    def conflicts(self, request, pk=None):
        offering = self.get_object()
        return Response({"offering_id": offering.id,
                         "conflicts": check_timetable_conflicts(offering)})

    @action(detail=True, methods=["get"])
    def roster(self, request, pk=None):
        offering = self.get_object()
        user = request.user
        profile = getattr(user, "lecturer_profile", None)
        allowed = (
            has_role(user, "admin", "registrar")
            or (profile and offering.lecturer_id == profile.id)
            or (has_role(user, "hod")
                and offering.course.department_id == _hod_department_id(user))
        )
        if not allowed:
            raise BusinessError("You do not teach this offering.",
                                code="NOT_YOUR_OFFERING")
        regs = CourseRegistration.objects.filter(
            offering=offering, status="registered").select_related("student")
        return Response(CourseRegistrationSerializer(regs, many=True).data)


class TimetableSessionViewSet(viewsets.ModelViewSet):
    serializer_class = TimetableSessionSerializer
    filterset_fields = ["offering", "weekday", "is_published"]

    def get_queryset(self):
        qs = TimetableSession.objects.select_related(
            "offering__course", "offering").all()
        user = self.request.user
        if has_role(user, "admin", "registrar"):
            return qs
        if has_role(user, "student"):
            student = getattr(user, "student_profile", None)
            if student is None:
                return qs.none()
            # Own cohort's schedule plus anything the student actually
            # registered for (incl. cross-department electives).
            own = qs.filter(
                offering__program_id=student.program_id,
                offering__level_id=student.current_level_id,
            )
            registered = qs.filter(
                offering__registrations__student=student,
                offering__registrations__status="registered",
            )
            return (own | registered).distinct()
        if has_role(user, "lecturer", "hod"):
            profile = getattr(user, "lecturer_profile", None)
            own = qs.filter(offering__lecturer=profile) if profile else qs.none()
            return own | qs.filter(
                offering__program__department_id=_hod_department_id(user))
        return qs.filter(offering__program_id=getattr(
            getattr(user, "student_profile", None), "program_id", None))

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAcademicsAdmin() | IsHOD()]


class RegistrationViewSet(viewsets.ViewSet):
    """Student-facing registration endpoints (blueprint SS8)."""

    permission_classes = [IsAuthenticated]

    def list(self, request):
        """GET /api/student/registrations – my registrations."""
        student = _get_student(request)
        regs = CourseRegistration.objects.filter(student=student).select_related(
            "offering__course")
        semester_id = request.query_params.get("semester")
        if semester_id:
            regs = regs.filter(semester_id=semester_id)
        return Response(CourseRegistrationSerializer(regs, many=True).data)

    def create(self, request):
        """POST /api/student/registrations – atomic bulk registration."""
        student = _get_student(request)
        offering_ids = request.data.get("offering_ids") or []
        if not isinstance(offering_ids, list) or not offering_ids:
            raise BusinessError("offering_ids must be a non-empty list.",
                                code="NO_OFFERINGS_SELECTED")
        semester = current_semester()
        if request.data.get("semester"):
            semester = Semester.objects.filter(id=request.data["semester"]).first()
        created = register_courses(request.user, student,
                                   [int(o) for o in offering_ids], semester)
        return Response(
            CourseRegistrationSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED)

    def destroy(self, request, pk=None):
        """DELETE /api/student/registrations/:id – drop a course."""
        student = _get_student(request)
        registration = drop_registration(request.user, student, int(pk))
        return Response(CourseRegistrationSerializer(registration).data)

    @action(detail=False, methods=["get"])
    def available(self, request):
        """GET /api/student/registration/available – offerings + reasons."""
        student = _get_student(request)
        semester = current_semester()
        if semester is None:
            return Response({"eligible": False,
                             "reasons": [{"code": "NO_OPEN_SEMESTER",
                                          "message": "No semester is currently open."}],
                             "offerings": []})
        eligibility = registration_eligibility(student, semester)
        # Cross-department electives: list every published offering for the
        # semester, from every department — not just the student's own
        # program/level. `?department=<id>` narrows the list for browsing.
        offerings = (
            CourseOffering.objects.filter(semester=semester, status="published")
            .select_related("course", "course__department")
            .order_by("course__department__name", "course__code")
        )
        department = request.query_params.get("department")
        if department:
            offerings = offerings.filter(course__department_id=department)
        student_dept_id = (
            student.program.department_id if student.program_id else None
        )
        data = []
        for offering in offerings:
            problem = offering_unavailability(student, offering, semester)
            item = CourseOfferingSerializer(offering).data
            item["available"] = problem is None
            item["unavailable_reason"] = problem
            item["is_cross_department"] = bool(
                item["department_id"] and student_dept_id
                and item["department_id"] != student_dept_id
            )
            data.append(item)
        departments = sorted(
            {o["department_name"] for o in data if o["department_name"]}
        )
        return Response({**eligibility, "offerings": data,
                         "departments": departments})

    @action(detail=False, methods=["get"])
    def eligibility(self, request):
        """GET /api/student/registration/eligibility – checklist only."""
        student = _get_student(request)
        semester = current_semester()
        return Response(registration_eligibility(student, semester))


class ExemptionViewSet(viewsets.ModelViewSet):
    queryset = RegistrationExemption.objects.all()
    serializer_class = RegistrationExemptionSerializer
    permission_classes = [IsAdmin]
    filterset_fields = ["student", "semester", "exemption_type", "is_active"]

    def perform_create(self, serializer):
        exemption = serializer.save(granted_by=self.request.user)
        audit("registration.exemption_granted", actor=self.request.user,
              entity=exemption, reason=exemption.reason)


class HodOverviewView(APIView):
    """GET /api/hod/department/overview – own department only."""

    permission_classes = [IsHOD]

    def get(self, request):
        dept_id = _hod_department_id(request.user)
        if dept_id is None and not has_role(request.user, "admin"):
            raise BusinessError("No department is assigned to your HOD profile.",
                                code="NO_DEPARTMENT")
        from accounts.models import LecturerProfile, StudentProfile

        offerings = CourseOffering.objects.filter(course__department_id=dept_id)
        return Response({
            "department_id": dept_id,
            "programs": Program.objects.filter(department_id=dept_id).count(),
            "courses": Course.objects.filter(department_id=dept_id).count(),
            "offerings": offerings.count(),
            "lecturers": LecturerProfile.objects.filter(department_id=dept_id).count(),
            "students": StudentProfile.objects.filter(
                program__department_id=dept_id).count(),
            "registrations": CourseRegistration.objects.filter(
                offering__course__department_id=dept_id, status="registered").count(),
        })


class LecturerAttendanceView(APIView):
    """POST /api/lecturer/offerings/:id/attendance"""

    permission_classes = [IsAuthenticated]

    def post(self, request, offering_id):
        from .models import AttendanceRecord, TimetableSession

        offering = CourseOffering.objects.filter(id=offering_id).first()
        if offering is None:
            raise BusinessError("Offering not found.", code="OFFERING_NOT_FOUND")
        profile = getattr(request.user, "lecturer_profile", None)
        if not (has_role(request.user, "admin")
                or (profile and offering.lecturer_id == profile.id)):
            raise BusinessError("You do not teach this offering.",
                                code="NOT_YOUR_OFFERING")
        session_id = request.data.get("session_id")
        session = TimetableSession.objects.filter(
            id=session_id, offering=offering).first()
        if session is None:
            raise BusinessError("session_id is required and must belong to the offering.",
                                code="SESSION_NOT_FOUND")
        records = []
        for entry in request.data.get("records", []):
            record, _ = AttendanceRecord.objects.update_or_create(
                session=session, student_id=entry["student_id"],
                defaults={"status": entry.get("status", "present"),
                          "note": entry.get("note", ""), "marked_by": request.user})
            records.append(record)
        audit("attendance.saved", actor=request.user, entity=offering,
              summary={"session_id": session.id, "count": len(records)})
        from .serializers import AttendanceRecordSerializer

        return Response(AttendanceRecordSerializer(records, many=True).data)


class TimetableView(APIView):
    """GET /api/timetable?program=&level=&semester=&lecturer= – filtered view."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = TimetableSession.objects.select_related("offering__course").all()
        params = request.query_params
        if params.get("program"):
            qs = qs.filter(offering__program_id=params["program"])
        if params.get("level"):
            qs = qs.filter(offering__level_id=params["level"])
        if params.get("semester"):
            qs = qs.filter(offering__semester_id=params["semester"])
        if params.get("lecturer"):
            qs = qs.filter(offering__lecturer_id=params["lecturer"])
        if has_role(request.user, "student") and not (
            params.get("program") or params.get("level")
        ):
            student = getattr(request.user, "student_profile", None)
            if student is not None:
                # Own cohort's schedule plus anything the student actually
                # registered for (incl. cross-department electives).
                own = qs.filter(offering__program_id=student.program_id,
                                offering__level_id=student.current_level_id)
                registered = qs.filter(
                    offering__registrations__student=student,
                    offering__registrations__status="registered",
                )
                qs = (own | registered).distinct()
        return Response(TimetableSessionSerializer(qs, many=True).data)
