"""HOD-owned CA/exam marks: CSV upload by matricule + fee gate.

Covers the requested behaviour:
- the HOD uploads one CSV (matricule, course_code, ca_mark, exam_mark)
  for a chosen academic year + semester;
- each row is routed to the student with that matricule and published
  straight to them;
- a student whose fees have not cleared is NOT uploaded (nothing saved)
  and is reported in ``held_fees`` until they pay and the row is
  re-uploaded;
- courses outside the HOD's department, unknown matricules, students not
  registered, duplicates and out-of-range marks are rejected per row
  without blocking the valid rows;
- non-HOD users cannot use the endpoint at all;
- the HOD can also enter marks manually for department offerings.
"""
import io
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


def auth(api, user):
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def upload(client, world, csv_text, *, dry_run=False):
    data = {
        "file": SimpleUploadedFile("marks.csv", csv_text.encode("utf-8"),
                                   content_type="text/csv"),
        "academic_year": str(world["year"].id),
        "semester": str(world["semester"].id),
    }
    if dry_run:
        data["dry_run"] = "1"
    return client.post("/api/hod/marks-upload", data, format="multipart")


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


def register_offering(api, world, student, user, offering):
    from academics.services import register_courses

    return register_courses(user, student, [offering.id], world["semester"])


def add_student(world, matricule, first, last, *, pay=True, course_idx=0, api=None):
    """Create + (optionally) pay + register an extra student."""
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
    if pay:
        pay_fees(api, user, student, world["semester"])
    register_offering(api, world, student, user, world["offerings"][course_idx])
    return student, user


@pytest.mark.django_db
def test_hod_csv_upload_publishes_paid_students_and_holds_unpaid(api, world):
    from academics.models import CourseRegistration
    from finance.services import ensure_invoice
    from results.models import AssessmentResult

    hod = auth(api, world["hod_user"])
    course = world["courses"][0]

    # World student: registered (registration created directly, e.g. an
    # admin-registered student) but their invoice is completely unpaid —
    # so the fee gate must hold their marks back.
    CourseRegistration.objects.create(
        student=world["student"], offering=world["offerings"][0],
        semester=world["semester"], status="registered")
    ensure_invoice(world["student"], world["semester"],
                   actor=world["student_user"])
    # Extra student: registered AND fully paid.
    paid_student, paid_user = add_student(
        world, "UB26-COMP-0002", "Pat", "Paid", pay=True, api=api)

    # A second *registered* course for the paid student, to exercise
    # out-of-range mark validation without tripping duplicate detection.
    register_offering(api, world, paid_student, paid_user,
                      world["offerings"][1])
    second_code = world["courses"][1].code

    csv_text = (
        "matricule,course_code,ca_mark,exam_mark\n"
        f"{world['student'].matricule},{course.code},24,56\n"
        f"{paid_student.matricule},{course.code},20,50\n"
        "UB26-COMP-9999,CSC101,10,10\n"
        f"{paid_student.matricule},{second_code},99,10\n"
    )
    resp = upload(hod, world, csv_text)
    assert resp.status_code == 200, resp.content
    assert resp.data["summary"] == {
        "total_rows": 4, "uploaded": 1, "held_fees": 1,
        "locked": 0, "errors": 2,
    }, resp.data

    # Paid student's marks were published straight to them.
    entry = resp.data["uploaded"][0]
    assert entry["matricule"] == paid_student.matricule
    assert entry["total"] == "70.0" and entry["grade_letter"] == "B+"
    assert entry["status"] == "published"
    result = AssessmentResult.objects.get(
        registration__student=paid_student)
    assert result.status == "published"
    assert result.published_by == world["hod_user"]
    assert result.registration.status == "completed"

    # Unpaid student: reported as held, and NOTHING was saved.
    held = resp.data["held_fees"][0]
    assert held["matricule"] == world["student"].matricule
    assert held["code"] == "FEES_NOT_PAID"
    assert held["balance"] == "60000.00"
    assert not AssessmentResult.objects.filter(
        registration__student=world["student"]).exists()

    # Unknown matricule + out-of-range marks rejected per row.
    errors = {e["code"] for e in resp.data["errors"]}
    assert errors == {"STUDENT_NOT_FOUND", "INVALID_MARKS"}

    # The paid student sees the grade + GPA; the unpaid one sees nothing.
    resp = auth(api, paid_user).get("/api/student/results")
    assert resp.status_code == 200
    assert resp.data["results"][0]["grade_letter"] == "B+"
    assert resp.data["gpa"] == "3.50"
    resp = auth(api, world["student_user"]).get("/api/student/results")
    assert resp.data["results"] == []

    # Once the unpaid student pays, re-uploading the same row publishes it.
    pay_fees(api, world["student_user"], world["student"], world["semester"])
    csv_text = (
        "matricule,course_code,ca_mark,exam_mark\n"
        f"{world['student'].matricule},{course.code},24,56\n"
    )
    resp = upload(hod, world, csv_text)
    assert resp.status_code == 200, resp.content
    assert resp.data["summary"]["uploaded"] == 1
    assert resp.data["summary"]["held_fees"] == 0
    resp = auth(api, world["student_user"]).get("/api/student/results")
    assert resp.data["results"][0]["grade_letter"] == "A"



@pytest.mark.django_db
def test_hod_csv_dry_run_saves_nothing_and_rerun_reports_locked(api, world):
    from results.models import AssessmentResult

    hod = auth(api, world["hod_user"])
    course = world["courses"][0]
    paid_student, _paid_user = add_student(
        world, "UB26-COMP-0003", "Dee", "Dryrun", pay=True, api=api)
    csv_text = (
        "matricule,course_code,ca_mark,exam_mark\n"
        f"{paid_student.matricule},{course.code},24,56\n"
    )

    # Dry run reports what would happen but persists nothing.
    resp = upload(hod, world, csv_text, dry_run=True)
    assert resp.status_code == 200, resp.content
    assert resp.data["dry_run"] is True
    assert resp.data["summary"]["uploaded"] == 1
    assert not AssessmentResult.objects.exists()

    # Real run publishes; a second identical run is skipped as locked.
    resp = upload(hod, world, csv_text)
    assert resp.data["summary"]["uploaded"] == 1
    resp = upload(hod, world, csv_text)
    assert resp.status_code == 200, resp.content
    assert resp.data["summary"]["uploaded"] == 0
    assert resp.data["summary"]["locked"] == 1
    resp = upload(hod, world, csv_text, dry_run=True)
    assert resp.data["summary"]["locked"] == 1


@pytest.mark.django_db
def test_hod_csv_rejects_other_department_and_unregistered(api, world):
    from academics.models import Course, CourseOffering

    hod = auth(api, world["hod_user"])
    maths_course = Course.objects.create(
        code="MTH999", title="Maths", credits=3, department=world["dept2"])
    CourseOffering.objects.create(
        course=maths_course, semester=world["semester"],
        program=world["program"], level=world["level"],
        lecturer=world["lecturer"], capacity=30, status="published")

    paid_student, _paid_user = add_student(
        world, "UB26-COMP-0004", "Ola", "Outsider", pay=True, api=api)

    csv_text = (
        "matricule,course_code,ca_mark,exam_mark\n"
        # Course belongs to Mathematics, not the HOD's department.
        f"{paid_student.matricule},MTH999,10,10\n"
        # Department course, but this student is not registered for it.
        f"{paid_student.matricule},{world['courses'][1].code},10,10\n"
        # Duplicate of the previous row.
        f"{paid_student.matricule},{world['courses'][1].code},12,12\n"
    )
    resp = upload(hod, world, csv_text)
    assert resp.status_code == 200, resp.content
    assert resp.data["summary"]["uploaded"] == 0
    codes = [e["code"] for e in resp.data["errors"]]
    assert codes == ["OUT_OF_DEPARTMENT", "NOT_REGISTERED", "DUPLICATE_ROW"], codes



@pytest.mark.django_db
def test_hod_csv_requires_hod_and_valid_file(api, world):
    # Lecturers, students and registrars cannot upload marks at all.
    csv_text = "matricule,course_code,ca_mark,exam_mark\nUB26-COMP-0001,CSC101,1,1\n"
    for user in (world["lecturer_user"], world["student_user"],
                 world["registrar"]):
        resp = upload(auth(api, user), world, csv_text)
        assert resp.status_code == 403, (user, resp.content)

    hod = auth(api, world["hod_user"])
    # Missing file.
    resp = hod.post("/api/hod/marks-upload", {
        "academic_year": str(world["year"].id),
        "semester": str(world["semester"].id),
    }, format="multipart")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "CSV_REQUIRED"

    # Semester that does not belong to the chosen year.
    resp = hod.post("/api/hod/marks-upload", {
        "file": SimpleUploadedFile("m.csv", csv_text.encode(),
                                   content_type="text/csv"),
        "academic_year": "99999",
        "semester": str(world["semester"].id),
    }, format="multipart")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "SEMESTER_NOT_IN_YEAR"

    # Missing required columns.
    resp = upload(hod, world, "matricule,course_code\nUB26-COMP-0001,CSC101\n")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "CSV_MISSING_COLUMNS"

    # Template is downloadable by the HOD only.
    resp = hod.get("/api/hod/marks-upload/template")
    assert resp.status_code == 200
    assert b"matricule,course_code,ca_mark,exam_mark" in resp.content
    resp = auth(api, world["lecturer_user"]).get(
        "/api/hod/marks-upload/template")
    assert resp.status_code == 403


@pytest.mark.django_db
def test_hod_can_enter_marks_manually_for_department_offering(api, world):
    offering = world["offerings"][0]
    pay_fees(api, world["student_user"], world["student"], world["semester"])
    register_offering(api, world, world["student"], world["student_user"],
                      offering)
    regs = list(world["student"].registrations.all())

    # The HOD (not the assigned lecturer) can save marks for a course in
    # their own department.
    hod = auth(api, world["hod_user"])
    resp = hod.put(f"/api/lecturer/offerings/{offering.id}/results", {
        "submit": True,
        "marks": [{"registration_id": regs[0].id, "ca_mark": "24",
                   "final_mark": "56"}],
    }, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["saved"][0]["status"] == "submitted"

    # A plain lecturer who does not teach the offering still cannot.
    from accounts.models import LecturerProfile, User, UserRole

    other_user = User(email="other@test.edu", username="other@test.edu",
                      is_active=True)
    other_user.set_password("DemoPass123!")
    other_user.save()
    UserRole.objects.create(user=other_user, role="lecturer")
    LecturerProfile.objects.create(
        lecturer_code="LEC-999", user=other_user, first_name="Otto",
        last_name="Other", email="other@test.edu",
        department=world["dept"], status="active")
    resp = auth(api, other_user).put(
        f"/api/lecturer/offerings/{offering.id}/results", {
            "submit": False,
            "marks": [{"registration_id": regs[0].id, "ca_mark": "1",
                       "final_mark": "1"}],
        }, format="json")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "NOT_YOUR_OFFERING"

