"""Academic structure, catalog, offerings, registration, timetable, attendance."""
from django.conf import settings
from django.db import models

from core.models import TimeStampedModel

SEMESTER_TYPES = [("first", "First"), ("second", "Second"), ("resit", "Resit")]

WEEKDAYS = [
    (0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"),
    (4, "Friday"), (5, "Saturday"), (6, "Sunday"),
]


class AcademicUnit(TimeStampedModel):
    """Faculty/school/institute/department – nestable (blueprint SS4)."""

    UNIT_TYPES = [
        ("university", "University"), ("faculty", "Faculty"),
        ("school", "School"), ("institute", "Institute"),
        ("department", "Department"),
    ]

    name = models.CharField(max_length=160)
    unit_type = models.CharField(max_length=16, choices=UNIT_TYPES, default="department")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    code = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        unique_together = [("name", "parent")]

    def __str__(self):
        return self.name


class Program(TimeStampedModel):
    department = models.ForeignKey(
        AcademicUnit, on_delete=models.CASCADE, related_name="programs"
    )
    name = models.CharField(max_length=200)
    award = models.CharField(max_length=80, blank=True, help_text="e.g. BSc, MSc, HND")
    duration_years = models.PositiveIntegerField(default=3)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Level(TimeStampedModel):
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="levels")
    label = models.CharField(max_length=40)
    order = models.PositiveIntegerField()
    default_max_credits = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Optional credit cap for this level (overrides program default).")

    class Meta:
        ordering = ["program", "order"]
        unique_together = [("program", "order")]

    def __str__(self):
        return f"{self.program.name} – {self.label}"


class AcademicYear(TimeStampedModel):
    label = models.CharField(max_length=20, unique=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=[("draft", "Draft"), ("active", "Active"), ("closed", "Closed")],
        default="draft",
    )

    class Meta:
        ordering = ["-start_date", "label"]

    def __str__(self):
        return self.label


class Semester(TimeStampedModel):
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="semesters"
    )
    semester_type = models.CharField(max_length=8, choices=SEMESTER_TYPES)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    registration_opens_at = models.DateTimeField(null=True, blank=True)
    registration_deadline = models.DateTimeField(null=True, blank=True, db_index=True)
    fee_deadline = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=[("draft", "Draft"), ("open", "Open"), ("closed", "Closed")],
        default="draft",
    )
    registration_open = models.BooleanField(default=False)

    class Meta:
        ordering = ["academic_year", "semester_type"]
        unique_together = [("academic_year", "semester_type")]

    def __str__(self):
        return f"{self.academic_year} {self.get_semester_type_display()}"


class Course(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True, db_index=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    credits = models.PositiveIntegerField(default=3)
    department = models.ForeignKey(
        AcademicUnit, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="courses",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.title}"


class ProgramCourse(TimeStampedModel):
    program = models.ForeignKey(Program, on_delete=models.CASCADE,
                                related_name="program_courses")
    level = models.ForeignKey(Level, on_delete=models.CASCADE,
                              related_name="program_courses")
    course = models.ForeignKey(Course, on_delete=models.CASCADE,
                               related_name="program_courses")
    semester_type = models.CharField(max_length=8, choices=SEMESTER_TYPES, default="first")
    is_required = models.BooleanField(default=True)
    prerequisite = models.ForeignKey(
        Course, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="prerequisite_for",
    )

    class Meta:
        unique_together = [("program", "level", "course", "semester_type")]
        ordering = ["program", "level", "course__code"]

    def __str__(self):
        return f"{self.program} {self.level} {self.course}"


class CourseOffering(TimeStampedModel):
    """A course actually running for a semester, with lecturer + capacity."""

    STATUS_CHOICES = [
        ("draft", "Draft"), ("published", "Published"), ("closed", "Closed"),
    ]

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="offerings")
    program = models.ForeignKey(Program, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="offerings")
    level = models.ForeignKey(Level, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="offerings")
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE,
                                 related_name="offerings")
    lecturer = models.ForeignKey(
        "accounts.LecturerProfile", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="offerings",
    )
    capacity = models.PositiveIntegerField(default=60)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="draft")

    class Meta:
        ordering = ["course__code"]
        unique_together = [("course", "semester", "program", "level")]

    def __str__(self):
        return f"{self.course} ({self.semester})"

    @property
    def enrolled_count(self) -> int:
        return self.registrations.filter(status="registered").count()


class CourseRegistration(TimeStampedModel):
    STATUS_CHOICES = [
        ("registered", "Registered"), ("dropped", "Dropped"),
        ("withdrawn", "Withdrawn"), ("completed", "Completed"),
    ]

    student = models.ForeignKey(
        "accounts.StudentProfile", on_delete=models.CASCADE, related_name="registrations"
    )
    offering = models.ForeignKey(
        CourseOffering, on_delete=models.CASCADE, related_name="registrations"
    )
    semester = models.ForeignKey(
        Semester, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="registrations",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="registered")
    registered_at = models.DateTimeField(auto_now_add=True)
    admin_exception = models.BooleanField(
        default=False, help_text="Registered by an admin past the deadline."
    )

    class Meta:
        ordering = ["-registered_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "offering"],
                condition=models.Q(status="registered"),
                name="uniq_active_registration",
            )
        ]

    def __str__(self):
        return f"{self.student} -> {self.offering} ({self.status})"


class RegistrationExemption(TimeStampedModel):
    """Recorded admin exception (late registration / fee waiver)."""

    student = models.ForeignKey(
        "accounts.StudentProfile", on_delete=models.CASCADE, related_name="exemptions"
    )
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE,
                                 related_name="exemptions")
    exemption_type = models.CharField(
        max_length=24,
        choices=[("late_registration", "Late registration"),
                 ("fee_waiver", "Fee waiver"),
                 ("credit_override", "Credit override")],
    )
    reason = models.TextField()
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="granted_exemptions",
    )
    extra_credits = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]


class TimetableSession(TimeStampedModel):
    offering = models.ForeignKey(
        CourseOffering, on_delete=models.CASCADE, related_name="timetable_sessions"
    )
    weekday = models.IntegerField(choices=WEEKDAYS, null=True, blank=True)
    session_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.CharField(max_length=80, blank=True)
    campus = models.CharField(max_length=80, blank=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["weekday", "start_time"]

    def __str__(self):
        return f"{self.offering} {self.get_weekday_display()} {self.start_time}-{self.end_time}"


class AttendanceRecord(TimeStampedModel):
    STATUS_CHOICES = [
        ("present", "Present"), ("absent", "Absent"),
        ("late", "Late"), ("excused", "Excused"),
    ]

    session = models.ForeignKey(
        TimetableSession, on_delete=models.CASCADE, related_name="attendance_records"
    )
    student = models.ForeignKey(
        "accounts.StudentProfile", on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="present")
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="marked_attendance",
    )
    marked_at = models.DateTimeField(auto_now=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("session", "student")]
        ordering = ["session", "student"]
