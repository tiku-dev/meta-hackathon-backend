"""HOD single-form marks entry (the non-CSV mode of the marks page).

The HOD picks a year + semester + one department course and gets ONE
form with every registered student (name, matricule, CA, exam):
- students who have cleared fees are editable and come first;
- students who have NOT paid are pinned to the bottom of the form with
  ``can_enter_marks`` False — and the server refuses their marks even if
  a client submits them, so no action can be taken on them;
- saving publishes each paid student's marks straight to them;
- non-HOD users and other departments' courses are rejected.
"""
from decimal import Decimal

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


def auth(api, user):
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def pay_fees(api, user, student, semester, amount=Decimal("60000")):
    """Settle a student's invoice through the mock provider (verified)."""
    from finance.providers.base import get_provider
    from finance.services import ensure_invoice, reconcile_attempt, start_payment

    invoice = ensure_invoice(student, semester, actor=user)
    attempt = start_payment(user, invoice)
    attempt.status = "succeeded"
    attempt.save(update_fields=["status"])
    verified = get_provider("mock").verify(attempt=attempt)
    attempt.status = "pending"
    attempt.save(update_fields=["status"])
    reconcile_attempt(attempt, verified_status=verified.status,
                      amount=verified.amount, currency=verified.currency)
    return invoice


def register_direct(world, student, offering):
    """Register without the paid-up eligibility flow (admin-style)."""
    from academics.models import CourseRegistration

    return CourseRegistration.objects.create(
        student=student, offering=offering, semester=world["semester"],
        status="registered")


def add_student(world, matricule, first, last):
    from accounts.models import StudentProfile, User, UserRole

    user = User(email=f"{matricule.lower()}@test.edu", username=matricule,
                is_active=True)
    user.set_password("DemoPass123!")
    user.save()
    UserRole.objects.create(user=user, role="student")
    student = StudentProfile.objects.create(
        user=user, matricule=matricule, first_name=first, last_name=last,
        email=f"{matricule.lower()}@test.edu", program=world["program"],
        current_level=world["level"], admission_year=world["year"],
        status="active")
    return student, user


def form_context(world):
    return {"academic_year": world["year"].id, "semester": world["semester"].id}


@pytest.mark.django_db
def test_form_roster_unpaid_students_are_at_the_bottom_and_locked(api, world):
    from finance.services import ensure_invoice

    hod = auth(api, world["hod_user"])
    offering = world["offerings"][0]
    ctx = form_context(world)

    # The course picker only lists department offerings.
    resp = hod.get("/api/hod/marks-form/offerings", ctx)
    assert resp.status_code == 200, resp.content
    assert [o["id"] for o in resp.data["offerings"]] == [
        o.id for o in world["offerings"]]

    paid_student, paid_user = add_student(world, "UB26-COMP-1001", "Pat", "Paid")
    pay_fees(api, paid_user, paid_student, world["semester"])
    register_direct(world, paid_student, offering)

    unpaid_student, unpaid_user = add_student(
        world, "UB26-COMP-1002", "Uma", "Unpaid")
    register_direct(world, unpaid_student, offering)
    ensure_invoice(unpaid_student, world["semester"], actor=unpaid_user)

    resp = hod.get("/api/hod/marks-form/roster",
                   {**ctx, "offering": offering.id})
    assert resp.status_code == 200, resp.content
    assert resp.data["policy"]["ca_max"] == "30.00"
    assert resp.data["summary"] == {
        "total_students": 2, "can_enter_marks": 1,
        "fees_not_cleared": 1, "already_published": 0,
    }
    rows = resp.data["students"]
    # Paid, editable student first…
    assert rows[0]["matricule"] == paid_student.matricule
    assert rows[0]["fees_cleared"] is True
    assert rows[0]["can_enter_marks"] is True
    # …unpaid student pinned to the bottom, locked, no action possible.
    assert rows[-1]["matricule"] == unpaid_student.matricule
    assert rows[-1]["student_name"] == unpaid_student.full_name
    assert rows[-1]["fees_cleared"] is False
    assert rows[-1]["can_enter_marks"] is False
    assert rows[-1]["fee"]["status"] == "unpaid"
    assert rows[-1]["fee"]["balance"] == "60000.00"


@pytest.mark.django_db
def test_form_save_publishes_paid_and_refuses_unpaid_even_if_sent(api, world):
    from finance.services import ensure_invoice
    from results.models import AssessmentResult

    hod = auth(api, world["hod_user"])
    offering = world["offerings"][0]
    ctx = form_context(world)

    paid_student, paid_user = add_student(world, "UB26-COMP-1003", "Pat", "Paid")
    pay_fees(api, paid_user, paid_student, world["semester"])
    paid_reg = register_direct(world, paid_student, offering)

    unpaid_student, unpaid_user = add_student(
        world, "UB26-COMP-1004", "Uma", "Unpaid")
    unpaid_reg = register_direct(world, unpaid_student, offering)
    ensure_invoice(unpaid_student, world["semester"], actor=unpaid_user)

    # Even if a client submits marks for the locked bottom row, the
    # server must refuse them: no action can be taken on unpaid students.
    resp = hod.post("/api/hod/marks-form/save", {
        **ctx, "offering": offering.id,
        "marks": [
            {"registration_id": paid_reg.id, "ca_mark": "24", "exam_mark": "56"},
            {"registration_id": unpaid_reg.id, "ca_mark": "20", "exam_mark": "40"},
        ],
    }, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["summary"] == {
        "saved": 1, "blocked_fees": 1, "locked": 0, "errors": 0,
    }, resp.data
    blocked = resp.data["blocked_fees"][0]
    assert blocked["matricule"] == unpaid_student.matricule
    assert blocked["code"] == "FEES_NOT_PAID"

    result = AssessmentResult.objects.get(registration=paid_reg)
    assert result.status == "published"
    assert result.total == Decimal("80.0")
    assert result.published_by == world["hod_user"]
    paid_reg.refresh_from_db()
    assert paid_reg.status == "completed"
    assert not AssessmentResult.objects.filter(
        registration=unpaid_reg).exists()

    # The paid student sees the grade; the unpaid one sees nothing.
    resp = auth(api, paid_user).get("/api/student/results")
    assert resp.data["results"][0]["grade_letter"] == "A"
    resp = auth(api, unpaid_user).get("/api/student/results")
    assert resp.data["results"] == []

    # Reloading the form shows the paid row published (locked from
    # re-entry) and the unpaid row still at the bottom, still locked.
    resp = hod.get("/api/hod/marks-form/roster",
                   {**ctx, "offering": offering.id})
    rows = resp.data["students"]
    assert rows[0]["result_status"] == "published"
    assert rows[0]["can_enter_marks"] is False
    assert rows[-1]["matricule"] == unpaid_student.matricule
    assert rows[-1]["can_enter_marks"] is False

    # Re-saving the published row is reported as locked, never overwritten.
    resp = hod.post("/api/hod/marks-form/save", {
        **ctx, "offering": offering.id,
        "marks": [{"registration_id": paid_reg.id,
                   "ca_mark": "1", "exam_mark": "1"}],
    }, format="json")
    assert resp.data["summary"]["locked"] == 1
    result.refresh_from_db()
    assert result.total == Decimal("80.0")

    # Once the student pays, their row becomes editable and saves.
    pay_fees(api, unpaid_user, unpaid_student, world["semester"])
    resp = hod.get("/api/hod/marks-form/roster",
                   {**ctx, "offering": offering.id})
    unpaid_row = [r for r in resp.data["students"]
                  if r["matricule"] == unpaid_student.matricule][0]
    assert unpaid_row["can_enter_marks"] is True
    resp = hod.post("/api/hod/marks-form/save", {
        **ctx, "offering": offering.id,
        "marks": [{"registration_id": unpaid_reg.id,
                   "ca_mark": "20", "exam_mark": "40"}],
    }, format="json")
    assert resp.data["summary"]["saved"] == 1



@pytest.mark.django_db
def test_form_endpoints_require_hod_and_department_scope(api, world):
    from academics.models import Course, CourseOffering

    ctx = form_context(world)
    offering = world["offerings"][0]

    # Non-HOD users cannot read or save the form at all.
    for user in (world["lecturer_user"], world["student_user"],
                 world["registrar"]):
        client = auth(api, user)
        assert client.get(
            "/api/hod/marks-form/offerings", ctx).status_code == 403
        assert client.get(
            "/api/hod/marks-form/roster",
            {**ctx, "offering": offering.id}).status_code == 403
        assert client.post("/api/hod/marks-form/save", {
            **ctx, "offering": offering.id, "marks": [],
        }, format="json").status_code == 403

    hod = auth(api, world["hod_user"])

    # Missing year/semester context.
    resp = hod.get("/api/hod/marks-form/offerings")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "SEMESTER_REQUIRED"

    # A course from another department: not in the picker, and both the
    # roster and the save are refused with OUT_OF_DEPARTMENT.
    maths = Course.objects.create(
        code="MTH999", title="Maths", credits=3, department=world["dept2"])
    maths_offering = CourseOffering.objects.create(
        course=maths, semester=world["semester"], program=world["program"],
        level=world["level"], lecturer=world["lecturer"], capacity=30,
        status="published")
    resp = hod.get("/api/hod/marks-form/offerings", ctx)
    assert maths_offering.id not in [o["id"] for o in resp.data["offerings"]]
    resp = hod.get("/api/hod/marks-form/roster",
                   {**ctx, "offering": maths_offering.id})
    assert resp.status_code == 403
    assert resp.data["error"]["code"] == "OUT_OF_DEPARTMENT"
    resp = hod.post("/api/hod/marks-form/save", {
        **ctx, "offering": maths_offering.id, "marks": [],
    }, format="json")
    assert resp.status_code == 403
    assert resp.data["error"]["code"] == "OUT_OF_DEPARTMENT"


@pytest.mark.django_db
def test_form_save_validates_marks_and_ignores_blank_rows(api, world):
    from results.models import AssessmentResult

    hod = auth(api, world["hod_user"])
    offering = world["offerings"][0]
    ctx = form_context(world)

    student, user = add_student(world, "UB26-COMP-1005", "Val", "Valid")
    pay_fees(api, user, student, world["semester"])
    reg = register_direct(world, student, offering)
    other, other_user = add_student(world, "UB26-COMP-1006", "Skip", "Me")
    pay_fees(api, other_user, other, world["semester"])
    other_reg = register_direct(world, other, offering)

    resp = hod.post("/api/hod/marks-form/save", {
        **ctx, "offering": offering.id,
        "marks": [
            # Out of range for the active policy (CA max 30).
            {"registration_id": reg.id, "ca_mark": "31", "exam_mark": "10"},
            # Only one of the two marks: incomplete.
            {"registration_id": reg.id, "ca_mark": "20", "exam_mark": None},
            # Blank row: skipped silently, not an error.
            {"registration_id": other_reg.id, "ca_mark": None, "exam_mark": None},
            # Not on this roster at all.
            {"registration_id": 999999, "ca_mark": "1", "exam_mark": "1"},
        ],
    }, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["summary"]["saved"] == 0
    codes = [e["code"] for e in resp.data["errors"]]
    assert codes == ["INVALID_MARKS", "INCOMPLETE_MARKS", "NOT_ON_ROSTER"], codes
    assert not AssessmentResult.objects.exists()

    # A valid save then works.
    resp = hod.post("/api/hod/marks-form/save", {
        **ctx, "offering": offering.id,
        "marks": [{"registration_id": reg.id,
                   "ca_mark": "25", "exam_mark": "55"}],
    }, format="json")
    assert resp.data["summary"]["saved"] == 1
    assert resp.data["saved"][0]["total"] == "80.0"
    assert resp.data["saved"][0]["grade_letter"] == "A"

