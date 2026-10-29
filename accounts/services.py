"""Business logic for matricules and account activation (blueprint SS5A)."""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.audit import audit
from core.exceptions import BusinessError

from .models import ActivationToken, StudentProfile, User, UserRole


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_matricule(student: StudentProfile) -> str:
    """Unique, never-reused matricule, e.g. UB26-CSC-0007."""
    year = timezone.now().year % 100
    dept_code = "GEN"
    if student.program_id and student.program and student.program.department_id:
        dept_code = (
            "".join(w for w in student.program.department.name.split())[:4].upper()
            or "GEN"
        )
    prefix = f"UB{year:02d}-{dept_code}-"
    last = (
        StudentProfile.objects.filter(matricule__startswith=prefix)
        .order_by("-matricule")
        .values_list("matricule", flat=True)
        .first()
    )
    seq = 1
    if last:
        try:
            seq = int(last.rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            seq = StudentProfile.objects.filter(
                matricule__startswith=prefix
            ).count() + 1
    candidate = f"{prefix}{seq:04d}"
    while StudentProfile.objects.filter(matricule=candidate).exists():
        seq += 1
        candidate = f"{prefix}{seq:04d}"
    return candidate


@transaction.atomic
def issue_matricule(actor, student: StudentProfile, *, send_email: bool = True) -> dict:
    """Issue (or re-issue) a matricule + fresh one-time activation token.

    The plaintext token is returned once for delivery (email / demo display);
    only its hash is persisted. Matricules are never reused, even if revoked.
    """
    if student.matricule_revoked:
        raise BusinessError(
            "This student's matricule was revoked and cannot be re-issued.",
            code="MATRICULE_REVOKED",
        )
    if not student.matricule:
        student.matricule = generate_matricule(student)
        student.matricule_issued_at = timezone.now()
    student.status = "pending"
    student.save(update_fields=["matricule", "matricule_issued_at", "status", "updated_at"])

    # Invalidate any previous unused tokens.
    student.activation_tokens.filter(used_at__isnull=True, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )
    raw = secrets.token_urlsafe(32)
    token = ActivationToken.objects.create(
        student=student,
        token_hash=_hash(raw),
        expires_at=timezone.now()
        + timedelta(hours=settings.ACTIVATION_TOKEN_TTL_HOURS),
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    audit(
        "student.matricule_issued",
        actor=actor,
        entity=student,
        summary={"matricule": student.matricule, "token_expires_at": token.expires_at.isoformat()},
    )
    link = (
        f"{settings.FRONTEND_BASE_URL}/activate"
        f"?matricule={student.matricule}&email={student.email}&token={raw}"
    )
    if send_email:
        from django.core.mail import send_mail

        send_mail(
            "Activate your student account",
            f"Your matricule is {student.matricule}.\n"
            f"Your email on record is {student.email}.\n"
            f"Activate your account within {settings.ACTIVATION_TOKEN_TTL_HOURS} hours: {link}\n"
            f"You will need your matricule, this email address and your activation code.\n"
            f"Activation code: {raw}",
            settings.DEFAULT_FROM_EMAIL,
            [student.email],
            fail_silently=True,
        )
    return {"matricule": student.matricule, "activation_token": raw,
            "activation_link": link, "expires_at": token.expires_at}

@transaction.atomic
def activate_account(activation_code: str, password: str, *, matricule: str,
                     email: str) -> StudentProfile:
    """Activate a student account with admissions-issued credentials only.

    The student supplies their matricule, email, one-time activation code
    and a new password. Every value must match the record admissions
    created: a mismatch on matricule or email raises an error, as does an
    invalid/expired/used/revoked code. Display names always come from the
    admin-entered student record; the form can never set them.
    """
    token = (
        ActivationToken.objects.select_related("student")
        .filter(token_hash=_hash((activation_code or "").strip()))
        .first()
    )
    if token is None:
        raise BusinessError("Invalid activation code.", code="ACTIVATION_TOKEN_INVALID")
    if token.used_at is not None:
        raise BusinessError("This activation code has already been used.",
                            code="ACTIVATION_TOKEN_USED")
    if token.revoked_at is not None:
        raise BusinessError("This activation code has been revoked.",
                            code="ACTIVATION_TOKEN_REVOKED")
    if token.expires_at <= timezone.now():
        raise BusinessError("This activation code has expired. Ask admissions to re-issue it.",
                            code="ACTIVATION_TOKEN_EXPIRED")
    student = token.student
    if (student.matricule or "").casefold() != (matricule or "").strip().casefold():
        raise BusinessError(
            "This activation code does not match the matricule you entered. "
            "Check both values and try again.",
            code="ACTIVATION_MATRICULE_MISMATCH")
    if (student.email or "").casefold() != (email or "").strip().casefold():
        raise BusinessError(
            "This activation code does not match the email address you entered. "
            "Use the email address admissions has on record.",
            code="ACTIVATION_EMAIL_MISMATCH")
    if student.matricule_revoked:
        raise BusinessError("This matricule has been revoked.",
                            code="MATRICULE_REVOKED")
    if student.user_id:
        raise BusinessError("This student account is already activated.",
                            code="ACCOUNT_ALREADY_ACTIVE")

    # Names and phone always come from the admin-entered record.
    user = User.objects.filter(email=student.email).first()
    if user is None:
        user = User(email=student.email, is_active=True)
    user.username = student.matricule
    user.set_password(password)
    user.first_name = student.first_name
    user.last_name = student.last_name
    user.phone = student.phone
    user.account_status = "active"
    user.save()
    UserRole.objects.get_or_create(user=user, role="student")

    student.user = user
    student.status = "active"
    student.save(update_fields=["user", "status", "updated_at"])
    token.used_at = timezone.now()
    token.save(update_fields=["used_at", "updated_at"])
    audit("student.account_activated", actor=user, entity=student,
          summary={"matricule": student.matricule, "username": user.username})
    return student


@transaction.atomic
def revoke_student(actor, student: StudentProfile, reason: str = "") -> StudentProfile:
    """Revoke a matricule/invitation. The matricule itself is never reused."""
    student.matricule_revoked = True
    student.status = "withdrawn"
    student.save(update_fields=["matricule_revoked", "status", "updated_at"])
    student.activation_tokens.filter(used_at__isnull=True, revoked_at__isnull=True).update(
        revoked_at=timezone.now())
    if student.user_id:
        student.user.is_active = False
        student.user.account_status = "inactive"
        student.user.save(update_fields=["is_active", "account_status"])
    audit("student.matricule_revoked", actor=actor, entity=student, reason=reason,
          summary={"matricule": student.matricule})
    return student
