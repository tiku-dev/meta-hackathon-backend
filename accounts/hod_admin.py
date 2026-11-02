"""Admin management of Heads of Department (one HOD per department).

An HOD in this system is a ``LecturerProfile`` with ``is_hod=True`` whose
``department`` is the department they head; the linked login account
carries the ``hod`` UserRole scoped to that department. These helpers
keep the three in sync whenever an admin adds or removes an HOD:

- ``assign_hod`` makes one lecturer the (single) head of a department,
  demoting any previous head of that same department.
- ``remove_hod`` clears the headship; the person stays a lecturer.
- The ``hod`` role is granted on assignment and revoked on removal —
  unless the same login still heads another department through the same
  profile (a profile heads exactly one department, so this is a guard).
"""
from django.db import transaction

from core.audit import audit
from core.exceptions import BusinessError

from .models import LecturerProfile, UserRole


def role_is_active(user, role) -> bool:
    """True when ``user`` currently holds ``role`` (fresh DB read)."""
    if user is None:
        return False
    return user.user_roles.filter(role=role, is_active=True).exists()


def profile_summary(profile):
    """Serializer-shaped dict for API responses (avoids import cycles)."""
    return {
        "id": profile.id,
        "lecturer_code": profile.lecturer_code,
        "full_name": profile.full_name,
        "first_name": profile.first_name,
        "last_name": profile.last_name,
        "email": profile.email,
        "phone": profile.phone,
        "user": profile.user_id,
        "user_email": profile.user.email if profile.user_id else None,
        "has_login": profile.user_id is not None,
        "status": profile.status,
    }


def department_hod(department_id):
    """The current head profile of a department, or None."""
    if not department_id:
        return None
    return (
        LecturerProfile.objects.filter(
            department_id=department_id, is_hod=True)
        .select_related("department", "user")
        .first()
    )


def _grant_hod_role(profile):
    if profile.user_id:
        UserRole.objects.get_or_create(
            user_id=profile.user_id, role="lecturer")
        UserRole.objects.update_or_create(
            user_id=profile.user_id, role="hod",
            unit_scope_id=profile.department_id,
            defaults={"is_active": True})


def _revoke_hod_role(profile, *, keep_if_heads_elsewhere=True):
    """Deactivate the hod role(s) of a profile's login account.

    The role is kept only when the same profile still heads a department
    (it heads exactly one, so after a removal it heads none — the check
    is a safety net for reassignment races).
    """
    if not profile.user_id:
        return
    if keep_if_heads_elsewhere and LecturerProfile.objects.filter(
            pk=profile.pk, is_hod=True).exists():
        return
    profile.user.user_roles.filter(role="hod", is_active=True).update(
        is_active=False)


def _notify(user, title, body):
    if user is None:
        return
    from communication.services import notify

    notify(user, "account", title, body, link="/hod")


def generate_lecturer_code(first_name="", last_name=""):
    """Next free lecturer code, e.g. LEC-101 (never reuses a taken code)."""
    import re

    codes = LecturerProfile.objects.values_list("lecturer_code", flat=True)
    highest = 0
    for code in codes:
        match = re.fullmatch(r"LEC-(\d+)", str(code or "").strip().upper())
        if match:
            highest = max(highest, int(match.group(1)))
    seq = highest + 1
    while LecturerProfile.objects.filter(lecturer_code=f"LEC-{seq:03d}").exists():
        seq += 1
    return f"LEC-{seq:03d}"


@transaction.atomic
def create_hod(actor, *, department, data, reason=""):
    """Create a brand-new lecturer and make them ``department``'s HOD.

    Backs the "Add new HOD" button: the profile is created (generating a
    lecturer code when none is given) and immediately assigned as the
    department's single head, replacing any previous head. Returns the
    assignment report merged with the department row fields.
    """
    first_name = str(data.get("first_name") or "").strip()
    last_name = str(data.get("last_name") or "").strip()
    if not first_name or not last_name:
        raise BusinessError(
            "First name and last name are required for the new HOD.",
            code="HOD_NAME_REQUIRED")
    lecturer_code = str(data.get("lecturer_code") or "").strip()
    if lecturer_code:
        if LecturerProfile.objects.filter(
                lecturer_code__iexact=lecturer_code).exists():
            raise BusinessError(
                f"Lecturer code {lecturer_code} is already in use. "
                "Leave it blank to generate one automatically.",
                code="LECTURER_CODE_TAKEN")
    else:
        lecturer_code = generate_lecturer_code(first_name, last_name)
    email = str(data.get("email") or "").strip()
    if email and LecturerProfile.objects.filter(email__iexact=email).exists():
        raise BusinessError(
            "A lecturer with this email already exists. Use “Assign HOD” "
            "on the department row to make that person the head instead.",
            code="LECTURER_EMAIL_TAKEN")

    profile = LecturerProfile.objects.create(
        lecturer_code=lecturer_code,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=str(data.get("phone") or "").strip(),
        department=department,
        is_hod=False,
        status="active")
    audit("lecturer.created", actor=actor, entity=profile,
          summary={"via": "add_new_hod", "department_id": department.id})
    report = assign_hod(actor, department=department, lecturer=profile,
                        reason=reason)
    return {**report, "created": True}


@transaction.atomic
def assign_hod(actor, *, department, lecturer, reason=""):
    """Make ``lecturer`` the single HOD of ``department``.

    Demotes the department's previous head (if any, and if it is a
    different person). Returns a report dict. Raises BusinessError for a
    lecturer who already heads another department or is inactive.
    """
    lecturer = (
        LecturerProfile.objects.select_for_update()
        .select_related("department", "user").get(pk=lecturer.pk))
    if lecturer.status != "active":
        raise BusinessError(
            "Only an active lecturer can be made Head of Department.",
            code="LECTURER_INACTIVE")
    if lecturer.is_hod and lecturer.department_id not in (None, department.id):
        raise BusinessError(
            f"{lecturer.full_name} already heads "
            f"{lecturer.department.name}. Remove them as HOD of that "
            "department first — a lecturer heads one department at a time.",
            code="ALREADY_HOD_ELSEWHERE")

    previous = (
        LecturerProfile.objects.select_for_update()
        .filter(department_id=department.id, is_hod=True)
        .exclude(pk=lecturer.pk)
        .select_related("user")
        .first())
    demoted = None
    if previous is not None:
        previous.is_hod = False
        previous.save(update_fields=["is_hod", "updated_at"])
        _revoke_hod_role(previous, keep_if_heads_elsewhere=False)
        audit("hod.removed", actor=actor, entity=previous, reason=reason,
              summary={"department_id": department.id,
                       "department": department.name,
                       "replaced_by": lecturer.id})
        _notify(previous.user, "Head of Department removed",
                f"You are no longer Head of {department.name}; "
                f"{lecturer.full_name} now heads the department.")
        demoted = profile_summary(previous)

    already = lecturer.is_hod and lecturer.department_id == department.id
    lecturer.department = department
    lecturer.is_hod = True
    lecturer.save(update_fields=["department", "is_hod", "updated_at"])
    _grant_hod_role(lecturer)
    audit("hod.assigned", actor=actor, entity=lecturer, reason=reason,
          summary={"department_id": department.id,
                   "department": department.name,
                   "previous_hod_id": previous.id if previous else None,
                   "already_hod": already})
    _notify(lecturer.user, "You are now Head of Department",
            f"You now head {department.name}. Department overview and "
            "marks upload are available in your portal.")
    return {
        "department_id": department.id,
        "department": department.name,
        "hod": profile_summary(lecturer),
        "role_active": role_is_active(lecturer.user, "hod"),
        "previous_hod": demoted,
        "already_hod": already,
    }



@transaction.atomic
def remove_hod(actor, *, department=None, lecturer=None, reason=""):
    """Remove the headship (the lecturer profile itself is kept).

    Accepts either the department (removes its current head) or the
    lecturer profile. Returns a report dict.
    """
    if lecturer is not None:
        profile = (
            LecturerProfile.objects.select_for_update()
            .select_related("department", "user").get(pk=lecturer.pk))
    elif department is not None:
        profile = department_hod(department.id)
        if profile is None:
            raise BusinessError(
                f"{department.name} has no Head of Department to remove.",
                code="NO_HOD")
    else:
        raise BusinessError("A department or lecturer is required.",
                            code="HOD_TARGET_REQUIRED")
    if not profile.is_hod:
        raise BusinessError(
            f"{profile.full_name} is not a Head of Department.",
            code="NOT_HOD")

    headed = profile.department
    profile.is_hod = False
    profile.save(update_fields=["is_hod", "updated_at"])
    _revoke_hod_role(profile, keep_if_heads_elsewhere=False)
    audit("hod.removed", actor=actor, entity=profile, reason=reason,
          summary={"department_id": headed.id if headed else None,
                   "department": headed.name if headed else None})
    _notify(profile.user, "Head of Department removed",
            f"You are no longer Head of "
            f"{headed.name if headed else 'your department'}. You remain "
            "a lecturer.")
    return {
        "department_id": headed.id if headed else None,
        "department": headed.name if headed else None,
        "removed_hod": profile_summary(profile),
        "role_active": role_is_active(profile.user, "hod"),
    }


def sync_hod_after_lecturer_save(actor, profile, *, was_hod, old_department_id):
    """Keep roles consistent after a lecturer create/edit.

    Called by the lecturer viewset after every save so the Admin →
    Lecturers checkbox can never leave a stale ``hod`` role behind:
    losing the headship (flag off or department moved) revokes the role;
    gaining it grants the scoped role.
    """
    is_head_now = bool(profile.is_hod and profile.department_id)
    if was_hod and not (profile.is_hod and
                        profile.department_id == old_department_id):
        # Headship lost or moved away from the old department.
        _revoke_hod_role(profile, keep_if_heads_elsewhere=True)
    if is_head_now:
        _grant_hod_role(profile)
    if bool(was_hod) != bool(profile.is_hod) or (
            profile.department_id != old_department_id):
        audit("hod.lecturer_updated", actor=actor, entity=profile,
              summary={"was_hod": was_hod,
                       "old_department_id": old_department_id,
                       "is_hod": profile.is_hod,
                       "department_id": profile.department_id})

