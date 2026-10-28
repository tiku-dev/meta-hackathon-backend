"""Grading policies, assessment results, transcript requests."""
from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class GradingPolicy(TimeStampedModel):
    """Configurable grading rules (blueprint SS4). CA 30 / exam 70 default."""

    name = models.CharField(max_length=160)
    academic_unit = models.ForeignKey(
        "academics.AcademicUnit", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="grading_policies")
    program = models.ForeignKey(
        "academics.Program", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="grading_policies")
    academic_year = models.ForeignKey(
        "academics.AcademicYear", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="grading_policies")
    ca_max = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("30"))
    exam_max = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("70"))
    pass_mark = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("50"))
    rounding_places = models.PositiveIntegerField(default=1)
    gpa_scale = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("4.0"))
    gpa_weighted_by_credits = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Grading policies"

    def __str__(self):
        return self.name

    @property
    def total_max(self) -> Decimal:
        return self.ca_max + self.exam_max

    def band_for(self, total: Decimal):
        return (
            self.bands.filter(min_score__lte=total, max_score__gte=total)
            .order_by("-min_score")
            .first()
        )


class GradeBand(TimeStampedModel):
    policy = models.ForeignKey(GradingPolicy, on_delete=models.CASCADE,
                               related_name="bands")
    min_score = models.DecimalField(max_digits=6, decimal_places=2)
    max_score = models.DecimalField(max_digits=6, decimal_places=2)
    letter = models.CharField(max_length=8)
    points = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0"))
    is_pass = models.BooleanField(default=False)
    label = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["policy", "-min_score"]
        unique_together = [("policy", "letter")]

    def __str__(self):
        return f"{self.letter} [{self.min_score}-{self.max_score}]"


class AssessmentResult(TimeStampedModel):
    STATUS_CHOICES = [
        ("draft", "Draft"), ("submitted", "Submitted"),
        ("hod_approved", "HOD approved"), ("published", "Published"),
        ("locked", "Locked"),
    ]

    registration = models.OneToOneField(
        "academics.CourseRegistration", on_delete=models.CASCADE,
        related_name="result")
    ca_mark = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    final_mark = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    total = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    grade_band = models.ForeignKey(GradeBand, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="results")
    grade_points = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    is_pass = models.BooleanField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="draft",
                              db_index=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="entered_results")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="approved_results")
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="published_results")
    published_at = models.DateTimeField(null=True, blank=True)
    change_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["registration"]

    def __str__(self):
        return f"Result {self.registration} total={self.total} ({self.status})"


class ResultCorrection(TimeStampedModel):
    """Authorized post-publication correction, always with a reason."""

    result = models.ForeignKey(AssessmentResult, on_delete=models.CASCADE,
                               related_name="corrections")
    old_ca = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    old_final = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    old_total = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    new_ca = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    new_final = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    new_total = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    reason = models.TextField()
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="result_corrections")

    class Meta:
        ordering = ["-created_at"]


class TranscriptRequest(TimeStampedModel):
    STATUS_CHOICES = [
        ("requested", "Requested"), ("approved", "Approved"),
        ("issued", "Issued"), ("rejected", "Rejected"),
    ]

    student = models.ForeignKey(
        "accounts.StudentProfile", on_delete=models.CASCADE,
        related_name="transcript_requests")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="transcript_requests")
    is_official = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="requested")
    issue_number = models.CharField(max_length=40, blank=True, unique=True, null=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="issued_transcripts")
    issued_at = models.DateTimeField(null=True, blank=True)
    document_path = models.CharField(max_length=255, blank=True)
    policy_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
