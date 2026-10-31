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

from academics.models import Course, CourseRegistration
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
                         dry_run=False):
    """Process the HOD marks CSV. Returns the upload report dict.

    ``department_id=None`` means the actor is not department-scoped
    (super admin); every course is then in scope.
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
                   **report["summary"]})
    return report


CSV_TEMPLATE = (
    "matricule,course_code,ca_mark,exam_mark\r\n"
    "UB26-COMP-0001,CSC101,24,56\r\n"
    "UB26-COMP-0002,CSC101,18,40\r\n"
)

