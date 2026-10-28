"""Per-request context (request id + client IP) used by the audit service."""
import threading
import uuid

_local = threading.local()


class AuditRequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        _local.ip = (
            xff.split(",")[0].strip()
            if xff
            else request.META.get("REMOTE_ADDR")
        )
        _local.user = None
        response = self.get_response(request)
        response["X-Request-ID"] = getattr(_local, "request_id", "")
        return response


def get_request_context():
    return {
        "request_id": getattr(_local, "request_id", ""),
        "ip": getattr(_local, "ip", None),
    }
