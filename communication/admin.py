from django.contrib import admin

from .models import (Announcement, EmergencyContact, Event, EventRSVP,
                     Notification)

for model in (Announcement, Notification, Event, EventRSVP):
    admin.site.register(model)


@admin.register(EmergencyContact)
class EmergencyContactAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "category", "priority", "status",
                    "assigned_to", "created_at")
    list_filter = ("status", "priority", "category")
    search_fields = ("subject", "message", "student__first_name",
                     "student__last_name", "student__matricule")
    readonly_fields = ("student", "category", "priority", "subject",
                       "message", "contact_phone", "contact_email",
                       "location", "created_at", "updated_at")
