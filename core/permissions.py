"""Role-based DRF permissions (blueprint SS3).

Roles live in ``accounts.UserRole``. A user may hold several roles; the
``roles`` property on the user model returns the set of active role names.
"""
from rest_framework.permissions import BasePermission

ADMIN = "admin"
SUPER_ADMIN = "super_admin"
ADMISSIONS = "admissions"
FINANCE = "finance"
REGISTRAR = "registrar"
HOD = "hod"
COORDINATOR = "coordinator"
LECTURER = "lecturer"
STUDENT = "student"

ALL_ROLES = [
    ADMIN,
    SUPER_ADMIN,
    ADMISSIONS,
    FINANCE,
    REGISTRAR,
    HOD,
    COORDINATOR,
    LECTURER,
    STUDENT,
]


def user_roles(user) -> set[str]:
    if not user or not user.is_authenticated:
        return set()
    roles = set(getattr(user, "roles", set()) or set())
    if getattr(user, "is_superuser", False):
        roles.add(SUPER_ADMIN)
    return roles


def has_role(user, *roles: str) -> bool:
    have = user_roles(user)
    return SUPER_ADMIN in have or bool(have.intersection(roles))


class RolePermission(BasePermission):
    """Allow if the user holds any of ``allowed_roles`` (superusers always).

    Supports DRF-style composition with ``|`` between permission *classes*
    (``IsA | IsB`` in ``permission_classes`` and instance composition too.
    """

    allowed_roles: tuple[str, ...] = ()

    def has_permission(self, request, view):
        return has_role(request.user, *self.allowed_roles)

    def __or__(self, other):
        parent = self

        class _Or(BasePermission):
            def has_permission(self, request, view):
                return (parent.has_permission(request, view)
                        or other.has_permission(request, view))

            def has_object_permission(self, request, view, obj):
                return (parent.has_object_permission(request, view, obj)
                        or other.has_object_permission(request, view, obj))

        return _Or()

    def __and__(self, other):
        parent = self

        class _And(BasePermission):
            def has_permission(self, request, view):
                return (parent.has_permission(request, view)
                        and other.has_permission(request, view))

            def has_object_permission(self, request, view, obj):
                return (parent.has_object_permission(request, view, obj)
                        and other.has_object_permission(request, view, obj))

        return _And()


class IsAdmin(RolePermission):
    allowed_roles = (ADMIN,)


class IsAdmissions(RolePermission):
    allowed_roles = (ADMIN, ADMISSIONS)


class IsFinance(RolePermission):
    allowed_roles = (ADMIN, FINANCE)


class IsRegistrar(RolePermission):
    allowed_roles = (ADMIN, REGISTRAR)


class IsAcademicsAdmin(RolePermission):
    """Anyone who may manage academic structure (admin/registrar/coordinator)."""

    allowed_roles = (ADMIN, REGISTRAR, COORDINATOR)


class IsHOD(RolePermission):
    allowed_roles = (ADMIN, HOD)


class IsLecturer(RolePermission):
    allowed_roles = (ADMIN, LECTURER)


class IsStudent(RolePermission):
    allowed_roles = (STUDENT,)
