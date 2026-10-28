"""Seed demo data so the 5-minute demo flow (blueprint SS12) works.

Creates: demo users (admin/finance/registrar/HOD/lecturer/student),
faculty -> department -> program -> levels, academic year + semesters,
fee schedule (60000 XAF demo default, fully editable), grading policy with
sample bands, courses, offerings, timetable sessions, an announcement and
a pending student prospect with a matricule. Idempotent.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

PASSWORD = "DemoPass123!"


class Command(BaseCommand):
    help = "Seed demo data for the school management system"

    @transaction.atomic
    def handle(self, *args, **options):
        from academics.models import (
            AcademicUnit, AcademicYear, Course, CourseOffering, Level,
            Program, ProgramCourse, Semester, TimetableSession,
        )
        from accounts.models import LecturerProfile, StudentProfile, User, UserRole
        from finance.models import FeeSchedule, Invoice
        from results.models import GradeBand, GradingPolicy

        def get_user(email, role, **kwargs):
            user, created = User.objects.get_or_create(
                email=email,
                defaults={"username": email, "is_active": True, **kwargs},
            )
            if created:
                user.set_password(PASSWORD)
                user.save()
            UserRole.objects.get_or_create(user=user, role=role)
            return user

        admin = get_user("admin@demo.edu", "admin", is_staff=True,
                         first_name="Ada", last_name="Admin")
        get_user("finance@demo.edu", "finance", first_name="Fiona", last_name="Finance")
        get_user("registrar@demo.edu", "registrar", first_name="Reg", last_name="Registrar")

        faculty, _ = AcademicUnit.objects.get_or_create(
            name="Faculty of Science", defaults={"unit_type": "faculty"})
        dept, _ = AcademicUnit.objects.get_or_create(
            name="Computer Science",
            defaults={"unit_type": "department", "parent": faculty})
        program, _ = Program.objects.get_or_create(
            name="BSc Computer Science", department=dept,
            defaults={"award": "BSc", "duration_years": 3})
        levels = {}
        for order in (1, 2, 3):
            levels[order], _ = Level.objects.get_or_create(
                program=program, order=order,
                defaults={"label": f"Level {order}",
                          "default_max_credits": settings.DEFAULT_MAX_CREDITS})
        year, _ = AcademicYear.objects.get_or_create(
            label="2026/2027",
            defaults={"start_date": date(2026, 9, 1),
                      "end_date": date(2027, 7, 31), "status": "active"})
        now = timezone.now()
        sem1, _ = Semester.objects.get_or_create(
            academic_year=year, semester_type="first",
            defaults={
                "start_date": date(2026, 9, 1), "end_date": date(2027, 1, 15),
                "registration_opens_at": now - timedelta(days=1),
                "registration_deadline": now + timedelta(days=30),
                "fee_deadline": now + timedelta(days=45),
                "status": "open",
                "registration_open": True,
            })
        Semester.objects.get_or_create(
            academic_year=year, semester_type="second",
            defaults={"start_date": date(2027, 2, 1), "end_date": date(2027, 6, 30),
                      "registration_deadline": now + timedelta(days=120),
                      "fee_deadline": now + timedelta(days=135), "status": "draft"})

        # --- People -------------------------------------------------------
        hod_user = get_user("hod@demo.edu", "hod", first_name="Hannah", last_name="HOD")
        lecturer_user = get_user("lecturer@demo.edu", "lecturer",
                                 first_name="Leo", last_name="Lecturer")
        lecturer, _ = LecturerProfile.objects.get_or_create(
            lecturer_code="LEC-001",
            defaults={"user": lecturer_user, "first_name": "Leo", "last_name": "Lecturer",
                      "email": "lecturer@demo.edu", "department": dept, "status": "active"})
        hod_profile, _ = LecturerProfile.objects.get_or_create(
            lecturer_code="LEC-000",
            defaults={"user": hod_user, "first_name": "Hannah", "last_name": "HOD",
                      "email": "hod@demo.edu", "department": dept, "status": "active"})
        hod_profile.is_hod = True
        hod_profile.department = dept
        hod_profile.save(update_fields=["is_hod", "department"])

        # --- Fee schedule (60000 XAF = editable demo default) -------------
        schedule, _ = FeeSchedule.objects.get_or_create(
            program=program, academic_year=year, semester=sem1, fee_type="registration",
            defaults={"academic_unit": dept, "currency": "XAF",
                      "amount": Decimal(settings.DEFAULT_FEE_AMOUNT_XAF),
                      "registration_threshold_type": "percent",
                      "registration_threshold_value": Decimal("100"),
                      "is_active": True})

        # --- Grading policy (sample bands = demo data, not policy) --------
        policy, _ = GradingPolicy.objects.get_or_create(
            name="Demo grading policy 2026/2027", academic_year=year,
            defaults={"ca_max": Decimal("30"), "exam_max": Decimal("70"),
                      "pass_mark": Decimal("50"), "rounding_places": 1,
                      "gpa_scale": Decimal("4.0"), "is_active": True})
        for lo, hi, letter, points, passed in [
            (80, 100, "A", "4.0", True), (70, "79.99", "B+", "3.5", True),
            (60, "69.99", "B", "3.0", True), (55, "59.99", "C+", "2.5", True),
            (50, "54.99", "C", "2.0", True), (45, "49.99", "D", "1.0", False),
            (0, "44.99", "F", "0.0", False),
        ]:
            GradeBand.objects.get_or_create(
                policy=policy, letter=letter,
                defaults={"min_score": Decimal(str(lo)), "max_score": Decimal(str(hi)),
                          "points": Decimal(points), "is_pass": passed})

        # --- Courses & offerings ------------------------------------------
        course_data = [
            ("CSC201", "Data Structures", 4), ("CSC203", "Database Systems", 4),
            ("CSC205", "Operating Systems", 3), ("MTH201", "Discrete Mathematics", 3),
            ("CSC207", "Web Programming", 4), ("GST201", "Communication Skills", 2),
        ]
        offerings = []
        for code, title, credits in course_data:
            course, _ = Course.objects.get_or_create(
                code=code, defaults={"title": title, "credits": credits, "department": dept})
            ProgramCourse.objects.get_or_create(
                program=program, level=levels[2], course=course,
                defaults={"semester_type": "first", "is_required": True})
            offering, _ = CourseOffering.objects.get_or_create(
                course=course, semester=sem1, program=program, level=levels[2],
                defaults={"lecturer": lecturer, "capacity": 60, "status": "published"})
            offerings.append(offering)
        for i, offering in enumerate(offerings):
            TimetableSession.objects.get_or_create(
                offering=offering, weekday=[0, 1, 2, 3, 4, 0][i],
                start_time="08:00", end_time="10:00",
                defaults={"room": f"Room {101 + i}", "campus": "Main Campus"})

        # --- Students: one active (unpaid), one pending prospect ----------
        student_user = get_user("student@demo.edu", "student",
                                first_name="Sam", last_name="Student")
        student, _ = StudentProfile.objects.get_or_create(
            matricule="UB26-CSC-0002",
            defaults={"user": student_user, "first_name": "Sam", "last_name": "Student",
                      "email": "student@demo.edu", "program": program,
                      "current_level": levels[2], "admission_year": year, "status": "active"})
        Invoice.objects.get_or_create(
            student=student, fee_schedule=schedule, semester=sem1,
            defaults={"currency": "XAF", "total_due": schedule.amount,
                      "due_date": sem1.fee_deadline})
        prospect, created = StudentProfile.objects.get_or_create(
            email="prospect@demo.edu",
            defaults={"first_name": "Pat", "last_name": "Prospect",
                      "program": program, "current_level": levels[1],
                      "admission_year": year, "status": "pending"})
        if created:
            from accounts.services import issue_matricule

            result = issue_matricule(admin, prospect)
            self.stdout.write(f"Prospect matricule: {result['matricule']}")
            self.stdout.write(f"Activation token (demo only): {result['activation_token']}")

        from communication.models import Announcement

        Announcement.objects.get_or_create(
            title="Welcome to 2026/2027",
            defaults={"body": "Registration is open. Pay fees before the deadline.",
                      "author": admin, "audience_type": "all", "status": "published",
                      "publish_at": now - timedelta(hours=1)})
        self.stdout.write(self.style.SUCCESS(
            "Seed complete. Demo password for all users: " + PASSWORD))
