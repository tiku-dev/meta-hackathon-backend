"""Shared fixtures: a whole mini-university built through the ORM."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

PASSWORD = "DemoPass123!"


@pytest.fixture(autouse=True)
def mock_payment_provider(settings):
    """Keep the suite hermetic: never call the real Stripe/Notch Pay sandbox.

    The local backend/.env now defaults to real (test-key) providers, so
    tests explicitly pin the mock provider + simulator back on. Tests that
    assert real keys are loaded read them via a subprocess instead.
    """
    settings.PAYMENT_PROVIDER = "mock"
    settings.PAYMENT_ALLOW_MOCK = True


def make_user(email, role, **kwargs):
    from accounts.models import User, UserRole

    user = User(email=email, username=email, is_active=True, **kwargs)
    user.set_password(PASSWORD)
    user.save()
    UserRole.objects.create(user=user, role=role)
    return user


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def world(db):
    from academics.models import (
        AcademicUnit, AcademicYear, Course, CourseOffering, Level, Program,
        ProgramCourse, Semester, TimetableSession,
    )
    from accounts.models import LecturerProfile, StudentProfile
    from finance.models import FeeSchedule
    from results.models import GradeBand, GradingPolicy

    admin = make_user("admin@test.edu", "admin", is_staff=True)
    registrar = make_user("registrar@test.edu", "registrar")
    faculty = AcademicUnit.objects.create(name="Faculty of Science", unit_type="faculty")
    dept = AcademicUnit.objects.create(name="Computer Science", unit_type="department", parent=faculty)
    dept2 = AcademicUnit.objects.create(name="Mathematics", unit_type="department", parent=faculty)
    program = Program.objects.create(name="BSc Computer Science", department=dept, award="BSc", duration_years=3)
    level = Level.objects.create(program=program, label="Level 1", order=1, default_max_credits=10)
    year = AcademicYear.objects.create(label="2026/2027", start_date=date(2026, 9, 1), end_date=date(2027, 7, 31), status="active")
    now = timezone.now()
    semester = Semester.objects.create(
        academic_year=year, semester_type="first",
        start_date=date(2026, 9, 1), end_date=date(2027, 1, 15),
        registration_opens_at=now - timedelta(days=1),
        registration_deadline=now + timedelta(days=30),
        fee_deadline=now + timedelta(days=45),
        status="open", registration_open=True,
    )
    schedule = FeeSchedule.objects.create(
        program=program, academic_year=year, semester=semester, fee_type="registration",
        academic_unit=dept, currency="XAF", amount=Decimal("60000"),
        registration_threshold_type="percent", registration_threshold_value=Decimal("100"),
        is_active=True)
    policy = GradingPolicy.objects.create(
        name="Test policy", academic_year=year, ca_max=Decimal("30"), exam_max=Decimal("70"),
        pass_mark=Decimal("50"), rounding_places=1, gpa_scale=Decimal("4.0"), is_active=True)
    for lo, hi, letter, points, passed in [
        (80, 100, "A", "4.0", True), (70, "79.99", "B+", "3.5", True),
        (60, "69.99", "B", "3.0", True), (50, "59.99", "C", "2.0", True),
        (0, "49.99", "F", "0.0", False),
    ]:
        GradeBand.objects.create(policy=policy, min_score=Decimal(str(lo)),
                                 max_score=Decimal(str(hi)), letter=letter,
                                 points=Decimal(points), is_pass=passed)

    lecturer_user = make_user("lecturer@test.edu", "lecturer")
    lecturer = LecturerProfile.objects.create(
        lecturer_code="LEC-001", user=lecturer_user, first_name="Leo", last_name="Lecturer",
        email="lecturer@test.edu", department=dept, status="active")
    hod_user = make_user("hod@test.edu", "hod")
    hod = LecturerProfile.objects.create(
        lecturer_code="LEC-000", user=hod_user, first_name="Hannah", last_name="HOD",
        email="hod@test.edu", department=dept, is_hod=True, status="active")

    courses, offerings = [], []
    for i, (code, credits, weekday) in enumerate([
        ("CSC101", 4, 0), ("CSC103", 4, 1), ("MTH101", 3, 2), ("PHY101", 3, 0),
    ]):
        course = Course.objects.create(code=code, title=f"Course {code}", credits=credits, department=dept)
        ProgramCourse.objects.create(program=program, level=level, course=course, semester_type="first")
        offering = CourseOffering.objects.create(
            course=course, semester=semester, program=program, level=level,
            lecturer=lecturer, capacity=2, status="published")
        TimetableSession.objects.create(
            offering=offering, weekday=weekday, start_time="08:00", end_time="10:00",
            room=f"R{i}", campus="Main")
        courses.append(course)
        offerings.append(offering)

    student_user = make_user("student@test.edu", "student")
    student = StudentProfile.objects.create(
        user=student_user, matricule="UB26-COMP-0001", first_name="Sam", last_name="Student",
        email="student@test.edu", program=program, current_level=level,
        admission_year=year, status="active")
    prospect = StudentProfile.objects.create(
        first_name="Pat", last_name="Prospect", email="prospect@test.edu",
        program=program, current_level=level, admission_year=year, status="pending")

    return {
        "admin": admin, "registrar": registrar, "program": program, "level": level,
        "year": year, "semester": semester, "schedule": schedule, "policy": policy,
        "lecturer_user": lecturer_user, "lecturer": lecturer,
        "hod_user": hod_user, "hod": hod, "courses": courses, "offerings": offerings,
        "student_user": student_user, "student": student, "prospect": prospect,
        "dept": dept, "dept2": dept2,
    }
