"""Notification + announcement audience services."""
from django.db.models import Q
from django.utils import timezone

from .models import Announcement, Event, Notification


def notify(user, notification_type: str, title: str, body: str = "", link: str = ""):
    """Create an in-app notification (no-op without a linked user)."""
    if user is None:
        return None
    return Notification.objects.create(
        user=user, notification_type=notification_type, title=title, body=body,
        link=link)


def visible_announcements(user):
    """Announcements whose audience includes this user (SS5D/SS11.10)."""
    now = timezone.now()
    qs = Announcement.objects.filter(status="published").filter(
        Q(publish_at__isnull=True) | Q(publish_at__lte=now)
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))

    conditions = Q(audience_type="all")
    roles = getattr(user, "roles", set()) or set()
    if roles:
        conditions |= Q(audience_type="role", audience_role__in=list(roles))
    student = getattr(user, "student_profile", None)
    if student is not None:
        if student.program_id:
            conditions |= Q(audience_type="program", audience_program_id=student.program_id)
            if student.program.department_id:
                conditions |= Q(audience_type="unit",
                                audience_unit_id=student.program.department_id)
                parent_id = student.program.department.parent_id
                if parent_id:
                    conditions |= Q(audience_type="unit", audience_unit_id=parent_id)
        if student.current_level_id:
            conditions |= Q(audience_type="level",
                            audience_level_id=student.current_level_id)
    return qs.filter(conditions).distinct().order_by("-publish_at", "-created_at")


def visible_events(user):
    now = timezone.now()
    qs = Event.objects.filter(is_published=True, start_at__gte=now - timezone.timedelta(days=1))
    conditions = Q(audience_type="all")
    student = getattr(user, "student_profile", None)
    if student is not None:
        if student.program_id:
            conditions |= Q(audience_type="program", audience_program_id=student.program_id)
        if student.current_level_id:
            conditions |= Q(audience_type="level",
                            audience_level_id=student.current_level_id)
    return qs.filter(conditions).distinct().order_by("start_at")
