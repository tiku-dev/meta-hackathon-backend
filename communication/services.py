"""Notification + announcement audience services."""
from django.conf import settings
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


def emergency_contact_directory() -> dict:
    """Published emergency contacts for the student contact page.

    Values come from server settings (``EMERGENCY_CONTACT_*`` env vars) so
    the university can publish real numbers without a code change. Only
    entries with a dialable phone are listed — the page never shows
    placeholder or invented numbers.
    """
    entries = []
    contacts = getattr(settings, "EMERGENCY_CONTACTS", {}) or {}
    for label, phone_key, name_key in (
        ("Campus security", "security_phone", "security_name"),
        ("Campus medical / clinic", "medical_phone", "medical_name"),
        ("Student welfare / counselling", "welfare_phone", "welfare_name"),
        ("Administration office", "admin_phone", "admin_name"),
    ):
        phone = str(contacts.get(phone_key) or "").strip()
        name = str(contacts.get(name_key) or "").strip()
        if phone:
            entries.append({"label": label, "name": name, "phone": phone})
    email = str(getattr(settings, "EMERGENCY_CONTACT_EMAIL", "") or "").strip()
    return {
        "university_name": getattr(settings, "UNIVERSITY_NAME", "University"),
        "emergency_email": email,
        "contacts": entries,
        "escalation_note": (
            "For immediate danger, call campus security or local emergency "
            "services first, then send an emergency message here."),
    }


def notify_admins(notification_type, title, body="", link="",
                  *, roles=("admin", "super_admin")):
    """In-app notification for every active admin/super-admin account.

    Unread in-app alerts are the reliable channel (the notification bell
    polls every minute), so no request is ever left unseen in the inbox.
    """
    from accounts.models import User

    admins = User.objects.filter(
        is_active=True, user_roles__role__in=list(roles),
        user_roles__is_active=True).distinct()
    created = [notify(user, notification_type, title, body, link)
               for user in admins]
    return [c for c in created if c is not None]


def email_admins(subject, body):
    """Best-effort email copy of an urgent alert (never blocks the request).

    In-app admin notifications stay the source of truth; email is only an
    extra copy when SMTP is configured. Any failure is swallowed so the
    student's message is still saved and triaged in the admin inbox.
    """
    from accounts.models import User

    recipients = list(User.objects.filter(
        is_active=True, user_roles__role__in=["admin", "super_admin"],
        user_roles__is_active=True,
    ).exclude(email="").values_list("email", flat=True).distinct())
    extra = str(getattr(settings, "EMERGENCY_ALERT_EMAIL", "") or "").strip()
    if extra and extra not in recipients:
        recipients.append(extra)
    if not recipients:
        return 0
    try:
        from django.core.mail import send_mail

        return send_mail(subject, body, settings.DEFAULT_FROM_EMAIL,
                         recipients, fail_silently=True) or 0
    except Exception:  # email must never break an emergency report
        return 0


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
