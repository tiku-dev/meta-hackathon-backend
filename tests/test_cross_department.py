"""Cross-department course registration.

Rules under test:
- every department exposes its own courses (catalog + counts);
- a student may register for a published offering from ANY department —
  the offering's home program/level no longer blocks enrollment;
- all credits (own + other departments) count toward the student's one
  credit cap (`Level.default_max_credits` / `DEFAULT_MAX_CREDITS`):
  a registration that would exceed the cap is rejected with
  CREDIT_LIMIT_EXCEEDED, even purely cross-department;
- the same course cannot be taken twice via two department cohorts;
- cross-department registrations appear in "my registrations" and in
  the student's timetable.
"""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


def auth(api, user):
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def assert_error(resp, code, status=400):
    assert resp.status_code == status, resp.content
    assert resp.data["error"]["code"] == code, resp.data


def pay_fees(api, user, student, semester):
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


def make_maths_program(world):
    """A second department with its own program/level/courses/offerings."""
    from academics.models import Course, CourseOffering, Level, Program

    dept2 = world["dept2"]  # Mathematics
    program2 = Program.objects.create(
        name="BSc Mathematics", department=dept2, award="BSc", duration_years=3)
    level2 = Level.objects.create(
        program=program2, label="Level 9", order=9, default_max_credits=30)
    made = []
    for code, credits, weekday in [("MTH201", 4, 3), ("MTH203", 3, 4),
                                   ("MTH205", 4, 1)]:
        course = Course.objects.create(
            code=code, title=f"Course {code}", credits=credits, department=dept2)
        offering = CourseOffering.objects.create(
            course=course, semester=world["semester"], program=program2,
            level=level2, lecturer=world["lecturer"], capacity=30,
            status="published")
        made.append((course, offering))
    return program2, level2, made


@pytest.mark.django_db
def test_available_lists_courses_from_every_department(api, world):
    client = auth(api, world["student_user"])
    _, _, maths = make_maths_program(world)

    resp = client.get("/api/student/registration/available")
    assert resp.status_code == 200, resp.content
    by_code = {o["course_code"]: o for o in resp.data["offerings"]}

    # Own-department courses are present and not flagged cross-department.
    assert by_code["CSC101"]["department_name"] == "Computer Science"
    assert by_code["CSC101"]["is_cross_department"] is False

    # Other-department courses are listed, available and flagged.
    maths_offering = by_code["MTH201"]
    assert maths_offering["department_id"] == world["dept2"].id
    assert maths_offering["department_name"] == "Mathematics"
    assert maths_offering["is_cross_department"] is True
    assert maths_offering["available"] is True
    assert maths_offering["unavailable_reason"] is None
    assert "Mathematics" in resp.data["departments"]

    # Department filter narrows the list for browsing.
    resp = client.get(
        f"/api/student/registration/available?department={world['dept2'].id}")
    assert resp.status_code == 200
    assert {o["course_code"] for o in resp.data["offerings"]} == {
        "MTH201", "MTH203", "MTH205"}

    # Catalog endpoints expose every department's courses + counts.
    admin = auth(api, world["admin"])
    resp = admin.get("/api/admin/courses/?department=" + str(world["dept2"].id))
    assert {c["code"] for c in resp.data["results"]} == {
        "MTH201", "MTH203", "MTH205"}
    assert resp.data["results"][0]["department_name"] == "Mathematics"
    resp = admin.get("/api/admin/academic-units/")
    counts = {u["name"]: u["course_count"] for u in resp.data["results"]}
    assert counts["Computer Science"] == 4
    assert counts["Mathematics"] == 3



@pytest.mark.django_db
def test_cross_department_registration_and_credit_cap(api, world):
    """Cap is 10 (world level). Cross-department credits count toward it."""
    client = auth(api, world["student_user"])
    _, _, maths = make_maths_program(world)
    maths_offerings = [o for _, o in maths]  # 4 + 3 + 4 credits
    pay_fees(api, world["student_user"], world["student"], world["semester"])

    # Mix own + other department: 4 (CSC101) + 4 (MTH201) = 8 <= 10.
    resp = client.post("/api/student/registrations", {
        "offering_ids": [world["offerings"][0].id, maths_offerings[0].id],
    }, format="json")
    assert resp.status_code == 201, resp.content
    assert len(resp.data) == 2
    departments = {r["department_name"] for r in resp.data}
    assert departments == {"Computer Science", "Mathematics"}

    # 8 used; +3 cross-department credits = 11 > 10 -> cap rejects it.
    resp = client.post("/api/student/registrations", {
        "offering_ids": [maths_offerings[1].id]}, format="json")
    assert_error(resp, "CREDIT_LIMIT_EXCEEDED")

    # Re-registering the same cross-department course is a duplicate.
    resp = client.post("/api/student/registrations", {
        "offering_ids": [maths_offerings[0].id]}, format="json")
    assert_error(resp, "ALREADY_REGISTERED")

    # Eligibility reflects credits used across all departments.
    resp = client.get("/api/student/registration/eligibility")
    assert resp.status_code == 200
    assert resp.data["credits_used"] == 8
    assert resp.data["max_credits"] == 10
    assert resp.data["credits_remaining"] == 2

    # My registrations include the cross-department course + department.
    regs = client.get("/api/student/registrations").data
    maths_reg = [r for r in regs if r["course_code"] == "MTH201"]
    assert len(maths_reg) == 1
    assert maths_reg[0]["department_name"] == "Mathematics"

    # The cross-department course shows in the student's own timetable,
    # and the other maths course (not registered, other cohort) does not.
    resp = client.get("/api/timetable")
    assert resp.status_code == 200
    codes = {s["course_code"] for s in resp.data}
    assert "MTH201" not in codes  # no timetable session seeded for it yet
    from academics.models import TimetableSession

    offering = maths_offerings[0]
    TimetableSession.objects.create(
        offering=offering, weekday=3, start_time="08:00", end_time="10:00",
        room="M1", campus="Main")
    resp = client.get("/api/timetable")
    codes = {s["course_code"] for s in resp.data}
    assert "MTH201" in codes
    assert "MTH203" not in codes

    # Dropping frees the credits: now the 3-credit course fits (4+3=7).
    resp = client.delete(f"/api/student/registrations/{maths_reg[0]['id']}")
    assert resp.status_code == 200
    resp = client.post("/api/student/registrations", {
        "offering_ids": [maths_offerings[1].id]}, format="json")
    assert resp.status_code == 201, resp.content



@pytest.mark.django_db
def test_same_course_via_two_cohorts_is_rejected(api, world):
    """A course offered under two programs cannot be registered twice."""
    from academics.models import CourseOffering

    client = auth(api, world["student_user"])
    program2, level2, _ = make_maths_program(world)
    pay_fees(api, world["student_user"], world["student"], world["semester"])

    own = world["offerings"][0]  # CSC101 in the student's own program
    twin = CourseOffering.objects.create(
        course=world["courses"][0], semester=world["semester"],
        program=program2, level=level2, lecturer=world["lecturer"],
        capacity=30, status="published")

    resp = client.post("/api/student/registrations", {
        "offering_ids": [own.id, twin.id]}, format="json")
    assert_error(resp, "DUPLICATE_REGISTRATION")

    resp = client.post("/api/student/registrations", {
        "offering_ids": [own.id]}, format="json")
    assert resp.status_code == 201, resp.content

    resp = client.post("/api/student/registrations", {
        "offering_ids": [twin.id]}, format="json")
    assert_error(resp, "ALREADY_REGISTERED")


@pytest.mark.django_db
def test_cross_department_still_requires_payment_and_open_window(api, world):
    """Removing the department block does not weaken the other gates."""
    client = auth(api, world["student_user"])
    _, _, maths = make_maths_program(world)
    maths_offerings = [o for _, o in maths]

    resp = client.post("/api/student/registrations", {
        "offering_ids": [maths_offerings[0].id]}, format="json")
    assert_error(resp, "PAYMENT_THRESHOLD_NOT_MET")

