"""One-time registration fee for NEW students (paid once, ever).

Rules under test:
- the fee amount comes from an admin-created FeeSchedule with
  fee_type="registration_fee" (never hard-coded);
- a student admitted AFTER that schedule exists is a NEW student: any
  tuition/fee payment is rejected with REGISTRATION_FEE_REQUIRED until
  the fee is verified paid;
- paying it marks the DB RegistrationFeeRecord paid, unlocks tuition,
  and the student is never charged again (a second payment attempt on
  the fee invoice is refused, fees page no longer requires it);
- students admitted BEFORE the schedule existed (pre-existing /
  grandfathered) are never charged — including when the fee is created
  in their fixture world afterwards;
- finance can waive the fee (recorded), which also unlocks tuition;
- the fee invoice does not count toward the semester fee threshold.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

PASSWORD = "DemoPass123!"
FEE = Decimal("5000")


def auth(api, user):
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def assert_error(resp, code, status=400):
    assert resp.status_code == status, resp.content
    assert resp.data["error"]["code"] == code, resp.data


def make_user(email, role, username=None):
    from accounts.models import User, UserRole

    user = User(email=email, username=username or email, is_active=True)
    user.set_password(PASSWORD)
    user.save()
    UserRole.objects.create(user=user, role=role)
    return user


def make_student(world, matricule, *, created_days_ago=0):
    from accounts.models import StudentProfile

    user = make_user(f"{matricule.lower()}@test.edu", "student",
                     username=matricule)
    student = StudentProfile.objects.create(
        user=user, matricule=matricule, first_name="New", last_name="Student",
        email=f"{matricule.lower()}@test.edu", program=world["program"],
        current_level=world["level"], admission_year=world["year"],
        status="active")
    if created_days_ago:
        StudentProfile.objects.filter(pk=student.pk).update(
            created_at=timezone.now() - timedelta(days=created_days_ago))
        student.refresh_from_db()
    return student, user


def add_registration_fee(world, amount=FEE):
    from finance.models import FeeSchedule

    return FeeSchedule.objects.create(
        fee_type="registration_fee", currency="XAF", amount=amount,
        is_active=True)


def pay_via_mock(client, invoice_id, amount):
    """Start a payment and verify it through the mock provider."""
    resp = client.post(f"/api/invoices/{invoice_id}/payments",
                       {"amount": str(amount)}, format="json")
    assert resp.status_code == 201, resp.content
    reference = resp.data["reference"]
    resp = client.post(f"/api/payments/{reference}/mock-simulate",
                       {"outcome": "succeeded"}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["status"] == "succeeded"
    return resp.data


@pytest.mark.django_db
def test_new_student_must_pay_registration_fee_first_then_never_again(api, world):
    from finance.models import Invoice, RegistrationFeeRecord
    from finance.services import ensure_invoice, registration_fee_status

    schedule = add_registration_fee(world)
    student, user = make_student(world, "UB26-COMP-9001")
    client = auth(api, user)

    # DB record: required, unpaid, new student, admin amount.
    info = registration_fee_status(student)
    assert info["required"] is True
    assert info["status"] == "unpaid"
    assert info["is_new_student"] is True
    assert Decimal(info["amount"]) == FEE

    tuition = ensure_invoice(student, world["semester"], actor=user)

    # Tuition payment is blocked before the registration fee.
    resp = client.post(f"/api/invoices/{tuition.id}/payments",
                       {"amount": "60000"}, format="json")
    assert_error(resp, "REGISTRATION_FEE_REQUIRED")

    # Fees page surfaces the fee + its own payable invoice.
    resp = client.get("/api/student/fees")
    assert resp.status_code == 200, resp.content
    reg = resp.data["registration_fee"]
    assert reg["required"] is True
    assert reg["invoice"] is not None
    fee_invoice_id = reg["invoice"]["id"]
    assert reg["invoice"]["total_due"] == "5000.00"

    # Course registration is blocked too (fee reason reported).
    resp = client.get("/api/student/registration/eligibility")
    codes = [r["code"] for r in resp.data["reasons"]]
    assert "REGISTRATION_FEE_REQUIRED" in codes

    # Pay the registration fee itself (its own invoice is exempt).
    pay_via_mock(client, fee_invoice_id, FEE)
    record = RegistrationFeeRecord.objects.get(student=student)
    assert record.status == "paid"
    assert record.paid_invoice_id == fee_invoice_id
    info = registration_fee_status(student)
    assert info["required"] is False
    assert info["paid"] is True

    # Paying it again is impossible — nothing left to pay on that invoice.
    resp = client.post(f"/api/invoices/{fee_invoice_id}/payments",
                       {"amount": "5000"}, format="json")
    assert_error(resp, "INVOICE_NOT_PAYABLE")
    assert RegistrationFeeRecord.objects.filter(student=student).count() == 1

    # Tuition now unlocks and credits the semester invoice only.
    pay_via_mock(client, tuition.id, Decimal("60000"))
    tuition.refresh_from_db()
    assert tuition.status == "paid"
    assert tuition.amount_paid == Decimal("60000")

    # Fees page: fee no longer required; paid flag visible.
    resp = client.get("/api/student/fees")
    reg = resp.data["registration_fee"]
    assert reg["required"] is False
    assert reg["status"] == "paid"
    assert reg["paid"] is True

    # The fee payment did NOT count toward the semester threshold:
    # eligibility's payment figures come from the tuition invoice.
    resp = client.get("/api/student/registration/eligibility")
    assert resp.data["eligible"] is True
    assert resp.data["payment"]["invoice_id"] == tuition.id



@pytest.mark.django_db
def test_existing_students_are_never_charged(api, world):
    """Students on the books before the fee exists pay tuition directly."""
    from finance.services import ensure_invoice, registration_fee_status

    add_registration_fee(world)
    # world["student"] was admitted in the fixture BEFORE this schedule.
    veteran, veteran_user = world["student"], world["student_user"]
    info = registration_fee_status(veteran)
    assert info["required"] is False
    assert info["status"] == "not_required"
    assert info["is_new_student"] is False

    client = auth(api, veteran_user)
    tuition = ensure_invoice(veteran, world["semester"], actor=veteran_user)
    pay_via_mock(client, tuition.id, Decimal("60000"))
    tuition.refresh_from_db()
    assert tuition.status == "paid"

    # A backdated (pre-fee) student is likewise grandfathered.
    old, old_user = make_student(world, "UB26-COMP-9002", created_days_ago=30)
    from finance.models import FeeSchedule

    schedule = FeeSchedule.objects.get(fee_type="registration_fee")
    FeeSchedule.objects.filter(pk=schedule.pk).update(
        created_at=timezone.now() - timedelta(days=1))
    info = registration_fee_status(old)
    assert info["required"] is False
    assert info["status"] == "not_required"


@pytest.mark.django_db
def test_no_fee_configured_means_no_gate(api, world):
    """Without an admin registration-fee schedule, nothing changes."""
    from finance.services import ensure_invoice, registration_fee_status

    student, user = make_student(world, "UB26-COMP-9003")
    info = registration_fee_status(student)
    assert info["required"] is False
    assert info["status"] == "not_required"

    client = auth(api, user)
    tuition = ensure_invoice(student, world["semester"], actor=user)
    pay_via_mock(client, tuition.id, Decimal("60000"))


@pytest.mark.django_db
def test_finance_can_waive_the_registration_fee(api, world):
    from finance.models import RegistrationFeeRecord
    from finance.services import ensure_invoice, registration_fee_status

    add_registration_fee(world)
    student, user = make_student(world, "UB26-COMP-9004")
    client = auth(api, user)
    tuition = ensure_invoice(student, world["semester"], actor=user)

    resp = client.post(f"/api/invoices/{tuition.id}/payments",
                       {"amount": "60000"}, format="json")
    assert_error(resp, "REGISTRATION_FEE_REQUIRED")

    admin = auth(api, world["admin"])
    resp = admin.post(
        f"/api/admin/invoices/{tuition.id}/waive-registration-fee/",
        {"reason": "Scholarship"}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["status"] == "waived"

    record = RegistrationFeeRecord.objects.get(student=student)
    assert record.status == "waived"
    assert record.waiver_reason == "Scholarship"
    assert registration_fee_status(student)["required"] is False

    # Tuition unlocks after the waiver.
    pay_via_mock(client, tuition.id, Decimal("60000"))

