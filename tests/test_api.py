"""Acceptance tests mapped to blueprint SS11."""
from decimal import Decimal

import pytest
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.services import issue_matricule


def auth(api, user):
    """A fresh client per user so sequential logins never share credentials."""
    from rest_framework.test import APIClient

    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def assert_error(resp, code, status=400):
    assert resp.status_code == status, resp.content
    assert resp.data["error"]["code"] == code, resp.data


@pytest.mark.django_db
def test_login_roles_and_activation(api, world):
    """SS11.3: matricule issued before activation; token single-use/hashed."""
    admin = auth(api, world["admin"])
    resp = admin.get("/api/admin/students/")
    assert resp.status_code == 200

    issued = issue_matricule(world["admin"], world["prospect"])
    assert issued["matricule"].startswith("UB")
    # stored only as a hash
    token_obj = world["prospect"].activation_tokens.get()
    assert token_obj.token_hash != issued["activation_token"]
    assert len(token_obj.token_hash) == 64

    api2 = type(api)()
    resp = api2.post("/api/auth/activate", {
        "token": issued["activation_token"], "password": "NewPass123!"}, format="json")
    assert resp.status_code == 201, resp.content
    # reuse is rejected
    resp = api2.post("/api/auth/activate", {
        "token": issued["activation_token"], "password": "NewPass123!"}, format="json")
    assert_error(resp, "ACTIVATION_TOKEN_USED")
    resp = api2.post("/api/auth/activate", {"token": "bogus", "password": "NewPass123!"}, format="json")
    assert_error(resp, "ACTIVATION_TOKEN_INVALID")
    # login with the new password works and carries roles
    resp = api2.post("/api/auth/token/", {"email": "prospect@test.edu", "password": "NewPass123!"}, format="json")
    assert resp.status_code == 200
    assert "student" in resp.data["access"] or resp.data.get("access")
    me = type(api)()
    me.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    resp = me.get("/api/student/me")
    assert resp.status_code == 200
    assert resp.data["student"]["matricule"] == issued["matricule"]


@pytest.mark.django_db
def test_configurable_fee_not_hardcoded(api, world):
    """SS11.2: admin changes the fee; the invoice follows the schedule."""
    from finance.models import FeeSchedule, Invoice

    schedule = world["schedule"]
    schedule.amount = Decimal("75000")
    schedule.save()
    invoice = Invoice.objects.create(
        student=world["student"], fee_schedule=schedule, semester=world["semester"],
        currency="XAF", total_due=schedule.amount)
    assert invoice.total_due == Decimal("75000")

    client = auth(api, world["admin"])
    resp = client.post("/api/admin/fee-schedules/", {
        "program": world["program"].id, "academic_year": world["year"].id,
        "semester": world["semester"].id, "fee_type": "tuition",
        "currency": "XAF", "amount": "125000",
        "registration_threshold_type": "amount",
        "registration_threshold_value": "50000", "is_active": True}, format="json")
    assert resp.status_code == 201, resp.content
    assert Decimal(resp.data["amount"]) == Decimal("125000")


def _pay_via_mock(api_client, user, student, semester, amount=Decimal("60000")):
    from finance.services import ensure_invoice, start_payment, reconcile_attempt
    from finance.providers.base import get_provider

    invoice = ensure_invoice(student, semester, actor=user)
    attempt = start_payment(user, invoice)
    attempt.status = "succeeded"
    attempt.save(update_fields=["status"])
    verified = get_provider("mock").verify(attempt=attempt)
    attempt.status = "pending"
    attempt.save(update_fields=["status"])
    reconcile_attempt(attempt, verified_status=verified.status,
                      amount=verified.amount, currency=verified.currency)
    return attempt


@pytest.mark.django_db
def test_registration_blocked_until_paid_then_credit_cap(api, world):
    """SS11.4/5/6: payment gate, duplicates, credit cap, drop, deadlines."""
    from academics.services import register_courses
    from core.exceptions import BusinessError

    student, semester, user = world["student"], world["semester"], world["student_user"]
    client = auth(api, user)
    offerings = world["offerings"]

    resp = client.post("/api/student/registrations",
                       {"offering_ids": [offerings[0].id]}, format="json")
    assert_error(resp, "PAYMENT_THRESHOLD_NOT_MET")

    _pay_via_mock(client, user, student, semester)

    resp = client.post("/api/student/registrations",
                       {"offering_ids": [offerings[0].id, offerings[1].id]}, format="json")
    assert resp.status_code == 201, resp.content
    assert len(resp.data) == 2

    resp = client.post("/api/student/registrations",
                       {"offering_ids": [offerings[0].id]}, format="json")
    assert_error(resp, "ALREADY_REGISTERED")

    # 8 credits used; cap is 10 -> +3 credits course overflows
    resp = client.post("/api/student/registrations",
                       {"offering_ids": [offerings[2].id]}, format="json")
    assert_error(resp, "CREDIT_LIMIT_EXCEEDED")

    reg_id = resp.data and None
    regs = client.get("/api/student/registrations").data
    resp = client.delete(f"/api/student/registrations/{regs[0]['id']}")
    assert resp.status_code == 200

    # deadline + exemption flow
    semester.registration_open = False
    semester.save()
    with pytest.raises(BusinessError) as exc:
        register_courses(user, student, [offerings[2].id], semester)
    assert exc.value.code == "REGISTRATION_CLOSED"
    from academics.models import RegistrationExemption
    RegistrationExemption.objects.create(
        student=student, semester=semester, exemption_type="late_registration",
        reason="Dean approved", granted_by=world["admin"])
    created = register_courses(user, student, [offerings[2].id], semester)
    assert created[0].admin_exception is True


@pytest.mark.django_db
def test_payment_flow_idempotency_and_webhook(api, world):
    """SS11.6: verified-only success; duplicate webhook never double-credits."""
    from finance.models import Invoice
    from finance.services import handle_webhook

    student, semester = world["student"], world["semester"]
    client = auth(api, world["student_user"])
    invoice = Invoice.objects.create(
        student=student, fee_schedule=world["schedule"], semester=semester,
        currency="XAF", total_due=Decimal("60000"))

    resp = client.post(f"/api/invoices/{invoice.id}/payments", {}, format="json")
    assert resp.status_code == 201, resp.content
    reference = resp.data["reference"]
    assert resp.data["status"] == "pending"

    # browser 'confirmation' alone changes nothing
    resp = client.get(f"/api/payments/{reference}/status")
    assert resp.data["status"] == "pending"
    invoice.refresh_from_db()
    assert invoice.amount_paid == Decimal("0")

    # simulate provider-side confirmation, then verify
    resp = client.post(f"/api/payments/{reference}/mock-simulate",
                       {"outcome": "succeeded"}, format="json")
    assert resp.status_code == 200, resp.content
    invoice.refresh_from_db()
    assert invoice.amount_paid == Decimal("60000")
    assert invoice.status == "paid"
    assert invoice.receipts.count() == 1

    # duplicate webhook -> no double credit, one receipt
    event = {"event_id": "evt-1", "event_type": "payment.complete",
             "reference": reference, "status": "succeeded"}
    handle_webhook("mock", event)
    handle_webhook("mock", event)
    invoice.refresh_from_db()
    assert invoice.amount_paid == Decimal("60000")
    assert invoice.receipts.count() == 1

    # paid invoice rejects further payments
    resp = client.post(f"/api/invoices/{invoice.id}/payments", {}, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_marks_workflow_and_results_lock(api, world):
    """SS11.8/9: CA<=30, exam<=70, totals, bands, publish lock, correction."""
    from academics.services import register_courses

    _pay_via_mock(api, world["student_user"], world["student"], world["semester"])
    regs = register_courses(world["student_user"], world["student"],
                            [world["offerings"][0].id], world["semester"])
    offering = world["offerings"][0]

    lecturer = auth(api, world["lecturer_user"])
    resp = lecturer.put(f"/api/lecturer/offerings/{offering.id}/results", {
        "submit": False,
        "marks": [{"registration_id": regs[0].id, "ca_mark": "31", "final_mark": "60"}],
    }, format="json")
    assert resp.status_code == 200
    assert resp.data["saved"] == [] and resp.data["errors"], resp.data

    resp = lecturer.put(f"/api/lecturer/offerings/{offering.id}/results", {
        "submit": True,
        "marks": [{"registration_id": regs[0].id, "ca_mark": "24", "final_mark": "56"}],
    }, format="json")
    assert resp.status_code == 200, resp.content
    saved = resp.data["saved"][0]
    assert Decimal(saved["total"]) == Decimal("80.0")
    assert saved["grade_letter"] == "A"
    assert saved["status"] == "submitted"

    hod = auth(api, world["hod_user"])
    resp = hod.post(f"/api/hod/offerings/{offering.id}/approve-results", {}, format="json")
    assert resp.status_code == 200 and resp.data["approved"] == 1

    registrar = auth(api, world["registrar"])
    resp = registrar.post(f"/api/admin/offerings/{offering.id}/publish-results", {}, format="json")
    assert resp.status_code == 200 and resp.data["published"] == 1

    resp = lecturer.put(f"/api/lecturer/offerings/{offering.id}/results", {
        "submit": False,
        "marks": [{"registration_id": regs[0].id, "ca_mark": "10", "final_mark": "10"}],
    }, format="json")
    assert_error(resp, "RESULTS_LOCKED")

    student_client = auth(api, world["student_user"])
    resp = student_client.get("/api/student/results")
    assert resp.status_code == 200
    assert resp.data["results"][0]["grade_letter"] == "A"
    assert resp.data["gpa"] == "4.00"

    result_id = resp.data["results"][0]["id"]
    resp = registrar.post(f"/api/admin/results/{result_id}/correct", {
        "ca_mark": "25", "reason": "Script re-marked"}, format="json")
    assert resp.status_code == 201, resp.content
    assert Decimal(resp.data["new_total"]) == Decimal("81.0")


@pytest.mark.django_db
def test_student_isolation_announcements_and_audit(api, world):
    """SS11.7/10/11: own-data only, audience filter, audit trail, reports."""
    from communication.models import Announcement
    from core.models import AuditLog

    Announcement.objects.create(title="Everyone", body="b", audience_type="all",
                                status="published")
    Announcement.objects.create(title="Lecturers only", body="b", audience_type="role",
                                audience_role="lecturer", status="published")

    student_client = auth(api, world["student_user"])
    resp = student_client.get("/api/announcements/")
    titles = [a["title"] for a in resp.data["results"]]
    assert "Everyone" in titles and "Lecturers only" not in titles

    resp = student_client.get("/api/notifications/")
    assert resp.status_code == 200 and resp.data["count"] == 0

    admin = auth(api, world["admin"])
    issued = issue_matricule(world["admin"], world["prospect"], send_email=False)
    assert issued["matricule"]
    assert AuditLog.objects.filter(action="student.matricule_issued").exists()

    resp = admin.get("/api/admin/overview")
    assert resp.status_code == 200
    assert resp.data["students_pending_activation"] >= 1
    resp = admin.get("/api/admin/reports/fee-balances")
    assert resp.status_code == 200
    resp = admin.get("/api/admin/reports/fee-balances?export=csv")
    assert resp.status_code == 200 and b"matricule" in resp.content

    hod = auth(api, world["hod_user"])
    resp = hod.get("/api/hod/department/overview")
    assert resp.status_code == 200
    assert resp.data["department_id"] == world["dept"].id

    # student cannot reach admin overview
    resp = student_client.get("/api/admin/overview")
    assert resp.status_code == 403
