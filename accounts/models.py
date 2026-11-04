"""Identity: users, roles, student/lecturer profiles, activation tokens."""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

from core.models import TimeStampedModel

ROLE_CHOICES = [
    ("super_admin", "Super admin"),
    ("admin", "Admin"),
    ("admissions", "Admissions staff"),
    ("finance", "Finance officer"),
    ("registrar", "Registrar / exams officer"),
    ("hod", "Head of Department"),
    ("coordinator", "Department/program coordinator"),
    ("lecturer", "Lecturer"),
    ("student", "Student"),
]


class User(AbstractUser):
    """Login account. ``USERNAME_FIELD`` stays ``email`` for staff accounts;
    student accounts sign in with their matricule (their ``username`` is set
    to the matricule at activation, and the token endpoint resolves a
    matricule login to the student's email)."""

    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True)
    account_status = models.CharField(
        max_length=16,
        choices=[("active", "Active"), ("suspended", "Suspended"), ("inactive", "Inactive")],
        default="active",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        ordering = ["email"]

    def __str__(self):
        return self.email

    @property
    def roles(self) -> set[str]:
        """Active role names for this user (cached per instance)."""
        if not hasattr(self, "_roles_cache"):
            self._roles_cache = set(
                self.user_roles.filter(is_active=True).values_list("role", flat=True)
            )
        return self._roles_cache


class UserRole(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_roles")
    role = models.CharField(max_length=32, choices=ROLE_CHOICES)
    unit_scope = models.ForeignKey(
        "academics.AcademicUnit",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="scoped_roles",
        help_text="Optional academic-unit scope (e.g. the HOD's department).",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("user", "role", "unit_scope")]
        ordering = ["user", "role"]

    def __str__(self):
        return f"{self.user.email}: {self.role}"


class StudentProfile(TimeStampedModel):
    """A student record. Exists *before* account activation (blueprint SS5A);

    ``matricule`` is university-issued, unique and never reused.
    """

    STATUS_CHOICES = [
        ("pending", "Pending activation"),
        ("active", "Active"),
        ("suspended", "Suspended"),
        ("graduated", "Graduated"),
        ("withdrawn", "Withdrawn"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="student_profile",
    )
    matricule = models.CharField(max_length=32, unique=True, null=True, blank=True,
                                 db_index=True)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=32, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    GENDER_CHOICES = [
        ("male", "Male"),
        ("female", "Female"),
    ]

    gender = models.CharField(max_length=16, choices=GENDER_CHOICES, blank=True)
    address = models.CharField(max_length=255, blank=True)
    program = models.ForeignKey(
        "academics.Program", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="students",
    )
    current_level = models.ForeignKey(
        "academics.Level", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="students",
    )
    admission_year = models.ForeignKey(
        "academics.AcademicYear", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="admitted_students",
    )
    student_category = models.CharField(max_length=32, default="regular")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    matricule_issued_at = models.DateTimeField(null=True, blank=True)
    matricule_revoked = models.BooleanField(default=False)

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.matricule or 'no-matricule'} {self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class LecturerProfile(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lecturer_profile",
    )
    lecturer_code = models.CharField(max_length=32, unique=True)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    department = models.ForeignKey(
        "academics.AcademicUnit", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="lecturers",
    )
    is_hod = models.BooleanField(default=False, help_text="Heads ``department``.")
    status = models.CharField(
        max_length=16,
        choices=[("active", "Active"), ("inactive", "Inactive")],
        default="active",
    )

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.lecturer_code} {self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class ActivationToken(TimeStampedModel):
    """One-time activation credential. Only the SHA-256 hash is stored."""

    student = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="activation_tokens"
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="issued_activation_tokens",
    )

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_usable(self) -> bool:
        from django.utils import timezone

        return (
            self.used_at is None
            and self.revoked_at is None
            and self.expires_at > timezone.now()
        )
