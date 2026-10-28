"""Uniform API errors.

Every business-rule rejection carries a stable machine-readable ``code``
(blueprint SS8): REGISTRATION_CLOSED, PAYMENT_THRESHOLD_NOT_MET,
CREDIT_LIMIT_EXCEEDED, COURSE_FULL, TIMETABLE_CONFLICT, RESULTS_LOCKED, ...
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


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    detail = response.data
    code = getattr(exc, "code", None) or getattr(exc, "default_code", "error")
    message = "Request failed."
    if isinstance(detail, dict):
        if isinstance(detail.get("detail"), str):
            message = detail["detail"]
        elif detail:
            message = str(next(iter(detail.values())))
    elif isinstance(detail, list) and detail:
        message = str(detail[0])
    response.data = {
        "error": {
            "code": code,
            "message": message,
            "status": response.status_code,
            "details": detail,
        }
    }
    return response
