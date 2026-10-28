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
