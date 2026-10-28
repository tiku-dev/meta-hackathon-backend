from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.audit import audit
from core.exceptions import BusinessError
from core.permissions import IsAcademicsAdmin, IsAdmin, has_role

from .models import Announcement, Event, EventRSVP, Notification
from .serializers import (
    AnnouncementSerializer, EventRSVPSerializer, EventSerializer,
    NotificationSerializer,
)
from .services import visible_announcements, visible_events


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
