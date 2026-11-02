"""HOD bulk CA/exam marks upload (CSV).

The Head of Department is in charge of marks for their own department.
They pick an academic year + semester and upload one CSV where every row
carries a student's *matricule* and that student's CA and exam marks for
one course::

    matricule,course_code,ca_mark,exam_mark
    UB26-COMP-0001,CSC101,24,56

Each matricule routes the marks to exactly that student (via their course
registration in the chosen semester) and the marks are **published
straight to the student**. A student whose fees have not cleared for that
semester (the same verified-payment threshold used at course
registration) is *held back*: their marks are not saved at all and are
reported back to the HOD so the file can be re-uploaded once they pay.

Bad rows (unknown matricule, course outside the department, student not
registered, duplicate row, out-of-range marks, already-published result)
never block the good rows — they are reported per row.
"""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from academics.models import Course, CourseOffering, CourseRegistration
from accounts.models import StudentProfile
from core.audit import audit
from core.exceptions import BusinessError

from .models import AssessmentResult
from .services import active_policy, compute

MAX_CSV_ROWS = 5000

_HEADER_ALIASES = {
    "matricule": {"matricule", "studentmatricule", "matriculationnumber",
                  "matriculenumber", "regno", "registrationnumber"},
    "course_code": {"coursecode", "course", "code", "courseid"},
    "ca_mark": {"camark", "ca", "cascore", "continuousassessment",
                "continuousassessmentmark"},
    "exam_mark": {"exammark", "exam", "examscore", "finalmark", "final",
                  "finalexam", "finalexammark", "examinationmark"},
}


def _norm_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _header_map(fieldnames) -> dict:
    """Map canonical field -> actual CSV header (aliases accepted)."""
    found = {}
    for raw in fieldnames or []:
        key = _norm_header(raw)
        for canonical, aliases in _HEADER_ALIASES.items():
            if key in aliases and canonical not in found:
                found[canonical] = raw
    return found


def _parse_mark(raw, label, row_no, matricule, course_code, errors):
    """Parse one mark cell. Returns Decimal, or None after logging an error."""
    text = str(raw or "").strip()
    if text == "":
        errors.append({
            "row": row_no, "matricule": matricule, "course_code": course_code,
            "code": "INCOMPLETE_MARKS",
            "error": f"{label} is missing. Both CA and exam marks are required.",
        })
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        errors.append({
            "row": row_no, "matricule": matricule, "course_code": course_code,
            "code": "INVALID_MARK_FORMAT",
            "error": f"{label} '{text}' is not a number.",
        })
        return None


def import_hod_marks_csv(actor, *, semester, department_id, csv_text,
                         dry_run=False, offering=None):
    """Process the HOD marks CSV. Returns the upload report dict.

    ``department_id=None`` means the actor is not department-scoped
    (super admin); every course is then in scope. When ``offering`` is
    given, the upload is scoped to that chosen course: rows naming any
    other course are rejected (``WRONG_COURSE``) and registrations are
    matched within that offering.
    """
    from finance.services import payment_threshold_met

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise BusinessError(
            "The CSV file is empty. Expected a header row: "
            "matricule,course_code,ca_mark,exam_mark.", code="CSV_EMPTY")
    headers = _header_map(reader.fieldnames)
    missing = [name for name in ("matricule", "course_code", "ca_mark",
                                 "exam_mark") if name not in headers]
    if missing:
        raise BusinessError(
            "The CSV is missing required column(s): "
            + ", ".join(missing)
            + ". Expected header: matricule,course_code,ca_mark,exam_mark.",
            code="CSV_MISSING_COLUMNS")

    # Normalise rows once (blank lines are ignored).
    rows = []
    wanted = {_norm_header(headers[k]): k for k in headers}
    for row_no, raw_row in enumerate(reader, start=2):
        values = {}
        for raw_key, raw_value in (raw_row or {}).items():
            if raw_key is None:
                continue
            canonical = wanted.get(_norm_header(raw_key))
            if canonical:
                values[canonical] = (raw_value or "").strip()
        matricule = values.get("matricule", "")
        course_code = values.get("course_code", "")
        if not matricule and not course_code:
            continue
        rows.append({
            "row": row_no, "matricule": matricule,
            "course_code": course_code,
            "ca_raw": values.get("ca_mark", ""),
            "exam_raw": values.get("exam_mark", ""),
        })
    if len(rows) > MAX_CSV_ROWS:
        raise BusinessError(
            f"The CSV has {len(rows)} data rows; the limit is {MAX_CSV_ROWS} "
            "per upload. Split the file and upload it in parts.",
            code="CSV_TOO_LARGE")

    # Bulk-load everything the rows reference (no per-row queries).
    matricules = {r["matricule"].upper() for r in rows if r["matricule"]}
    course_codes = {r["course_code"].upper() for r in rows if r["course_code"]}
    students = {
        s.matricule.upper(): s
        for s in StudentProfile.objects.filter(matricule__in=matricules)
    }
    courses = {
        c.code.upper(): c
        for c in Course.objects.filter(code__in=course_codes)
        .select_related("department")
    }
    registrations = {}
    if students and courses:
        regs = (
            CourseRegistration.objects.filter(
                student__in=list(students.values()),
                offering__semester=semester,
                offering__course__code__in=course_codes,
                status__in=("registered", "completed"),
            )
            .select_related("student", "offering__course", "offering__semester")
        )
        for reg in regs:
            registrations[(reg.student_id, reg.offering.course.code.upper())] = reg
    existing_results = {
        r.registration_id: r
        for r in AssessmentResult.objects.filter(
            registration__offering__semester=semester)
    }

    uploaded, held_fees, locked, errors = [], [], [], []
    seen = set()
    fee_cache: dict[int, tuple] = {}
    policy_cache: dict[int, object] = {}
    now = timezone.now()

    for row in rows:
        row_no = row["row"]
        matricule, course_code = row["matricule"], row["course_code"]
        key = (matricule.upper(), course_code.upper())

        def fail(code, message):
            errors.append({"row": row_no, "matricule": matricule,
                           "course_code": course_code, "code": code,
                           "error": message})

        if not matricule or not course_code:
            fail("MISSING_VALUE",
                 "Every row needs both a matricule and a course_code.")
            continue
        if key in seen:
            fail("DUPLICATE_ROW",
                 "This matricule + course appears more than once in the "
                 "file; only the first row is used.")
            continue
        seen.add(key)

        ca_mark = _parse_mark(row["ca_raw"], "ca_mark", row_no, matricule,
                              course_code, errors)
        exam_mark = _parse_mark(row["exam_raw"], "exam_mark", row_no,
                                matricule, course_code, errors)
        if ca_mark is None or exam_mark is None:
            continue

        student = students.get(matricule.upper())
        if student is None:
            fail("STUDENT_NOT_FOUND",
                 "No student exists with this matricule.")
            continue
        course = courses.get(course_code.upper())
        if course is None:
            fail("COURSE_NOT_FOUND", "No course exists with this course_code.")
            continue
        if offering is not None and course.id != offering.course_id:
            fail("WRONG_COURSE",
                 f"This file is for {offering.course.code}, but the row is "
                 f"for {course.code}. Upload it from that course's page, or "
                 "fix the row.")
            continue
        if department_id is not None and course.department_id != department_id:
            fail("OUT_OF_DEPARTMENT",
                 f"{course.code} belongs to "
                 f"{course.department.name if course.department_id else 'another department'}, "
                 "not your department — only its HOD can upload its marks.")
            continue
        registration = registrations.get((student.id, course.code.upper()))
        if registration is None:
            fail("NOT_REGISTERED",
                 f"{student.full_name} is not registered for {course.code} "
                 f"in {semester}; marks can only go to registered students.")
            continue

        if student.id not in policy_cache:
            policy_cache[student.id] = active_policy(student)
        policy = policy_cache[student.id]
        if policy is None:
            fail("NO_GRADING_POLICY",
                 "No active grading policy is configured, so marks cannot "
                 "be graded. Ask an administrator to configure one.")
            continue
        outcome = compute(ca_mark, exam_mark, policy)
        if "errors" in outcome:
            fail("INVALID_MARKS",
                 "; ".join(f"{k}: {v}" for k, v in outcome["errors"].items()))
            continue

        existing = existing_results.get(registration.id)
        if existing is not None and existing.status in ("published", "locked"):
            locked.append({
                "row": row_no, "matricule": student.matricule,
                "student_name": student.full_name, "course_code": course.code,
                "code": "RESULTS_LOCKED",
                "error": "This result is already published/locked. Use an "
                         "audited correction instead of re-uploading.",
            })
            continue

        # ---- Fee gate: unpaid students' marks are NOT uploaded. ----
        if student.id not in fee_cache:
            fee_cache[student.id] = payment_threshold_met(student, semester)
        paid_ok, fee_info = fee_cache[student.id]
        if not paid_ok:
            held_fees.append({
                "row": row_no, "matricule": student.matricule,
                "student_name": student.full_name, "course_code": course.code,
                "ca_mark": str(ca_mark), "exam_mark": str(exam_mark),
                "code": "FEES_NOT_PAID",
                "error": "Marks were not uploaded because this student's "
                         "fees have not cleared for this semester. "
                         "Re-upload this row once the fees are paid.",
                "amount_paid": fee_info.get("amount_paid"),
                "required": fee_info.get("required"),
                "balance": fee_info.get("balance"),
                "currency": fee_info.get("currency"),
            })
            continue

        entry = {
            "row": row_no, "matricule": student.matricule,
            "student_name": student.full_name, "course_code": course.code,
            "ca_mark": str(ca_mark), "exam_mark": str(exam_mark),
            "total": str(outcome["total"]) if outcome["total"] is not None else None,
            "grade_letter": outcome["band"].letter if outcome["band"] else None,
            "status": "published",
        }
        if dry_run:
            uploaded.append(entry)
            continue

        try:
            with transaction.atomic():
                result, _created = AssessmentResult.objects.get_or_create(
                    registration=registration)
                if result.status in ("published", "locked"):
                    raise BusinessError(
                        "Result already published.", code="RESULTS_LOCKED")
                result.ca_mark = ca_mark
                result.final_mark = exam_mark
                result.total = outcome["total"]
                result.grade_band = outcome["band"]
                result.grade_points = outcome["points"]
                result.is_pass = outcome["is_pass"]
                result.status = "published"
                result.entered_by = actor
                result.approved_by = actor
                result.published_by = actor
                result.published_at = now
                result.save()
                if registration.status != "completed":
                    registration.status = "completed"
                    registration.save(update_fields=["status", "updated_at"])
                existing_results[registration.id] = result
            uploaded.append(entry)
            if student.user_id:
                from communication.services import notify

                notify(student.user, "result", "Result published",
                       f"Your result for {course.code} is published: "
                       f"{outcome['total']} "
                       f"({outcome['band'].letter if outcome['band'] else ''}).",
                       link="/results")
        except BusinessError:
            locked.append({
                "row": row_no, "matricule": student.matricule,
                "student_name": student.full_name, "course_code": course.code,
                "code": "RESULTS_LOCKED",
                "error": "This result is already published/locked.",
            })
        except Exception as exc:  # noqa: BLE001 – one bad row must not sink the file
            fail("SAVE_FAILED", f"Could not save this row: {exc}")

    report = {
        "dry_run": dry_run,
        "department_id": department_id,
        "semester_id": semester.id,
        "semester_label": str(semester),
        "academic_year_id": semester.academic_year_id,
        "academic_year_label": str(semester.academic_year),
        "offering_id": offering.id if offering else None,
        "course_code": offering.course.code if offering else None,
        "summary": {
            "total_rows": len(rows),
            "uploaded": len(uploaded),
            "held_fees": len(held_fees),
            "locked": len(locked),
            "errors": len(errors),
        },
        "uploaded": uploaded,
        "held_fees": held_fees,
        "locked": locked,
        "errors": errors,
    }
    audit("results.hod_csv_import", actor=actor, entity=semester,
          summary={"department_id": department_id, "dry_run": dry_run,
                   "offering_id": offering.id if offering else None,
                   **report["summary"]})
    return report


CSV_TEMPLATE = (
    "matricule,course_code,ca_mark,exam_mark\r\n"
    "UB26-COMP-0001,CSC101,24,56\r\n"
    "UB26-COMP-0002,CSC101,18,40\r\n"
)


# ---------------------------------------------------------------------------
# In-browser single-form mode
#
# The HOD picks one department course offering and gets a single form with
# every registered student (name, matricule, CA, exam). Students whose fees
# have cleared are editable and sorted first; students who have not paid are
# pinned to the bottom of the form and are completely locked: the API marks
# them not editable, and the save service refuses their marks even if a
# client tried to submit them.
# ---------------------------------------------------------------------------

def hod_offerings(semester, department_id):
    """Department offerings running in ``semester`` (course list/picker).

    Every row carries the details the HOD needs to choose which course to
    publish marks for: lecturer, how many students are registered, and
    how many of them already have published / pending marks.
    """
    from django.db.models import Count, Q

    qs = CourseOffering.objects.filter(semester=semester).select_related(
        "course", "semester__academic_year", "lecturer")
    if department_id is not None:
        qs = qs.filter(course__department_id=department_id)
    qs = qs.annotate(
        registered_count=Count(
            "registrations",
            filter=Q(registrations__status__in=("registered", "completed")),
            distinct=True),
        published_count=Count(
            "registrations__result",
            filter=Q(registrations__result__status__in=("published", "locked")),
            distinct=True),
        pending_count=Count(
            "registrations__result",
            filter=Q(registrations__result__status__in=(
                "draft", "submitted", "hod_approved")),
            distinct=True),
    )
    return qs.order_by("course__code")


def hod_offering_row(offering):
    """One row of the HOD's course list for a semester."""
    registered = getattr(offering, "registered_count", None)
    published = getattr(offering, "published_count", None)
    pending = getattr(offering, "pending_count", None)
    if registered is None:  # unannotated fallback
        registered = offering.registrations.filter(
            status__in=("registered", "completed")).count()
        published = AssessmentResult.objects.filter(
            registration__offering=offering,
            status__in=("published", "locked")).count()
        pending = AssessmentResult.objects.filter(
            registration__offering=offering,
            status__in=("draft", "submitted", "hod_approved")).count()
    if published >= registered and registered > 0:
        marks_status = "published"
    elif published > 0 or pending > 0:
        marks_status = "in_progress"
    else:
        marks_status = "not_started"
    return {
        "id": offering.id,
        "course_id": offering.course_id,
        "course_code": offering.course.code,
        "course_title": offering.course.title,
        "credits": offering.course.credits,
        "label": f"{offering.course.code} — {offering.course.title}",
        "lecturer_name": (
            offering.lecturer.full_name if offering.lecturer_id else None),
        "program_id": offering.program_id,
        "level_id": offering.level_id,
        "capacity": offering.capacity,
        "status": offering.status,
        "registered_students": registered,
        "published_count": published,
        "pending_count": pending,
        "marks_status": marks_status,
    }


def _marks_actionable(row) -> bool:
    """A roster row the HOD may actually type marks into."""
    return (
        row["fees_cleared"]
        and row["registration_status"] in ("registered", "completed")
        and row["result_status"] not in ("published", "locked")
    )


def hod_marks_roster(semester, department_id, offering):
    """Form data for one offering: policy + sorted student rows.

    Rows are sorted so every student the HOD can act on comes first
    (alphabetically), while fee-unpaid students sit at the very bottom of
    the form, locked (``can_enter_marks`` False).
    """
    from finance.services import payment_threshold_met

    if department_id is not None and (
            offering.course.department_id != department_id):
        raise BusinessError(
            "This course belongs to another department — only its HOD can "
            "enter its marks.", code="OUT_OF_DEPARTMENT",
            http_status=403)

    policy = active_policy()
    registrations = list(
        CourseRegistration.objects.filter(
            offering=offering,
            status__in=("registered", "completed", "dropped", "withdrawn"),
        )
        .select_related("student", "offering__course")
        .prefetch_related("result")
        .order_by("student__last_name", "student__first_name",
                  "student__matricule")
    )
    fee_cache: dict[int, tuple] = {}
    rows = []
    for registration in registrations:
        student = registration.student
        if student.id not in fee_cache:
            fee_cache[student.id] = payment_threshold_met(student, semester)
        paid_ok, fee_info = fee_cache[student.id]
        result = getattr(registration, "result", None)
        row = {
            "registration_id": registration.id,
            "student_id": student.id,
            "student_name": student.full_name,
            "matricule": student.matricule,
            "registration_status": registration.status,
            "fees_cleared": bool(paid_ok),
            "fee": {
                "status": "paid" if paid_ok else "unpaid",
                "amount_paid": fee_info.get("amount_paid"),
                "required": fee_info.get("required"),
                "balance": fee_info.get("balance"),
                "currency": fee_info.get("currency"),
            },
            "ca_mark": str(result.ca_mark) if result and result.ca_mark is not None else None,
            "exam_mark": str(result.final_mark) if result and result.final_mark is not None else None,
            "total": str(result.total) if result and result.total is not None else None,
            "grade_letter": (
                result.grade_band.letter
                if result and result.grade_band_id else None),
            "result_status": result.status if result else None,
        }
        row["can_enter_marks"] = _marks_actionable(row)
        rows.append(row)

    # Actionable students first, then already-published, then the rest;
    # fee-unpaid students are always pinned to the bottom of the form.
    def sort_key(r):
        if not r["fees_cleared"]:
            group = 3
        elif _marks_actionable(r):
            group = 0
        elif r["result_status"] in ("published", "locked"):
            group = 1
        else:
            group = 2
        return (group, (r["student_name"] or "").lower(), r["matricule"] or "")

    rows.sort(key=sort_key)
    return {
        "offering": {
            "id": offering.id,
            "course_code": offering.course.code,
            "course_title": offering.course.title,
            "credits": offering.course.credits,
            "semester_id": semester.id,
            "semester_label": str(semester),
            "academic_year_id": semester.academic_year_id,
            "academic_year_label": str(semester.academic_year),
        },
        "department_id": department_id,
        "policy": {
            "id": policy.id if policy else None,
            "name": policy.name if policy else None,
            "ca_max": str(policy.ca_max) if policy else None,
            "exam_max": str(policy.exam_max) if policy else None,
        },
        "summary": {
            "total_students": len(rows),
            "can_enter_marks": sum(1 for r in rows if r["can_enter_marks"]),
            "fees_not_cleared": sum(1 for r in rows if not r["fees_cleared"]),
            "already_published": sum(
                1 for r in rows
                if r["result_status"] in ("published", "locked")),
        },
        "students": rows,
    }



def save_hod_marks_form(actor, *, semester, department_id, offering, marks):
    """Save + publish marks typed into the single roster form.

    ``marks`` is a list of ``{"registration_id", "ca_mark", "exam_mark"}``.
    Mirrors the CSV rules: department-scoped, published straight to the
    student, and any student whose fees have not cleared is refused
    server-side (nothing saved) — the bottom of the form is inert.
    """
    from communication.services import notify
    from finance.services import payment_threshold_met

    if department_id is not None and (
            offering.course.department_id != department_id):
        raise BusinessError(
            "This course belongs to another department — only its HOD can "
            "enter its marks.", code="OUT_OF_DEPARTMENT",
            http_status=403)

    roster = {
        r.id: r for r in CourseRegistration.objects.filter(
            offering=offering, status__in=("registered", "completed"))
        .select_related("student", "offering__course")
    }
    saved, blocked_fees, locked, errors = [], [], [], []
    fee_cache: dict[int, tuple] = {}
    policy_cache: dict[int, object] = {}
    now = timezone.now()

    for entry in marks:
        reg_id = entry.get("registration_id")
        registration = roster.get(reg_id)
        if registration is None:
            errors.append({
                "registration_id": reg_id, "code": "NOT_ON_ROSTER",
                "error": "This student is not on this course's roster for "
                         "the selected semester.",
            })
            continue
        student = registration.student
        base = {
            "registration_id": registration.id,
            "matricule": student.matricule,
            "student_name": student.full_name,
            "course_code": offering.course.code,
        }
        ca_raw, exam_raw = entry.get("ca_mark"), entry.get("exam_mark")
        # Blank row = the HOD simply skipped this student: not an error.
        if (ca_raw is None or str(ca_raw).strip() == "") and (
                exam_raw is None or str(exam_raw).strip() == ""):
            continue

        # Fee gate re-checked server-side: an unpaid student's marks are
        # refused even if a client submits them (no action is possible).
        if student.id not in fee_cache:
            fee_cache[student.id] = payment_threshold_met(student, semester)
        paid_ok, fee_info = fee_cache[student.id]
        if not paid_ok:
            blocked_fees.append({
                **base, "code": "FEES_NOT_PAID",
                "ca_mark": None if ca_raw is None else str(ca_raw),
                "exam_mark": None if exam_raw is None else str(exam_raw),
                "error": "Marks were not saved because this student's fees "
                         "have not cleared for this semester.",
                "balance": fee_info.get("balance"),
                "currency": fee_info.get("currency"),
            })
            continue

        ca_mark = _parse_mark(ca_raw, "ca_mark", None, student.matricule,
                              offering.course.code, errors)
        exam_mark = _parse_mark(exam_raw, "exam_mark", None, student.matricule,
                                offering.course.code, errors)
        if ca_mark is None or exam_mark is None:
            continue

        if student.id not in policy_cache:
            policy_cache[student.id] = active_policy(student)
        policy = policy_cache[student.id]
        if policy is None:
            errors.append({
                **base, "code": "NO_GRADING_POLICY",
                "error": "No active grading policy is configured, so marks "
                         "cannot be graded.",
            })
            continue
        outcome = compute(ca_mark, exam_mark, policy)
        if "errors" in outcome:
            errors.append({
                **base, "code": "INVALID_MARKS",
                "error": "; ".join(
                    f"{k}: {v}" for k, v in outcome["errors"].items()),
            })
            continue


        try:
            with transaction.atomic():
                result, _created = AssessmentResult.objects.get_or_create(
                    registration=registration)
                if result.status in ("published", "locked"):
                    raise BusinessError(
                        "Result already published.", code="RESULTS_LOCKED")
                result.ca_mark = ca_mark
                result.final_mark = exam_mark
                result.total = outcome["total"]
                result.grade_band = outcome["band"]
                result.grade_points = outcome["points"]
                result.is_pass = outcome["is_pass"]
                result.status = "published"
                result.entered_by = actor
                result.approved_by = actor
                result.published_by = actor
                result.published_at = now
                result.save()
                if registration.status != "completed":
                    registration.status = "completed"
                    registration.save(update_fields=["status", "updated_at"])
            saved.append({
                **base,
                "ca_mark": str(ca_mark), "exam_mark": str(exam_mark),
                "total": str(outcome["total"]) if outcome["total"] is not None else None,
                "grade_letter": outcome["band"].letter if outcome["band"] else None,
                "status": "published",
            })
            if student.user_id:
                notify(student.user, "result", "Result published",
                       f"Your result for {offering.course.code} is published: "
                       f"{outcome['total']} "
                       f"({outcome['band'].letter if outcome['band'] else ''}).",
                       link="/results")
        except BusinessError:
            locked.append({
                **base, "code": "RESULTS_LOCKED",
                "error": "This result is already published/locked. Use an "
                         "audited correction instead.",
            })
        except Exception as exc:  # noqa: BLE001 – per-row isolation
            errors.append({**base, "code": "SAVE_FAILED",
                           "error": f"Could not save this row: {exc}"})

    report = {
        "offering_id": offering.id,
        "course_code": offering.course.code,
        "semester_id": semester.id,
        "semester_label": str(semester),
        "summary": {
            "saved": len(saved),
            "blocked_fees": len(blocked_fees),
            "locked": len(locked),
            "errors": len(errors),
        },
        "saved": saved,
        "blocked_fees": blocked_fees,
        "locked": locked,
        "errors": errors,
    }
    audit("results.hod_form_save", actor=actor, entity=offering,
          summary={"semester_id": semester.id, "department_id": department_id,
                   **report["summary"]})
    return report

