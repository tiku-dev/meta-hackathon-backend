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
def send_activation_email(student: StudentProfile, token: str, link: str, *, fail_silently: bool = False) -> bool:
    """Send an account activation email with matricule, code, and link."""
    from django.core.mail import EmailMultiAlternatives
    from django.utils.html import escape

    if not student.email:
        return False

    hours = getattr(settings, "ACTIVATION_TOKEN_TTL_HOURS", 72)
    univ = getattr(settings, "UNIVERSITY_NAME", "University") or "University"
    subject = f"Activate your {univ} student account"

    text_content = (
        f"Hello {student.full_name},\n\n"
        f"Your student account has been created at {univ}.\n\n"
        f"Matricule: {student.matricule}\n"
        f"Activation Code: {token}\n"
        f"Activation Link: {link}\n\n"
        f"Please activate your account within {hours} hours by visiting the link above.\n"
        f"You will need your matricule, this email address, and your activation code.\n\n"
        f"Best regards,\n{univ} Admissions"
    )

    html_content = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b;">
  <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 16px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
    <div style="background: #0f172a; padding: 28px 32px; color: #ffffff;">
      <h2 style="margin: 0; font-size: 20px; font-weight: 700; color: #f8fafc;">{escape(univ)}</h2>
      <p style="margin: 4px 0 0 0; font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;">Student Portal Activation</p>
    </div>
    <div style="padding: 32px;">
      <p style="margin: 0 0 16px; font-size: 15px; line-height: 1.6;">Hello <strong>{escape(student.full_name)}</strong>,</p>
      <p style="margin: 0 0 24px; font-size: 14px; color: #475569; line-height: 1.6;">
        Your student account has been created. Use the credentials below to activate your account within <strong>{hours} hours</strong>.
      </p>

      <div style="background: #f1f5f9; border-radius: 12px; padding: 18px 20px; margin-bottom: 24px;">
        <div style="margin-bottom: 12px;">
          <span style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: #64748b; letter-spacing: 0.05em; display: block;">Matricule</span>
          <span style="font-family: monospace; font-size: 15px; font-weight: 700; color: #0f172a;">{escape(student.matricule or '—')}</span>
        </div>
        <div>
          <span style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: #64748b; letter-spacing: 0.05em; display: block;">Activation Code</span>
          <span style="font-family: monospace; font-size: 15px; font-weight: 700; color: #0f172a; word-break: break-all;">{escape(token)}</span>
        </div>
      </div>

      <div style="text-align: center; margin: 32px 0;">
        <a href="{link}" style="background: #1e293b; color: #ffffff; text-decoration: none; padding: 12px 28px; border-radius: 8px; font-size: 14px; font-weight: 600; display: inline-block;">
          Activate My Account &rarr;
        </a>
      </div>

      <p style="margin: 0; font-size: 12px; color: #94a3b8; line-height: 1.5; border-top: 1px solid #e2e8f0; padding-top: 20px;">
        If the button above does not work, copy and paste this link into your browser:<br>
        <a href="{link}" style="color: #2563eb; word-break: break-all;">{link}</a>
      </p>
    </div>
  </div>
</body>
</html>"""

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[student.email],
    )
    msg.attach_alternative(html_content, "text/html")
    msg.send(fail_silently=fail_silently)
    return True


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
        send_activation_email(student, raw, link, fail_silently=True)
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

    # New-student onboarding: if a registration fee was configured before
    # this student existed but they only activate now, they are a NEW
    # student — issue their one-time fee unless they already settled
    # tuition (grandfathered payers are never charged retroactively).
    from finance.models import Invoice
    from finance.services import (
        find_registration_fee_schedule, registration_fee_status,
    )

    schedule = find_registration_fee_schedule(student)
    if schedule is not None:
        info = registration_fee_status(student)
        already_paid_tuition = Invoice.objects.filter(
            student=student, amount_paid__gt=0).exists()
        if (not info["required"] and not info["paid"]
                and not already_paid_tuition):
            from finance.models import RegistrationFeeRecord

            record = RegistrationFeeRecord.objects.get(pk=info["record_id"])
            record.required = True
            record.is_new_student = True
            record.amount = schedule.amount
            record.currency = schedule.currency
            record.status = "unpaid"
            record.fee_schedule = schedule
            record.save(update_fields=[
                "required", "is_new_student", "amount", "currency",
                "status", "fee_schedule", "updated_at"])
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
