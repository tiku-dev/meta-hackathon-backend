"""Course-first HOD marks publishing.

When the HOD wants to upload CA/exam marks, ALL courses of their
department for the chosen semester are listed (lecturer, registered
students, marks progress) and the HOD chooses which course to publish
marks for:

- the list contains only department courses of that semester;
- picking a course scopes BOTH the form and the CSV upload to it —
  CSV rows naming another course are rejected (WRONG_COURSE);
- after publishing, the list shows the course's updated progress.
"""
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


def register_direct(world, student, offering):
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


def ctx(world):
    return {"academic_year": world["year"].id, "semester": world["semester"].id}


@pytest.mark.django_db
def test_course_list_shows_all_department_courses_with_progress(api, world):
    from academics.models import Course, CourseOffering
    from academics.models import Semester

    hod = auth(api, world["hod_user"])

    # A course in another department, and one in another semester: both
    # must stay out of the HOD's list for this semester.
    maths = Course.objects.create(
        code="MTH999", title="Maths", credits=3, department=world["dept2"])
    CourseOffering.objects.create(
        course=maths, semester=world["semester"], program=world["program"],
        level=world["level"], lecturer=world["lecturer"], capacity=30,
        status="published")
    semester2 = Semester.objects.create(
        academic_year=world["year"], semester_type="second", status="open")
    CourseOffering.objects.create(
        course=world["courses"][0], semester=semester2,
        program=world["program"], level=world["level"],
        lecturer=world["lecturer"], capacity=30, status="published")

    student, user = add_student(world, "UB26-COMP-2001", "Pat", "Paid")
    pay_fees(api, user, student, world["semester"])
    register_direct(world, student, world["offerings"][0])

    resp = hod.get("/api/hod/marks-form/offerings", ctx(world))
    assert resp.status_code == 200, resp.content
    rows = resp.data["offerings"]
    assert [r["course_code"] for r in rows] == [
        c.code for c in world["courses"]]
    assert resp.data["summary"] == {
        "courses": 4, "published": 0, "in_progress": 0, "not_started": 4}
    first = rows[0]
    assert first["lecturer_name"] == "Leo Lecturer"
    assert first["registered_students"] == 1
    assert first["published_count"] == 0
    assert first["marks_status"] == "not_started"
    assert first["label"].startswith(first["course_code"])

    # Publish that student's marks via the form: progress updates.
    reg = student.registrations.get()
    resp = hod.post("/api/hod/marks-form/save", {
        **ctx(world), "offering": world["offerings"][0].id,
        "marks": [{"registration_id": reg.id,
                   "ca_mark": "24", "exam_mark": "56"}],
    }, format="json")
    assert resp.data["summary"]["saved"] == 1

    resp = hod.get("/api/hod/marks-form/offerings", ctx(world))
    first = resp.data["offerings"][0]
    assert first["published_count"] == 1
    assert first["marks_status"] == "published"
    assert resp.data["summary"]["published"] == 1

    # Non-HOD users get nothing.
    resp = auth(api, world["lecturer_user"]).get(
        "/api/hod/marks-form/offerings", ctx(world))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_csv_upload_is_scoped_to_the_chosen_course(api, world):
    from results.models import AssessmentResult

    hod = auth(api, world["hod_user"])
    offering = world["offerings"][0]
    other_offering = world["offerings"][1]

    student, user = add_student(world, "UB26-COMP-2002", "Sue", "Scoped")
    pay_fees(api, user, student, world["semester"])
    register_direct(world, student, offering)
    register_direct(world, student, other_offering)

    chosen = world["courses"][0].code
    other = world["courses"][1].code
    csv_text = (
        "matricule,course_code,ca_mark,exam_mark\n"
        f"{student.matricule},{chosen},24,56\n"
        f"{student.matricule},{other},20,40\n"
    )
    data = {
        "file": SimpleUploadedFile("marks.csv", csv_text.encode(),
                                   content_type="text/csv"),
        "academic_year": str(world["year"].id),
        "semester": str(world["semester"].id),
        "offering": str(offering.id),
    }
    resp = hod.post("/api/hod/marks-upload", data, format="multipart")
    assert resp.status_code == 200, resp.content
    assert resp.data["offering_id"] == offering.id
    assert resp.data["course_code"] == chosen
    assert resp.data["summary"]["uploaded"] == 1
    assert resp.data["summary"]["errors"] == 1
    assert resp.data["errors"][0]["code"] == "WRONG_COURSE"
    assert resp.data["errors"][0]["course_code"] == other

    # Only the chosen course got a result.
    results = list(AssessmentResult.objects.filter(
        registration__student=student))
    assert len(results) == 1
    assert results[0].registration.offering_id == offering.id
    assert results[0].status == "published"

    # A course from another department cannot be chosen at all.
    from academics.models import Course, CourseOffering

    maths = Course.objects.create(
        code="MTH999", title="Maths", credits=3, department=world["dept2"])
    maths_offering = CourseOffering.objects.create(
        course=maths, semester=world["semester"], program=world["program"],
        level=world["level"], lecturer=world["lecturer"], capacity=30,
        status="published")
    resp = hod.post("/api/hod/marks-upload", {
        **data, "offering": str(maths_offering.id),
        "file": SimpleUploadedFile("m.csv", csv_text.encode(),
                                   content_type="text/csv"),
    }, format="multipart")
    assert resp.status_code == 403
    assert resp.data["error"]["code"] == "OUT_OF_DEPARTMENT"

    # An offering from another semester is rejected too.
    from academics.models import Semester

    semester2 = Semester.objects.create(
        academic_year=world["year"], semester_type="second", status="open")
    other_sem_offering = CourseOffering.objects.create(
        course=world["courses"][0], semester=semester2,
        program=world["program"], level=world["level"],
        lecturer=world["lecturer"], capacity=30, status="published")
    resp = hod.post("/api/hod/marks-upload", {
        **data, "offering": str(other_sem_offering.id),
        "file": SimpleUploadedFile("m.csv", csv_text.encode(),
                                   content_type="text/csv"),
    }, format="multipart")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "OFFERING_NOT_FOUND"

