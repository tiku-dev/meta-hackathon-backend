from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.audit import audit
from core.exceptions import BusinessError
from core.permissions import IsAcademicsAdmin, IsAdmin, has_role

from .models import (Announcement, EmergencyContact, Event, EventRSVP,
                     Notification)
from .serializers import (
    AnnouncementSerializer, EmergencyContactAdminUpdateSerializer,
    EmergencyContactCreateSerializer, EmergencyContactSerializer,
    EventRSVPSerializer, EventSerializer, NotificationSerializer,
)
from .services import (
    email_admins, emergency_contact_directory, notify, notify_admins,
    visible_announcements, visible_events,
)


class AnnouncementViewSet(viewsets.ModelViewSet):
    serializer_class = AnnouncementSerializer
    filterset_fields = ["status", "audience_type"]

    def get_queryset(self):
        if has_role(self.request.user, "admin", "registrar"):
            return Announcement.objects.all()
        return visible_announcements(self.request.user)

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAcademicsAdmin()]

    def perform_create(self, serializer):
        announcement = serializer.save(author=self.request.user)
        if announcement.status == "published" and not announcement.publish_at:
            announcement.publish_at = timezone.now()
            announcement.save(update_fields=["publish_at", "updated_at"])
        audit("announcement.created", actor=self.request.user, entity=announcement)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        announcement = self.get_object()
        announcement.status = "published"
        announcement.publish_at = timezone.now()
        announcement.save(update_fields=["status", "publish_at", "updated_at"])
        audit("announcement.published", actor=request.user, entity=announcement)
        return Response(AnnouncementSerializer(announcement).data)


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/notifications (+ mark read). Strictly the caller's own."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ["notification_type", "read_at"]

    def get_queryset(self):
        qs = Notification.objects.filter(user=self.request.user)
        if self.request.query_params.get("unread") == "1":
            qs = qs.filter(read_at__isnull=True)
        return qs

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at", "updated_at"])
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        count = Notification.objects.filter(
            user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        return Response({"marked_read": count})

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"unread": Notification.objects.filter(
            user=request.user, read_at__isnull=True).count()})


class EventViewSet(viewsets.ModelViewSet):
    serializer_class = EventSerializer
    filterset_fields = ["audience_type", "is_published"]

    def get_queryset(self):
        if has_role(self.request.user, "admin", "registrar"):
            return Event.objects.all()
        return visible_events(self.request.user)

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "request": self.request}

    def get_permissions(self):
        if self.action in ("list", "retrieve", "rsvp"):
            return [IsAuthenticated()]
        return [IsAcademicsAdmin()]

    def perform_create(self, serializer):
        event = serializer.save(organizer=self.request.user)
        audit("event.created", actor=self.request.user, entity=event)

    @action(detail=True, methods=["post"])
    def rsvp(self, request, pk=None):
        event = self.get_object()
        if not event.rsvp_enabled:
            raise BusinessError("RSVP is disabled for this event.", code="RSVP_DISABLED")
        rsvp_status = request.data.get("status", "going")
        if rsvp_status not in ("going", "maybe", "declined"):
            raise BusinessError("Invalid RSVP status.", code="INVALID_RSVP")
        rsvp, _ = EventRSVP.objects.update_or_create(
            event=event, user=request.user,
            defaults={"status": rsvp_status, "note": request.data.get("note", "")})
        return Response(EventRSVPSerializer(rsvp).data,
                        status=status.HTTP_201_CREATED)


def _get_student(request):
    student = getattr(request.user, "student_profile", None)
    if student is None:
        raise BusinessError("Your account is not linked to a student profile.",
                            code="NOT_A_STUDENT")
    return student


def _priority_rank(priority):
    return {"urgent": 0, "high": 1, "normal": 2}.get(priority, 3)


class EmergencyContactsDirectoryView(APIView):
    """GET /api/student/emergency-contacts – public contact directory."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(emergency_contact_directory())


class StudentEmergencyContactsView(APIView):
    """Students create + track their own urgent messages to admin."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        student = _get_student(request)
        contacts = (
            EmergencyContact.objects.filter(student=student)
            .select_related("student", "assigned_to", "resolved_by")
            .order_by("-created_at"))
        serializer = EmergencyContactSerializer(contacts, many=True)
        # Unread reply = has a reply the student hasn't marked seen.
        # (Timestamps can tie within one request, so the marker itself —
        # cleared on each new reply — is the source of truth.)
        unread = sum(
            1 for c in contacts
            if c.admin_reply and c.student_read_at is None)
        return Response({
            "contacts": serializer.data,
            "unread_replies": unread,
            "directory": emergency_contact_directory(),
        })

    def post(self, request):
        student = _get_student(request)
        serializer = EmergencyContactCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        contact = EmergencyContact.objects.create(
            student=student,
            category=data["category"],
            priority=data["priority"],
            subject=data["subject"].strip(),
            message=data["message"].strip(),
            contact_phone=(data.get("contact_phone") or student.phone or ""),
            contact_email=(data.get("contact_email") or student.email or ""),
            location=data.get("location") or "",
        )
        audit("communication.emergency_contact_created",
              actor=request.user, entity=contact,
              summary={"student_id": student.id,
                       "category": contact.category,
                       "priority": contact.priority})
        # Alert every admin/super-admin in-app (bell polls each minute)
        # and, best-effort, by email for urgent/high priorities.
        label = contact.get_priority_display()
        title = f"{label} emergency contact from {student.full_name}"
        body = (f"{contact.get_category_display()}: {contact.subject}. "
                f"Reach them on {contact.contact_phone or contact.contact_email or '—'}"
                + (f" · {contact.location}" if contact.location else ""))
        notify_admins("emergency", title, body,
                      link="/admin/emergency-contacts")
        if contact.priority in ("urgent", "high"):
            email_admins(f"[{label}] Emergency contact — {contact.subject}",
                         f"{student.full_name} ({student.matricule or ''})\n"
                         f"Category: {contact.get_category_display()}\n"
                         f"Priority: {label}\nLocation: {contact.location or '—'}\n"
                         f"Phone: {contact.contact_phone or '—'} · "
                         f"Email: {contact.contact_email or '—'}\n\n"
                         f"{contact.message}")
        return Response(EmergencyContactSerializer(contact).data,
                        status=status.HTTP_201_CREATED)


class EmergencyContactsSummaryView(APIView):
    """GET /api/admin/emergency-contacts/summary – inbox counters."""

    permission_classes = [IsAdmin]

    def get(self, request):
        qs = EmergencyContact.objects.all()
        return Response({
            "open": qs.filter(status="open").count(),
            "in_progress": qs.filter(status="in_progress").count(),
            "urgent_open": qs.filter(status__in=["open", "in_progress"],
                                     priority="urgent").count(),
            "unread": qs.filter(status__in=["open", "in_progress"],
                                admin_read_at__isnull=True).count(),
            "total": qs.count(),
        })


class EmergencyContactViewSet(viewsets.ModelViewSet):
    """Admin inbox: triage, assign, reply to and resolve cases."""

    serializer_class = EmergencyContactSerializer
    permission_classes = [IsAdmin]
    filterset_fields = ["status", "priority", "category", "student"]
    search_fields = ["subject", "message", "student__first_name",
                     "student__last_name", "student__matricule"]

    def get_queryset(self):
        from django.db.models import Case, IntegerField, Value, When

        # Needs attention first (open, then in progress), most urgent, newest.
        status_rank = Case(
            When(status="open", then=Value(0)),
            When(status="in_progress", then=Value(1)),
            When(status="resolved", then=Value(2)),
            default=Value(3), output_field=IntegerField())
        priority_rank = Case(
            When(priority="urgent", then=Value(0)),
            When(priority="high", then=Value(1)),
            default=Value(2), output_field=IntegerField())
        return (
            EmergencyContact.objects.select_related(
                "student", "student__program", "assigned_to", "resolved_by")
            .annotate(_status_rank=status_rank, _priority_rank=priority_rank)
            .order_by("_status_rank", "_priority_rank", "-created_at"))

    def create(self, request, *args, **kwargs):
        # Cases are student-created only; admins respond, never fabricate.
        raise BusinessError(
            "Emergency contacts are created by students only.",
            code="EMERGENCY_CONTACT_STUDENT_ONLY")

    def destroy(self, request, *args, **kwargs):
        # Case records are never deleted — resolve/close them instead so
        # the audit trail (who was told what, when) is preserved.
        raise BusinessError(
            "Emergency contacts cannot be deleted; resolve or close them.",
            code="EMERGENCY_CONTACT_NOT_DELETABLE")

    def update(self, request, *args, **kwargs):
        contact = self.get_object()
        serializer = EmergencyContactAdminUpdateSerializer(data=request.data,
                                                           partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        now = timezone.now()
        changed = []

        if "status" in data and data["status"] != contact.status:
            contact.status = data["status"]
            changed.append("status")
            if contact.status in ("resolved", "closed"):
                contact.resolved_by = request.user
                contact.resolved_at = now
            else:
                contact.resolved_by = None
                contact.resolved_at = None

        if "assigned_to" in data:
            from accounts.models import User

            assignee = None
            if data["assigned_to"] is not None:
                assignee = User.objects.filter(id=data["assigned_to"]).first()
                if assignee is None:
                    raise BusinessError("Assigned user not found.",
                                        code="USER_NOT_FOUND")
            contact.assigned_to = assignee
            changed.append("assigned_to")

        if "mark_read" in data and data["mark_read"]:
            contact.admin_read_at = now
            changed.append("admin_read")

        reply = (data.get("admin_reply") or "").strip()
        if reply and reply != contact.admin_reply:
            contact.admin_reply = reply
            changed.append("admin_reply")
            if contact.first_response_at is None:
                contact.first_response_at = now
            if contact.status == "open":
                contact.status = "in_progress"
                changed.append("status")
            # Reopen the loop for the student: a new reply is unread until
            # they mark it seen (their read view re-stamps student_read_at).
            if contact.student_read_at is not None:
                contact.student_read_at = None
                changed.append("student_unread")
            notify(contact.student.user, "emergency",
                   "Admin replied to your emergency contact",
                   f"{contact.subject}: {reply[:160]}",
                   link="/contact-admin")

        if contact.admin_read_at is None:
            contact.admin_read_at = now
        contact.save()
        audit("communication.emergency_contact_updated",
              actor=request.user, entity=contact,
              summary={"fields": changed, "status": contact.status})
        return Response(EmergencyContactSerializer(contact).data)

    partial_update = update

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        """POST /api/admin/emergency-contacts/:id/mark-read/"""
        contact = self.get_object()
        contact.admin_read_at = timezone.now()
        contact.save(update_fields=["admin_read_at", "updated_at"])
        return Response(EmergencyContactSerializer(contact).data)


class StudentEmergencyContactReadView(APIView):
    """POST /api/student/emergency-contacts/:id/read – mark reply seen."""

    permission_classes = [IsAuthenticated]

    def post(self, request, contact_id):
        student = _get_student(request)
        contact = EmergencyContact.objects.filter(
            id=contact_id, student=student).first()
        if contact is None:
            raise BusinessError("Emergency contact not found.",
                                code="EMERGENCY_CONTACT_NOT_FOUND")
        contact.student_read_at = timezone.now()
        contact.save(update_fields=["student_read_at", "updated_at"])
        return Response(EmergencyContactSerializer(contact).data)


class DeadlineNotificationsView(APIView):
    """GET /api/student/deadlines – fee/registration deadlines as alerts."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from academics.services import current_semester

        semester = current_semester()
        if semester is None:
            return Response({"deadlines": []})
        now = timezone.now()
        deadlines = []
        for kind, when in (("fee", semester.fee_deadline),
                           ("registration", semester.registration_deadline)):
            if when is None:
                continue
            days = (when - now).days
            deadlines.append({
                "type": kind,
                "deadline": when,
                "semester_id": semester.id,
                "semester": str(semester),
                "days_remaining": days,
                "overdue": when < now,
                "urgent": 0 <= days <= 7,
            })
        return Response({"deadlines": deadlines})
