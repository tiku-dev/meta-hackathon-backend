"""Course-registration eligibility engine (blueprint SS4/SS5B).

Every check runs server-side inside one transaction; failures raise
``BusinessError`` with a stable code. The same checks back the
``/student/registration/available`` endpoint so the UI can explain *why*
a course is unavailable instead of guessing.
"""
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.audit import audit
from core.exceptions import BusinessError

from .models import CourseOffering, CourseRegistration, RegistrationExemption, Semester


def current_semester() -> Semester | None:
    return (
        Semester.objects.filter(status="open")
        .order_by("-registration_deadline")
        .first()
    )


def max_credits_for(student) -> int:
    level = student.current_level
    if level and level.default_max_credits:
        return level.default_max_credits
    return settings.DEFAULT_MAX_CREDITS


def active_credits(student, semester) -> int:
    total = (
        CourseRegistration.objects.filter(
            student=student, semester=semester, status="registered"
        ).aggregate(total=Sum("offering__course__credits"))["total"]
    )
    return total or 0


def _exemption(student, semester, kind) -> RegistrationExemption | None:
    return (
        RegistrationExemption.objects.filter(
            student=student, semester=semester, exemption_type=kind, is_active=True
        )
        .order_by("-created_at")
        .first()
    )


def registration_eligibility(student, semester, *, for_offering=None) -> dict:
    """Return {eligible, reasons[], ...} without raising."""
    from finance.services import payment_threshold_met, registration_fee_status

    reasons = []
    reg_fee = registration_fee_status(student)
    if reg_fee["required"]:
        reasons.append({
            "code": "REGISTRATION_FEE_REQUIRED",
            "message": "Pay your one-time registration fee before you can "
                       "pay fees and register for courses.",
            "registration_fee": reg_fee,
        })
    if student.status != "active":
        reasons.append({"code": "ACCOUNT_NOT_ACTIVE",
                        "message": "Your student account is not active."})
    if semester is None:
        reasons.append({"code": "NO_OPEN_SEMESTER",
                        "message": "No semester is currently open."})
        return {"eligible": False, "reasons": reasons}

    now = timezone.now()
    window_open = bool(
        semester.registration_open
        and semester.status == "open"
        and (semester.registration_opens_at is None or semester.registration_opens_at <= now)
        and (semester.registration_deadline is None or semester.registration_deadline >= now)
    )
    late_ok = _exemption(student, semester, "late_registration") is not None
    if not window_open and not late_ok:
        reasons.append({"code": "REGISTRATION_CLOSED",
                        "message": "Course registration is closed for this semester."})

    paid_ok, fee_info = payment_threshold_met(student, semester)
    if not paid_ok:
        reasons.append({"code": "PAYMENT_THRESHOLD_NOT_MET",
                        "message": "Your verified payments have not reached the "
                                   "required fee threshold for registration.",
                        **fee_info})

    max_credits = max_credits_for(student)
    credit_override = _exemption(student, semester, "credit_override")
    if credit_override:
        max_credits += credit_override.extra_credits
    credits_used = active_credits(student, semester)
    return {
        "eligible": not reasons,
        "reasons": reasons,
        "semester_id": semester.id,
        "registration_deadline": semester.registration_deadline,
        "fee_deadline": semester.fee_deadline,
        "max_credits": max_credits,
        "credits_used": credits_used,
        "credits_remaining": max(0, max_credits - credits_used),
        "payment": fee_info,
        "offering_id": for_offering.id if for_offering else None,
    }


def offering_unavailability(student, offering, semester) -> dict | None:
    """Why ``offering`` cannot be taken by ``student`` (or None if it can).

    Cross-department electives are allowed: an offering's ``program`` /
    ``level`` tags describe its home cohort, not a restriction. A student
    may take a published offering from ANY department; the binding limits
    are capacity, duplicate-course, timetable and the credit cap checked
    in :func:`register_courses` (credits sum across all departments).
    """
    if offering.status != "published":
        return {"code": "OFFERING_NOT_PUBLISHED",
                "message": "This course offering is not published yet."}
    if offering.semester_id != semester.id:
        return {"code": "WRONG_SEMESTER",
                "message": "This offering belongs to a different semester."}
    if CourseRegistration.objects.filter(
        student=student, semester=semester, status="registered",
        offering__course_id=offering.course_id,
    ).exists():
        return {"code": "ALREADY_REGISTERED",
                "message": "You are already registered for this course."}
    enrolled = offering.registrations.filter(status="registered").count()
    if enrolled >= offering.capacity:
        return {"code": "COURSE_FULL", "message": "This course is full."}
    # Optional prerequisite check when the program maps one.
    from .models import ProgramCourse

    mapping = ProgramCourse.objects.filter(
        program=student.program, course=offering.course
    ).exclude(prerequisite__isnull=True).first()
    if mapping and mapping.prerequisite_id:
        passed = CourseRegistration.objects.filter(
            student=student,
            offering__course_id=mapping.prerequisite_id,
            status="completed",
        ).exists()
        if not passed:
            return {"code": "PREREQUISITE_NOT_MET",
                    "message": f"Prerequisite {mapping.prerequisite.code} not completed."}
    return None


@transaction.atomic
def register_courses(actor, student, offering_ids, semester=None):
    """Atomically register a student for several offerings (blueprint SS8).

    Re-checks *every* rule inside the transaction: account active, window
    open (or recorded late exception), fee threshold, duplicates, offering
    availability/capacity, timetable conflicts and the credit cap.
    Returns the created CourseRegistration rows.
    """
    semester = semester or current_semester()
    if semester is None:
        raise BusinessError("No semester is currently open.", code="NO_OPEN_SEMESTER")

    # Lock the student row so concurrent requests cannot race the credit cap.
    from accounts.models import StudentProfile

    student = StudentProfile.objects.select_for_update().get(pk=student.pk)

    eligibility = registration_eligibility(student, semester)
    if eligibility["reasons"]:
        first = eligibility["reasons"][0]
        raise BusinessError(first["message"], code=first["code"])

    offerings = list(
        CourseOffering.objects.select_for_update()
        .select_related("course")
        .filter(id__in=offering_ids)
    )
    found = {o.id for o in offerings}
    missing = [oid for oid in offering_ids if oid not in found]
    if missing:
        raise BusinessError(f"Unknown offering ids: {missing}", code="OFFERING_NOT_FOUND")
    if len(offerings) != len(set(offering_ids)):
        raise BusinessError("Duplicate offerings in request.", code="DUPLICATE_REGISTRATION")
    course_ids = [o.course_id for o in offerings]
    if len(course_ids) != len(set(course_ids)):
        raise BusinessError(
            "The same course appears twice in this request (e.g. under "
            "two different departments/cohorts).", code="DUPLICATE_REGISTRATION")

    for offering in offerings:
        problem = offering_unavailability(student, offering, semester)
        if problem:
            raise BusinessError(problem["message"], code=problem["code"])


    # Timetable conflicts vs already-registered courses + within this batch.
    new_sessions = [(s.weekday, s.start_time, s.end_time) for s in _sessions_of(offerings)]
    existing = CourseRegistration.objects.filter(
        student=student, semester=semester, status="registered")
    existing_sessions = [
        (s.weekday, s.start_time, s.end_time)
        for s in _sessions_of([r.offering for r in existing])
    ]
    all_sessions = existing_sessions + new_sessions
    for i, a in enumerate(all_sessions):
        for b in all_sessions[i + 1:]:
            if a[0] is not None and a[0] == b[0] and a[1] < b[2] and b[1] < a[2]:
                raise BusinessError("Two of your courses clash on the timetable.",
                                    code="TIMETABLE_CONFLICT")

    new_credits = sum(o.course.credits for o in offerings)
    if eligibility["credits_used"] + new_credits > eligibility["max_credits"]:
        raise BusinessError(
            f"Registering would take you to "
            f"{eligibility['credits_used'] + new_credits} credits; the maximum is "
            f"{eligibility['max_credits']}.",
            code="CREDIT_LIMIT_EXCEEDED")

    window_open = bool(
        semester.registration_open and semester.status == "open"
        and (semester.registration_deadline is None
             or semester.registration_deadline >= timezone.now())
    )
    admin_exception = (
        _exemption(student, semester, "late_registration") is not None and not window_open
    )
    created = [
        CourseRegistration.objects.create(
            student=student, offering=offering, semester=semester,
            status="registered", admin_exception=admin_exception)
        for offering in offerings
    ]
    audit("registration.created", actor=actor, entity=student,
          summary={"offering_ids": offering_ids, "credits": new_credits,
                   "admin_exception": admin_exception})
    from communication.services import notify

    notify(student.user, "registration", "Course registration confirmed",
           f"You registered {len(created)} course(s) "
           f"({new_credits} credits) for {semester}.")
    return created


def drop_registration(actor, student, registration_id, *, force=False, reason=""):
    """Drop a course if the window allows (or an admin forces it)."""
    registration = CourseRegistration.objects.filter(
        id=registration_id, student=student, status="registered").first()
    if registration is None:
        raise BusinessError("Registration not found.", code="REGISTRATION_NOT_FOUND")
    semester = registration.semester or registration.offering.semester
    now = timezone.now()
    if not force and semester.registration_deadline and semester.registration_deadline < now:
        raise BusinessError("The add/drop deadline has passed.",
                            code="REGISTRATION_CLOSED")
    registration.status = "dropped"
    registration.save(update_fields=["status", "updated_at"])
    audit("registration.dropped", actor=actor, entity=registration,
          reason=reason, summary={"force": force})
    from communication.services import notify

    notify(student.user, "registration", "Course dropped",
           f"You dropped {registration.offering.course.code}.")
    return registration


def _sessions_of(offerings):
    from .models import TimetableSession

    ids = [o.id for o in offerings]
    if not ids:
        return []
    return list(TimetableSession.objects.filter(offering_id__in=ids, is_published=True))


def check_timetable_conflicts(offering) -> list[str]:
    """Conflicts (lecturer / room / level group) introduced by an offering."""
    from .models import TimetableSession

    conflicts = []
    others = TimetableSession.objects.filter(
        weekday__isnull=False, is_published=True
    ).exclude(offering=offering).select_related("offering")
    for session in offering.timetable_sessions.all():
        if session.weekday is None:
            continue
        for other in others:
            if other.weekday != session.weekday:
                continue
            if not (session.start_time < other.end_time
                    and other.start_time < session.end_time):
                continue
            if offering.lecturer_id and other.offering.lecturer_id == offering.lecturer_id:
                conflicts.append(
                    f"Lecturer already teaches {other.offering.course.code} at that time.")
            if session.room and other.room == session.room:
                conflicts.append(f"Room {session.room} is already booked at that time.")
            if (offering.program_id and offering.level_id
                    and other.offering.program_id == offering.program_id
                    and other.offering.level_id == offering.level_id):
                conflicts.append(
                    f"Same program/level already has {other.offering.course.code} then.")
    return sorted(set(conflicts))
