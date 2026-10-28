"""Shared building blocks: timestamped base model + append-only audit log."""
from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class AuditLog(models.Model):
    """Append-only record of sensitive actions (blueprint SS10).

    Rows must never be edited or deleted through the API/admin by normal
    roles; the model enforces this by refusing updates/deletes for
    non-superusers at the ORM level in the admin and API layers. The
    database itself should additionally revoke UPDATE/DELETE in production.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    actor_ip = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
    action = models.CharField(max_length=120, db_index=True)
    entity_type = models.CharField(max_length=120, blank=True, db_index=True)
    entity_id = models.CharField(max_length=64, blank=True, db_index=True)
    reason = models.TextField(blank=True)
    summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.entity_type}#{self.entity_id}"
