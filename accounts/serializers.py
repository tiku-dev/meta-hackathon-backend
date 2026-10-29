from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import LecturerProfile, StudentProfile, User, UserRole


class RolesTokenObtainPairSerializer(TokenObtainPairSerializer):
    """JWT login: students sign in with their *matricule*; staff keep email.

    The endpoint stays backward compatible — the login identifier may be
    sent as ``username``, ``matricule`` or ``email``. A matricule login is
    resolved to the student's stored email before authentication; all other
    identifiers authenticate as emails, exactly as before. ``email`` (the
    model's USERNAME_FIELD) is therefore optional at the serializer level:
    ``validate`` guarantees one identifier is present before authenticating.
    """

    email = serializers.CharField(write_only=True, required=False, allow_blank=True)
    matricule = serializers.CharField(write_only=True, required=False, allow_blank=True)
    username = serializers.CharField(write_only=True, required=False, allow_blank=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # TokenObtainSerializer.__init__ re-adds ``email`` as a required
        # field (it is the model's USERNAME_FIELD); a matricule-only login
        # must not be rejected before validate() can resolve it.
        self.fields[self.username_field].required = False
        self.fields[self.username_field].allow_blank = True

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["roles"] = sorted(user.roles)
        token["email"] = user.email
        token["username"] = user.username
        return token

    def validate(self, attrs):
        identifier = (
            attrs.pop("matricule", "")
            or attrs.pop("username", "")
            or attrs.get(self.username_field, "")
            or ""
        ).strip()
        if not identifier:
            raise serializers.ValidationError(
                {self.username_field: "Enter your matricule or email address."})
        if "@" not in identifier:
            # Matricule sign-in: resolve to the student's login email. A
            # case-insensitive match is enough; unknown matricules fall
            # through and fail authentication like any bad credential.
            student = (
                StudentProfile.objects.select_related("user")
                .filter(matricule__iexact=identifier, user__isnull=False)
                .first()
            )
            if student is not None:
                attrs[self.username_field] = student.user.email
        # SimpleJWT's validate() indexes attrs[username_field] directly, so
        # an unresolved identifier must be present (and simply fail auth).
        attrs.setdefault(self.username_field, identifier)
        return super().validate(attrs)


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "username", "first_name", "last_name",
                  "phone", "account_status", "roles", "is_staff", "date_joined"]
        read_only_fields = ["id", "date_joined"]

    def get_roles(self, obj):
        return sorted(obj.roles)


class UserRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserRole
        fields = ["id", "user", "role", "unit_scope", "is_active",
                  "created_at", "updated_at"]


class StudentProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.ReadOnlyField()
    program_name = serializers.CharField(source="program.name", read_only=True, default=None)
    level_label = serializers.CharField(source="current_level.label", read_only=True, default=None)

    class Meta:
        model = StudentProfile
        fields = [
            "id", "user", "matricule", "first_name", "last_name", "full_name",
            "email", "phone", "date_of_birth", "gender", "address",
            "program", "program_name", "current_level", "level_label",
            "admission_year", "student_category", "status",
            "matricule_issued_at", "matricule_revoked", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "user", "matricule", "matricule_issued_at",
                            "matricule_revoked", "created_at", "updated_at"]


class StudentProspectSerializer(serializers.ModelSerializer):
    """Admin creates a prospect; matricule/activation are issued separately."""

    class Meta:
        model = StudentProfile
        fields = [
            "first_name", "last_name", "email", "phone", "date_of_birth",
            "gender", "address", "program", "current_level", "admission_year",
            "student_category",
        ]


class LecturerProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.ReadOnlyField()
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)

    class Meta:
        model = LecturerProfile
        fields = [
            "id", "user", "lecturer_code", "first_name", "last_name", "full_name",
            "email", "phone", "department", "department_name", "is_hod",
            "status", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ActivationSerializer(serializers.Serializer):
    """Students activate with exactly what admissions issued: matricule,
    email, one-time activation code, and a new password. Display names come
    from the admin-created student record, never from this form."""

    matricule = serializers.CharField()
    email = serializers.EmailField()
    # Accept the legacy `token` field name as an alias for old links/forms.
    activation_code = serializers.CharField(required=False, allow_blank=True)
    token = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        activation_code = (attrs.get("activation_code") or attrs.get("token") or "").strip()
        if not activation_code:
            raise serializers.ValidationError(
                {"activation_code": "Enter the activation code issued by admissions."})
        attrs["activation_code"] = activation_code
        return attrs
