"""Result computation: totals, grade bands, GPA (all policy-driven)."""
from decimal import Decimal, ROUND_HALF_UP

from .models import GradingPolicy


def active_policy(student=None, semester=None) -> GradingPolicy | None:
    """Most specific active grading policy."""
    qs = GradingPolicy.objects.filter(is_active=True)
    if student is not None and student.program_id:
        found = qs.filter(program_id=student.program_id).order_by("-created_at").first()
        if found:
            return found
        if student.program and student.program.department_id:
            found = qs.filter(
                academic_unit_id=student.program.department_id
            ).order_by("-created_at").first()
            if found:
                return found
    return qs.order_by("-created_at").first()


def compute(ca, final, policy: GradingPolicy) -> dict:
    """Validate marks against the policy and compute total/grade."""
    errors = {}
    if ca is not None and not (Decimal("0") <= ca <= policy.ca_max):
        errors["ca_mark"] = f"CA must be between 0 and {policy.ca_max}."
    if final is not None and not (Decimal("0") <= final <= policy.exam_max):
        errors["final_mark"] = f"Final exam must be between 0 and {policy.exam_max}."
    if errors:
        return {"errors": errors}
    if ca is None or final is None:
        return {"total": None, "band": None, "points": None, "is_pass": None}
    quantum = Decimal(1).scaleb(-policy.rounding_places)
    total = (ca + final).quantize(quantum, rounding=ROUND_HALF_UP)
    band = policy.band_for(total)
    return {
        "total": total,
        "band": band,
        "points": band.points if band else None,
        "is_pass": band.is_pass if band else (total >= policy.pass_mark),
    }


def compute_gpa(results_with_credits, policy: GradingPolicy) -> Decimal | None:
    """Credit-weighted GPA over published results (unless policy says otherwise)."""
    rows = [(r, c) for r, c in results_with_credits
            if r.grade_points is not None and r.status in ("published", "locked")]
    if not rows:
        return None
    if policy.gpa_weighted_by_credits:
        total_points = sum(r.grade_points * c for r, c in rows)
        total_credits = sum(c for _, c in rows)
        if not total_credits:
            return None
        gpa = total_points / Decimal(total_credits)
    else:
        gpa = sum(r.grade_points for r, _ in rows) / Decimal(len(rows))
    return gpa.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def student_results(student, *, published_only=False):
    from academics.models import CourseRegistration

    qs = (
        CourseRegistration.objects.filter(student=student)
        .select_related("offering__course", "offering__semester", "result",
                        "result__grade_band")
    )
    if published_only:
        qs = qs.filter(result__status__in=("published", "locked"))
    return qs
