from rest_framework import exceptions, serializers
from rest_framework.exceptions import ErrorDetail
from core.exceptions import exception_handler, BusinessError


def test_exception_handler_blank_field():
    exc = exceptions.ValidationError({"first_name": [ErrorDetail("This field may not be blank.", code="blank")]})
    resp = exception_handler(exc, context={})
    assert resp is not None
    assert resp.status_code == 400
    assert resp.data["error"]["message"] == "First name cannot be blank."
    assert resp.data["error"]["details"] == {"first_name": ["This field may not be blank."]}


def test_exception_handler_required_field():
    exc = exceptions.ValidationError({"current_level": [ErrorDetail("This field is required.", code="required")]})
    resp = exception_handler(exc, context={})
    assert resp is not None
    assert resp.data["error"]["message"] == "Current level is required."


def test_exception_handler_multiple_fields():
    exc = exceptions.ValidationError({
        "first_name": [ErrorDetail("This field may not be blank.", code="blank")],
        "last_name": [ErrorDetail("This field may not be blank.", code="blank")],
    })
    resp = exception_handler(exc, context={})
    assert resp is not None
    assert "First name cannot be blank." in resp.data["error"]["message"]
    assert "Last name cannot be blank." in resp.data["error"]["message"]
    # Ensure no raw ErrorDetail repr is leaked into message or details
    assert "ErrorDetail(" not in resp.data["error"]["message"]
    assert "ErrorDetail(" not in str(resp.data["error"]["details"])


def test_exception_handler_business_error():
    exc = BusinessError("Only department heads can sign off.", code="HOD_REQUIRED")
    resp = exception_handler(exc, context={})
    assert resp is not None
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "HOD_REQUIRED"
    assert resp.data["error"]["message"] == "Only department heads can sign off."

