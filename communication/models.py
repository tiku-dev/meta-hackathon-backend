"""Announcements, in-app notifications, events + RSVP (blueprint SS5D)."""
from django.conf import settings
from django.db import models

from core.models import TimeStampedModel

AUDIENCE_TYPES = [
    ("all", "All university"), ("unit", "Academic unit"),
    ("program", "Program"), ("level", "Level"), ("role", "Role"),
]


class Announcement(TimeStampedModel):
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="announcements")
    title = models.CharField(max_length=200)
    body = models.TextField()
    audience_type = models.CharField(max_length=12, choices=AUDIENCE_TYPES, default="all")
    audience_unit = models.ForeignKey(
        "academics.AcademicUnit", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="announcements")
    audience_program = models.ForeignKey(
        "academics.Program", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="announcements")
    audience_level = models.ForeignKey(
        "academics.Level", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="announcements")
    audience_role = models.CharField(max_length=32, blank=True)
    status = models.CharField(
        max_length=12,
        choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")],
        default="draft", db_index=True)
    publish_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-publish_at", "-created_at"]

    def __str__(self):
        return self.title


class Notification(TimeStampedModel):
    TYPES = [
        ("payment", "Payment"), ("registration", "Registration"),
        ("result", "Result"), ("deadline", "Deadline"),
        ("announcement", "Announcement"), ("system", "System"),
        ("emergency", "Emergency contact"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="notifications")
    notification_type = models.CharField(max_length=20, choices=TYPES, default="system",
                                         db_index=True)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=255, blank=True)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} {self.title}"


class EmergencyContact(TimeStampedModel):
    """Urgent student → administration contact (student safety/help desk).

    Separate from generic notifications so cases can be triaged by
    priority, assigned, replied to and marked resolved without losing the
    audit trail. ``student_read_at``/``admin_read_at`` are per-side read
    markers; ``first_response_at`` records how quickly the first admin
    reply was sent.
    """

    CATEGORY_CHOICES = [
        ("medical", "Medical emergency"),
        ("security", "Security / safety"),
        ("welfare", "Personal welfare / distress"),
        ("financial", "Financial hardship"),
        ("technical", "Account / portal access"),
        ("other", "Other urgent matter"),
    ]
    PRIORITY_CHOICES = [
        ("urgent", "Urgent"), ("high", "High"), ("normal", "Normal"),
    ]
    STATUS_CHOICES = [
        ("open", "Open"), ("in_progress", "In progress"),
        ("resolved", "Resolved"), ("closed", "Closed"),
    ]

    student = models.ForeignKey(
        "accounts.StudentProfile", on_delete=models.CASCADE,
        related_name="emergency_contacts")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES,
                                default="other", db_index=True)
    priority = models.CharField(max_length=12, choices=PRIORITY_CHOICES,
                                default="normal", db_index=True)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    # Snapshot the student's contacts at send time so admins can reach
    # them even if the profile changes afterwards.
    contact_phone = models.CharField(max_length=32, blank=True)
    contact_email = models.EmailField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES,
                              default="open", db_index=True)
    admin_reply = models.TextField(blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_emergency_contacts")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="resolved_emergency_contacts")
    resolved_at = models.DateTimeField(null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    student_read_at = models.DateTimeField(null=True, blank=True)
    admin_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["priority", "status", "created_at"]),
            models.Index(fields=["student", "created_at"]),
        ]

    def __str__(self):
        return f"EmergencyContact #{self.pk} {self.subject} ({self.status})"

    @property
    def student_name(self) -> str:
        return self.student.full_name if self.student_id else ""

    @property
    def student_matricule(self) -> str:
        return self.student.matricule or "" if self.student_id else ""


class Event(TimeStampedModel):
    title = models.CharField(max_length=200)
    details = models.TextField(blank=True)
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=255, blank=True)
    meeting_link = models.URLField(blank=True)
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="organized_events")
    audience_type = models.CharField(max_length=12, choices=AUDIENCE_TYPES, default="all")
    audience_program = models.ForeignKey(
        "academics.Program", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="events")
    audience_level = models.ForeignKey(
        "academics.Level", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="events")
    rsvp_enabled = models.BooleanField(default=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["start_at"]

    def __str__(self):
        return self.title


class EventRSVP(TimeStampedModel):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="rsvps")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="event_rsvps")
    status = models.CharField(
        max_length=12,
        choices=[("going", "Going"), ("maybe", "Maybe"), ("declined", "Declined")],
        default="going")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("event", "user")]
        ordering = ["event", "user"]
