"""Admin → Heads of Department: assign / remove an HOD per department.

Mirrors the Students admin section, but for department heads:
- every department is listed with its current HOD (or none);
- assigning makes one lecturer the single head of a department and
  grants the scoped ``hod`` login role; changing replaces the old head;
- removing revokes the role immediately (department + marks-upload
  access close) while the person stays a lecturer;
- a lecturer heading another department cannot be double-assigned;
- unchecking "Heads department" on the Lecturers page also revokes the
  role (no stale access);
- non-admin users cannot use any of it.
"""
import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


def auth(api, user):
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def add_lecturer(world, code, first, last, *, department=None, with_user=True):
    from accounts.models import LecturerProfile, User, UserRole

    user = None
    if with_user:
        user = User(email=f"{code.lower()}@test.edu", username=f"{code.lower()}@test.edu",
                    is_active=True)
        user.set_password("DemoPass123!")
        user.save()
        UserRole.objects.create(user=user, role="lecturer")
    profile = LecturerProfile.objects.create(
        lecturer_code=code, user=user, first_name=first, last_name=last,
        email=f"{code.lower()}@test.edu",
        department=department or world["dept"], status="active")
    return profile, user


def hod_role_active(user):
    if user is None:
        return False
    return user.user_roles.filter(role="hod", is_active=True).exists()


@pytest.mark.django_db
def test_admin_assigns_changes_and_removes_hod(api, world):
    admin = auth(api, world["admin"])
    dept, dept2 = world["dept"], world["dept2"]

    # Department-first listing: CS already has Hannah, Maths has none.
    resp = admin.get("/api/admin/hods/departments")
    assert resp.status_code == 200, resp.content
    assert resp.data["summary"] == {
        "departments": 2, "with_hod": 1, "without_hod": 1}
    rows = {r["department"]: r for r in resp.data["departments"]}
    assert rows["Computer Science"]["hod"]["full_name"] == "Hannah HOD"
    assert rows["Computer Science"]["hod_role_active"] is True
    assert rows["Mathematics"]["hod"] is None

    # Assign a Maths HOD: flag + scoped role are granted.
    maths_hod, maths_user = add_lecturer(
        world, "LEC-100", "Mary", "Maths", department=dept2)
    resp = admin.post(f"/api/admin/hods/departments/{dept2.id}",
                      {"lecturer_id": maths_hod.id}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["has_hod"] is True
    assert resp.data["hod"]["full_name"] == "Mary Maths"
    assert resp.data["hod_role_active"] is True
    maths_hod.refresh_from_db()
    assert maths_hod.is_hod is True and maths_hod.department_id == dept2.id
    assert hod_role_active(maths_user) is True
    role = maths_user.user_roles.get(role="hod", is_active=True)
    assert role.unit_scope_id == dept2.id

    # The new HOD can immediately reach the department + marks upload.
    mary = auth(api, maths_user)
    assert mary.get("/api/hod/department/overview").status_code == 200
    assert mary.get("/api/hod/marks-upload/template").status_code == 200

    # Changing the CS head replaces Hannah automatically.
    new_head, new_user = add_lecturer(world, "LEC-101", "Carl", "CompSci")
    old_user = world["hod_user"]
    # Give Hannah's login a real hod role first, like a linked account.
    from accounts.models import UserRole
    UserRole.objects.update_or_create(
        user=old_user, role="hod", unit_scope=dept,
        defaults={"is_active": True})
    assert hod_role_active(old_user) is True

    resp = admin.post(f"/api/admin/hods/departments/{dept.id}",
                      {"lecturer_id": new_head.id}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["hod"]["full_name"] == "Carl CompSci"
    assert resp.data["previous_hod"]["full_name"] == "Hannah HOD"
    world["hod"].refresh_from_db()
    assert world["hod"].is_hod is False
    assert hod_role_active(old_user) is False
    assert hod_role_active(new_user) is True

    # Hannah falls back to plain lecturer access.
    hannah = auth(api, old_user)
    assert hannah.get("/api/hod/department/overview").status_code == 403
    assert hannah.get("/api/hod/marks-upload/template").status_code == 403
    assert hannah.get("/api/timetable").status_code == 200

    # Removing the Maths HOD revokes the role; she stays a lecturer.
    resp = admin.delete(f"/api/admin/hods/departments/{dept2.id}")
    assert resp.status_code == 200, resp.content
    assert resp.data["has_hod"] is False
    assert resp.data["removed_hod"]["full_name"] == "Mary Maths"
    maths_hod.refresh_from_db()
    assert maths_hod.is_hod is False
    assert hod_role_active(maths_user) is False
    assert maths_user.user_roles.filter(
        role="lecturer", is_active=True).exists()
    assert auth(api, maths_user).get(
        "/api/hod/department/overview").status_code == 403

    # Removing again: nothing to remove.
    resp = admin.delete(f"/api/admin/hods/departments/{dept2.id}")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "NO_HOD"

    # Heads-only listing reflects reality.
    resp = admin.get("/api/admin/hods")
    assert resp.data["count"] == 1
    assert resp.data["hods"][0]["full_name"] == "Carl CompSci"
    assert resp.data["hods"][0]["department"] == "Computer Science"



@pytest.mark.django_db
def test_hod_assignment_guards(api, world):
    admin = auth(api, world["admin"])
    dept, dept2 = world["dept"], world["dept2"]

    # Hannah already heads CS: she cannot also head Mathematics.
    resp = admin.post(f"/api/admin/hods/departments/{dept2.id}",
                      {"lecturer_id": world["hod"].id}, format="json")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "ALREADY_HOD_ELSEWHERE"

    # Unknown lecturer / unknown department.
    resp = admin.post(f"/api/admin/hods/departments/{dept2.id}",
                      {"lecturer_id": 999999}, format="json")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "LECTURER_NOT_FOUND"
    resp = admin.post("/api/admin/hods/departments/999999",
                      {"lecturer_id": world["lecturer"].id}, format="json")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "DEPARTMENT_NOT_FOUND"

    # Inactive lecturers cannot be made HOD.
    inactive, _user = add_lecturer(world, "LEC-102", "Ina", "Ctive")
    inactive.status = "inactive"
    inactive.save(update_fields=["status"])
    resp = admin.post(f"/api/admin/hods/departments/{dept2.id}",
                      {"lecturer_id": inactive.id}, format="json")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "LECTURER_INACTIVE"

    # The per-lecturer remove-hod action works too, and errors clearly
    # when the person is not an HOD.
    resp = admin.post(f"/api/admin/lecturers/{world['hod'].id}/remove-hod/",
                      {"reason": "Rotation"}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["removed_hod"]["full_name"] == "Hannah HOD"
    world["hod"].refresh_from_db()
    assert world["hod"].is_hod is False
    resp = admin.post(f"/api/admin/lecturers/{world['hod'].id}/remove-hod/",
                      {}, format="json")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "NOT_HOD"


@pytest.mark.django_db
def test_lecturer_edit_unchecking_hod_revokes_role(api, world):
    from accounts.models import UserRole

    admin = auth(api, world["admin"])
    hod = world["hod"]
    UserRole.objects.update_or_create(
        user=world["hod_user"], role="hod", unit_scope=world["dept"],
        defaults={"is_active": True})
    assert hod_role_active(world["hod_user"]) is True

    # Uncheck "Heads department" from the Lecturers page: role revoked.
    resp = admin.patch(f"/api/admin/lecturers/{hod.id}/",
                       {"is_hod": False}, format="json")
    assert resp.status_code == 200, resp.content
    assert hod_role_active(world["hod_user"]) is False
    assert auth(api, world["hod_user"]).get(
        "/api/hod/department/overview").status_code == 403

    # Re-checking it grants the scoped role again.
    resp = admin.patch(f"/api/admin/lecturers/{hod.id}/",
                       {"is_hod": True}, format="json")
    assert resp.status_code == 200, resp.content
    assert hod_role_active(world["hod_user"]) is True
    role = world["hod_user"].user_roles.get(role="hod", is_active=True)
    assert role.unit_scope_id == world["dept"].id


@pytest.mark.django_db
def test_add_new_hod_creates_lecturer_and_assigns(api, world):
    from accounts.models import LecturerProfile

    admin = auth(api, world["admin"])
    dept2 = world["dept2"]

    # "Add new HOD" with a generated lecturer code.
    resp = admin.post("/api/admin/hods/create", {
        "department_id": dept2.id,
        "first_name": "Nina",
        "last_name": "Newhead",
        "email": "nina@test.edu",
        "phone": "+237600000000",
    }, format="json")
    assert resp.status_code == 201, resp.content
    assert resp.data["created"] is True
    assert resp.data["has_hod"] is True
    assert resp.data["department"] == "Mathematics"
    created = resp.data["hod"]
    assert created["full_name"] == "Nina Newhead"
    assert created["lecturer_code"] == "LEC-002"
    assert created["has_login"] is False

    profile = LecturerProfile.objects.get(email="nina@test.edu")
    assert profile.is_hod is True
    assert profile.department_id == dept2.id
    assert profile.status == "active"

    # Creating for a headed department replaces the previous head.
    resp = admin.post("/api/admin/hods/create", {
        "department_id": world["dept"].id,
        "first_name": "Carl",
        "last_name": "CompSci",
        "lecturer_code": "LEC-500",
    }, format="json")
    assert resp.status_code == 201, resp.content
    assert resp.data["hod"]["lecturer_code"] == "LEC-500"
    assert resp.data["previous_hod"]["full_name"] == "Hannah HOD"
    world["hod"].refresh_from_db()
    assert world["hod"].is_hod is False

    # Validation: missing department / names / taken code / existing email.
    resp = admin.post("/api/admin/hods/create",
                      {"first_name": "A", "last_name": "B"}, format="json")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "DEPARTMENT_REQUIRED"
    resp = admin.post("/api/admin/hods/create",
                      {"department_id": dept2.id, "first_name": "Only"},
                      format="json")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "HOD_NAME_REQUIRED"
    resp = admin.post("/api/admin/hods/create", {
        "department_id": dept2.id, "first_name": "Dup", "last_name": "Code",
        "lecturer_code": "LEC-500",
    }, format="json")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "LECTURER_CODE_TAKEN"
    resp = admin.post("/api/admin/hods/create", {
        "department_id": dept2.id, "first_name": "Dup", "last_name": "Mail",
        "email": "nina@test.edu",
    }, format="json")
    assert resp.status_code == 400
    assert resp.data["error"]["code"] == "LECTURER_EMAIL_TAKEN"

    # Non-admins cannot create HODs.
    resp = auth(api, world["lecturer_user"]).post("/api/admin/hods/create", {
        "department_id": dept2.id, "first_name": "X", "last_name": "Y",
    }, format="json")
    assert resp.status_code == 403


@pytest.mark.django_db
def test_hod_admin_requires_admin(api, world):
    for user in (world["lecturer_user"], world["student_user"],
                 world["registrar"], world["hod_user"]):
        client = auth(api, user)
        assert client.get("/api/admin/hods/departments").status_code == 403
        assert client.get("/api/admin/hods").status_code == 403
        assert client.post(
            f"/api/admin/hods/departments/{world['dept'].id}",
            {"lecturer_id": world["lecturer"].id},
            format="json").status_code == 403
        assert client.delete(
            f"/api/admin/hods/departments/{world['dept'].id}"
        ).status_code == 403
        assert client.post(
            f"/api/admin/lecturers/{world['hod'].id}/remove-hod/",
            {}, format="json").status_code == 403

