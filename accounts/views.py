import csv
import io

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.audit import audit
from core.exceptions import BusinessError
from core.permissions import IsAdmin, IsAdmissions, has_role

from .models import LecturerProfile, StudentProfile, User
from .serializers import (
    ActivationSerializer,
    LecturerProfileSerializer,
    StudentProfileSerializer,
    StudentProspectSerializer,
    UserSerializer,
)
from .services import activate_account, issue_matricule, revoke_student


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
        if self.action in ("create", "issue_matricule", "revoke", "import_csv", "destroy"):
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
        if profile.is_hod:
            UserRole.objects.get_or_create(
                user=user, role="hod",
                defaults={"unit_scope": profile.department},
            )
        audit("lecturer.user_assigned", actor=request.user, entity=profile,
              summary={"user_id": user.id})
        return Response(LecturerProfileSerializer(profile).data)


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
