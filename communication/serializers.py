from rest_framework import serializers

from .models import (Announcement, EmergencyContact, Event, EventRSVP,
                     Notification)


class AnnouncementSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = Announcement
        fields = ["id", "author", "author_name", "title", "body", "audience_type",
                  "audience_unit", "audience_program", "audience_level",
                  "audience_role", "status", "publish_at", "expires_at",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "author", "created_at", "updated_at"]

    def get_author_name(self, obj):
        return obj.author.get_full_name() or obj.author.email if obj.author_id else None


class EmergencyContactSerializer(serializers.ModelSerializer):
    """Student-owned emergency contact with the admin reply workflow."""

    student_name = serializers.CharField(source="student.full_name",
                                         read_only=True)
    student_matricule = serializers.CharField(source="student.matricule",
                                              read_only=True, default="")
    student_email = serializers.CharField(source="student.email",
                                          read_only=True)
    student_phone = serializers.CharField(source="student.phone",
                                          read_only=True)
    program_name = serializers.CharField(source="student.program.name",
                                         read_only=True, default=None)
    assigned_to_name = serializers.SerializerMethodField()
    resolved_by_name = serializers.SerializerMethodField()
    is_read_by_admin = serializers.SerializerMethodField()
    is_read_by_student = serializers.SerializerMethodField()
    needs_admin_attention = serializers.SerializerMethodField()

    class Meta:
        model = EmergencyContact
        fields = [
            "id", "student", "student_name", "student_matricule",
            "student_email", "student_phone", "program_name",
            "category", "priority", "subject", "message",
            "contact_phone", "contact_email", "location",
            "status", "admin_reply", "assigned_to", "assigned_to_name",
            "resolved_by", "resolved_by_name", "resolved_at",
            "first_response_at", "student_read_at", "admin_read_at",
            "is_read_by_admin", "is_read_by_student",
            "needs_admin_attention", "created_at", "updated_at",
        ]
        # The write serializers below own creation/updates; this read
        # serializer never trusts client-supplied workflow fields.
        read_only_fields = fields

    def get_assigned_to_name(self, obj):
        if obj.assigned_to_id:
            return (obj.assigned_to.get_full_name()
                    or obj.assigned_to.email)
        return None

    def get_resolved_by_name(self, obj):
        if obj.resolved_by_id:
            return (obj.resolved_by.get_full_name()
                    or obj.resolved_by.email)
        return None

    def get_is_read_by_admin(self, obj):
        return obj.admin_read_at is not None

    def get_is_read_by_student(self, obj):
        return obj.student_read_at is not None

    def get_needs_admin_attention(self, obj):
        # A case needs admin attention while it is unresolved and no admin
        # has opened it yet (admin_read_at is stamped on open/update).
        return obj.status in ("open", "in_progress") and obj.admin_read_at is None


class EmergencyContactCreateSerializer(serializers.Serializer):
    category = serializers.ChoiceField(
        choices=EmergencyContact.CATEGORY_CHOICES, default="other")
    priority = serializers.ChoiceField(
        choices=EmergencyContact.PRIORITY_CHOICES, default="normal")
    subject = serializers.CharField(min_length=3, max_length=200)
    message = serializers.CharField(min_length=10)
    contact_phone = serializers.CharField(max_length=32, required=False,
                                        allow_blank=True, default="")
    contact_email = serializers.EmailField(required=False,
                                           allow_blank=True, default="")
    location = serializers.CharField(max_length=255, required=False,
                                     allow_blank=True, default="")


class EmergencyContactAdminUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=EmergencyContact.STATUS_CHOICES, required=False)
    admin_reply = serializers.CharField(required=False, allow_blank=True)
    assigned_to = serializers.IntegerField(required=False, allow_null=True)
    mark_read = serializers.BooleanField(required=False)


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ["id", "notification_type", "title", "body", "link",
                  "read_at", "is_read", "created_at"]

    def get_is_read(self, obj):
        return obj.read_at is not None


class EventSerializer(serializers.ModelSerializer):
    rsvp_count = serializers.SerializerMethodField()
    my_rsvp = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = ["id", "title", "details", "start_at", "end_at", "location",
                  "meeting_link", "organizer", "audience_type", "audience_program",
                  "audience_level", "rsvp_enabled", "is_published",
                  "rsvp_count", "my_rsvp", "created_at", "updated_at"]
        read_only_fields = ["id", "organizer", "created_at", "updated_at"]

    def get_rsvp_count(self, obj):
        return obj.rsvps.filter(status="going").count()

    def get_my_rsvp(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return None
        rsvp = obj.rsvps.filter(user=request.user).first()
        return rsvp.status if rsvp else None


class EventRSVPSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventRSVP
        fields = ["id", "event", "user", "status", "note", "created_at", "updated_at"]
        read_only_fields = ["id", "user", "created_at", "updated_at"]
