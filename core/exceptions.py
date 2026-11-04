"""Uniform API errors.

Every business-rule rejection carries a stable machine-readable ``code``
(blueprint SS8): REGISTRATION_CLOSED, PAYMENT_THRESHOLD_NOT_MET,
REGISTRATION_FEE_REQUIRED, CREDIT_LIMIT_EXCEEDED, COURSE_FULL,
TIMETABLE_CONFLICT, RESULTS_LOCKED, ...
"""
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_exception_handler


class BusinessError(APIException):
    """Domain rule violation with a stable error code."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "BUSINESS_RULE_VIOLATION"

    def __init__(self, detail: str, code: str | None = None, http_status=None):
        self.code = code or self.default_code
        if http_status is not None:
            self.status_code = http_status
        super().__init__(detail, code=self.code)


def business_error(detail: str, code: str, http_status=status.HTTP_400_BAD_REQUEST):
    return BusinessError(detail, code=code, http_status=http_status)


def _clean_error_value(val):
    """Convert ErrorDetail / list / dict into clean python primitives without reprs."""
    if isinstance(val, (list, tuple)):
        return [_clean_error_value(v) for v in val]
    if isinstance(val, dict):
        return {str(k): _clean_error_value(v) for k, v in val.items()}
    return str(val)


def _humanize_field_name(name: str) -> str:
    if not name or name in ("non_field_errors", "detail", "__all__"):
        return ""
    words = name.replace("_", " ").split()
    if not words:
        return ""
    return " ".join([words[0].capitalize(), *words[1:]])


def _format_error_phrase(field_name: str, raw_msg) -> str:
    label = _humanize_field_name(str(field_name)) if field_name else ""
    msg = str(raw_msg).strip()
    norm = msg.rstrip(".").lower()
    if norm == "this field may not be blank":
        return f"{label} cannot be blank." if label else "This field cannot be blank."
    if norm == "this field is required":
        return f"{label} is required." if label else "This field is required."
    if norm in ("this field may not be null", "this field cannot be null"):
        return f"{label} is required." if label else "This field cannot be empty."
    if label:
        return f"{label}: {msg}"
    return msg


def _collect_messages(data, field_prefix=""):
    messages = []
    if isinstance(data, (list, tuple)):
        for item in data:
            messages.extend(_collect_messages(item, field_prefix))
    elif isinstance(data, dict):
        if "detail" in data and isinstance(data["detail"], (str, list)):
            messages.extend(_collect_messages(data["detail"], field_prefix))
        elif "non_field_errors" in data:
            messages.extend(_collect_messages(data["non_field_errors"], field_prefix))
        for key, val in data.items():
            if key in ("detail", "non_field_errors"):
                continue
            nested_name = f"{field_prefix}.{key}" if field_prefix else key
            messages.extend(_collect_messages(val, nested_name))
    else:
        messages.append(_format_error_phrase(field_prefix, data))
    return messages


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    detail = response.data
    code = getattr(exc, "code", None) or getattr(exc, "default_code", "error")
    cleaned_detail = _clean_error_value(detail)
    collected = _collect_messages(detail)
    message = " ".join(collected) if collected else "Request failed."

    response.data = {
        "error": {
            "code": code,
            "message": message,
            "status": response.status_code,
            "details": cleaned_detail,
        }
    }
    return response
