"""Router that accepts URLs with or without a trailing slash.

DRF's ``trailing_slash`` argument is coerced to ``'/'``/``''``; the blueprint
(SS8) writes paths like ``POST /api/student/registrations`` (no slash), so
routes here use the optional-slash regex ``/?``.
"""
from rest_framework.routers import DefaultRouter


class OptionalSlashRouter(DefaultRouter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.trailing_slash = "/?"
