from rest_framework import serializers

from .models import Announcement, Event, EventRSVP, Notification


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
