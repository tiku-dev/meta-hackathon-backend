import csv
import io

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from academics.models import AcademicUnit
from core.audit import audit
from core.exceptions import BusinessError
from core.permissions import IsAdmin, IsAdmissions, has_role

from .hod_admin import (
    assign_hod, create_hod, department_hod, profile_summary, remove_hod,
    role_is_active, sync_hod_after_lecturer_save,
)
from .models import LecturerProfile, StudentProfile, User
from .serializers import (
    ActivationSerializer,
    LecturerProfileSerializer,
    StudentProfileSerializer,
    StudentProspectSerializer,
    UserSerializer,
)
from django.conf import settings
from django.utils import timezone
from .services import _hash, activate_account, issue_matricule, revoke_student, send_activation_email


@api_view(["POST"])
@permission_classes([AllowAny])
def activate_view(request):
    """POST /api/auth/activate – activate with matricule + email + code."""
    serializer = ActivationSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    student = activate_account(
        data["activation_code"],
        data["password"],
        matricule=data["matricule"],
        email=data["email"],
    )
    return Response(
        {
            "detail": "Account activated. Sign in with your matricule and password.",
            "matricule": student.matricule,
            "username": student.matricule,
            "name": student.full_name,
            "email": student.email,
            "student_id": student.id,
        },
        status=status.HTTP_201_CREATED,
    )


class StudentAdminViewSet(viewsets.ModelViewSet):
    """Admin/admissions management of student records."""

    serializer_class = StudentProfileSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status", "program", "current_level", "admission_year"]
    search_fields = ["matricule", "first_name", "last_name", "email"]
    ordering_fields = ["last_name", "created_at"]

    def get_queryset(self):
        qs = StudentProfile.objects.select_related(
            "program", "current_level", "user"
        ).all()
        user = self.request.user
        if has_role(user, "admin", "admissions", "finance", "registrar"):
            return qs
        if has_role(user, "hod"):
            department_id = getattr(
                getattr(user, "lecturer_profile", None), "department_id", None
            )
            return qs.filter(program__department_id=department_id)
        return qs.filter(user=user)

    def get_permissions(self):
        if self.action in ("create", "issue_matricule", "send_activation_email", "revoke", "import_csv", "destroy"):
            return [IsAdmissions()]
        return super().get_permissions()

    def create(self, request, *args, **kwargs):
        """POST /api/admin/students – create prospect + issue matricule."""
        serializer = StudentProspectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = serializer.save(status="pending")
        result = issue_matricule(request.user, student)
        audit("student.created", actor=request.user, entity=student,
              summary={"email": student.email})
        return Response(
            {**StudentProfileSerializer(student).data,
             "activation_token": result["activation_token"],
             "activation_link": result["activation_link"],
             "activation_expires_at": result["expires_at"]},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="issue-matricule")
    def issue_matricule(self, request, pk=None):
        student = self.get_object()
        result = issue_matricule(request.user, student)
        return Response(
            {"matricule": result["matricule"],
             "activation_token": result["activation_token"],
             "activation_link": result["activation_link"],
             "expires_at": result["expires_at"]}
        )

    @action(detail=True, methods=["post"], url_path="send-activation-email")
    def send_activation_email(self, request, pk=None):
        student = self.get_object()
        if not student.email:
            raise BusinessError("This student does not have an email address.", code="EMAIL_REQUIRED")

        raw_token = (request.data.get("activation_token") or "").strip()
        link = (request.data.get("activation_link") or "").strip()

        if raw_token:
            token_obj = student.activation_tokens.filter(
                token_hash=_hash(raw_token),
                used_at__isnull=True,
                revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).first()
            if not token_obj:
                raise BusinessError("The provided activation token is invalid or expired.", code="ACTIVATION_TOKEN_INVALID")
            if not link:
                base = settings.FRONTEND_BASE_URL.rstrip("/")
                link = f"{base}/activate?matricule={student.matricule}&email={student.email}&token={raw_token}"
            try:
                send_activation_email(student, raw_token, link, fail_silently=False)
            except Exception as exc:
                raise BusinessError(f"Failed to send email to {student.email}: {exc}", code="EMAIL_SEND_FAILED")
            return Response({"success": True, "email": student.email, "reissued": False})
        else:
            result = issue_matricule(request.user, student, send_email=False)
            try:
                send_activation_email(student, result["activation_token"], result["activation_link"], fail_silently=False)
            except Exception as exc:
                raise BusinessError(f"Failed to send email to {student.email}: {exc}", code="EMAIL_SEND_FAILED")
            return Response({
                "success": True,
                "email": student.email,
                "reissued": True,
                "matricule": result["matricule"],
                "activation_token": result["activation_token"],
                "activation_link": result["activation_link"],
                "expires_at": result["expires_at"],
            })

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        student = self.get_object()
        revoke_student(request.user, student, reason=request.data.get("reason", ""))
        return Response(StudentProfileSerializer(student).data)

    @action(detail=False, methods=["post"], url_path="import-csv")
    def import_csv(self, request):
        """CSV columns: first_name,last_name,email,phone,program_id,level_id."""
        upload = request.FILES.get("file")
        if upload is None:
            raise BusinessError("A CSV file is required (field name: file).",
                                code="CSV_REQUIRED")
        text = upload.read().decode("utf-8-sig")
        created, errors = [], []
        for i, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
            try:
                serializer = StudentProspectSerializer(data={
                    "first_name": row.get("first_name", ""),
                    "last_name": row.get("last_name", ""),
                    "email": row.get("email", ""),
                    "phone": row.get("phone", ""),
                    "program": int(row["program_id"]) if row.get("program_id") else None,
                    "current_level": int(row["level_id"]) if row.get("level_id") else None,
                })
                serializer.is_valid(raise_exception=True)
                student = serializer.save(status="pending")
                result = issue_matricule(request.user, student, send_email=False)
                created.append({"id": student.id, "matricule": result["matricule"],
                                "activation_token": result["activation_token"]})
            except Exception as exc:  # noqa: BLE001 – collect per-row errors
                errors.append({"row": i, "error": str(exc)})
        audit("student.csv_import", actor=request.user,
              summary={"created": len(created), "errors": len(errors)})
        return Response({"created": created, "errors": errors})


class LecturerAdminViewSet(viewsets.ModelViewSet):
    queryset = LecturerProfile.objects.select_related("department", "user").all()
    serializer_class = LecturerProfileSerializer
    filterset_fields = ["department", "is_hod", "status"]
    search_fields = ["lecturer_code", "first_name", "last_name", "email"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAdmin()]

    def perform_create(self, serializer):
        profile = serializer.save()
        audit("lecturer.created", actor=self.request.user, entity=profile)
        sync_hod_after_lecturer_save(
            self.request.user, profile,
            was_hod=False, old_department_id=None)

    def perform_update(self, serializer):
        instance = serializer.instance
        was_hod = bool(instance.is_hod)
        old_department_id = instance.department_id
        profile = serializer.save()
        sync_hod_after_lecturer_save(
            self.request.user, profile,
            was_hod=was_hod, old_department_id=old_department_id)

    @action(detail=True, methods=["post"], url_path="assign-user")
    def assign_user(self, request, pk=None):
        """Link an existing login account + lecturer (and optional HOD) role."""
        from .models import UserRole

        profile = self.get_object()
        user = User.objects.filter(id=request.data.get("user_id")).first()
        if user is None:
            raise BusinessError("user_id does not match a user.", code="USER_NOT_FOUND")
        profile.user = user
        profile.save(update_fields=["user", "updated_at"])
        UserRole.objects.get_or_create(user=user, role="lecturer")
        if profile.is_hod and profile.department_id:
            UserRole.objects.update_or_create(
                user=user, role="hod", unit_scope_id=profile.department_id,
                defaults={"is_active": True},
            )
        audit("lecturer.user_assigned", actor=request.user, entity=profile,
              summary={"user_id": user.id})
        return Response(LecturerProfileSerializer(profile).data)

    @action(detail=True, methods=["post"], url_path="remove-hod")
    def remove_hod_action(self, request, pk=None):
        """POST /api/admin/lecturers/:id/remove-hod – remove a headship."""
        profile = self.get_object()
        report = remove_hod(
            request.user, lecturer=profile,
            reason=request.data.get("reason", ""))
        return Response(report)


def _get_department(department_id):
    department = AcademicUnit.objects.filter(
        id=department_id, unit_type="department").first()
    if department is None:
        raise BusinessError("Department not found.", code="DEPARTMENT_NOT_FOUND")
    return department


def _department_row(department):
    hod = department_hod(department.id)
    return {
        "department_id": department.id,
        "department": department.name,
        "parent_id": department.parent_id,
        "parent_name": department.parent.name if department.parent_id else None,
        "is_active": department.is_active,
        "hod": profile_summary(hod) if hod else None,
        "has_hod": hod is not None,
        "hod_role_active": role_is_active(hod.user, "hod") if hod else False,
    }


class HodDepartmentsView(APIView):
    """GET /api/admin/hods/departments – every department + its HOD.

    The Admin → Heads of Department page is department-first (like the
    Students page is student-first): each row shows the current head or
    "No HOD", with assign/change/remove actions.
    """

    permission_classes = [IsAdmin]

    def get(self, request):
        departments = AcademicUnit.objects.filter(
            unit_type="department").select_related("parent").order_by("name")
        rows = [_department_row(d) for d in departments]
        return Response({
            "departments": rows,
            "summary": {
                "departments": len(rows),
                "with_hod": sum(1 for r in rows if r["has_hod"]),
                "without_hod": sum(1 for r in rows if not r["has_hod"]),
            },
        })


class HodDepartmentDetailView(APIView):
    """POST/DELETE /api/admin/hods/departments/:id – assign / remove HOD."""

    permission_classes = [IsAdmin]

    def post(self, request, department_id):
        """Assign (or change) the department's HOD. Body: {lecturer_id}."""
        department = _get_department(department_id)
        lecturer = LecturerProfile.objects.filter(
            id=request.data.get("lecturer_id")).first()
        if lecturer is None:
            raise BusinessError("lecturer_id does not match a lecturer.",
                                code="LECTURER_NOT_FOUND")
        report = assign_hod(
            request.user, department=department, lecturer=lecturer,
            reason=request.data.get("reason", ""))
        return Response({**report, **_department_row(department)})

    def delete(self, request, department_id):
        """Remove the department's current HOD (headship only)."""
        department = _get_department(department_id)
        report = remove_hod(
            request.user, department=department,
            reason=request.data.get("reason", ""))
        return Response({**report, **_department_row(department)})


class HodCreateView(APIView):
    """POST /api/admin/hods – create a brand-new HOD ("Add new HOD").

    Body: ``{department_id, first_name, last_name, email?, phone?,
    lecturer_code?, reason?}``. Creates the lecturer profile and assigns
    them as that department's single head in one step (replacing any
    previous head); the login account is linked afterwards from the page.
    """

    permission_classes = [IsAdmin]

    def post(self, request):
        department_id = request.data.get("department_id")
        if not department_id:
            raise BusinessError(
                "Choose the department the new HOD will head.",
                code="DEPARTMENT_REQUIRED")
        department = _get_department(department_id)
        report = create_hod(
            request.user, department=department, data=request.data,
            reason=request.data.get("reason", ""))
        return Response(
            {**report, **_department_row(department)},
            status=status.HTTP_201_CREATED)


class HodListView(APIView):
    """GET /api/admin/hods – current heads only (HODs view of the page)."""

    permission_classes = [IsAdmin]

    def get(self, request):
        heads = (
            LecturerProfile.objects.filter(is_hod=True)
            .select_related("department", "user")
            .order_by("department__name", "last_name"))
        rows = [{
            **profile_summary(p),
            "department_id": p.department_id,
            "department": p.department.name if p.department_id else None,
            "hod_role_active": role_is_active(p.user, "hod"),
        } for p in heads]
        return Response({"hods": rows, "count": len(rows)})


class MeViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        """GET /api/me – profile + roles for the signed-in user."""
        user = request.user
        data = {"user": UserSerializer(user).data, "roles": sorted(user.roles),
                "student": None, "lecturer": None}
        student = getattr(user, "student_profile", None)
        if student is not None:
            data["student"] = StudentProfileSerializer(student).data
        lecturer = getattr(user, "lecturer_profile", None)
        if lecturer is not None:
            data["lecturer"] = LecturerProfileSerializer(lecturer).data
        return Response(data)
