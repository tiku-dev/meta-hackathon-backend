from django.contrib import admin

from .models import Announcement, Event, EventRSVP, Notification

for model in (Announcement, Notification, Event, EventRSVP):
    admin.site.register(model)
