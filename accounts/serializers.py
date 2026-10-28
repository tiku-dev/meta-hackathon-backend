from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import LecturerProfile, StudentProfile, User, UserRole


class RolesTokenObtainPairSerializer(TokenObtainPairSerializer):
    """JWT that also carries the user's roles for the frontend."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token["roles"] = sorted(user.roles)
        token["email"] = user.email
        return token


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
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
