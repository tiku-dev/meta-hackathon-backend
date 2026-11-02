"""Student -> admin emergency contact.

Rules under test:
- a student creates an urgent contact with category/priority/subject/
  message + snapshot phone/email/location, and sees only their own;
- every admin/super-admin receives an `emergency` in-app notification,
  and urgent/high cases also send a best-effort email copy;
- only admin/super-admin may open the admin inbox; students, lecturers
  and HODs are refused, and one student can never read/mark another's;
- admin can reply (student gets a notification) and resolve/close;
- cases can never be deleted or admin-created (audit trail preserved);
- the published directory comes from EMERGENCY_CONTACT_* settings and
  hides blank entries (no invented numbers).
"""
import pytest
from django.core import mail
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

PASSWORD = "DemoPass123!"


def auth(api, user):
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def assert_error(resp, code, status=400):
    assert resp.status_code == status, resp.content
    assert resp.data["error"]["code"] == code, resp.data


def make_user(email, role):
    from accounts.models import User, UserRole

    user = User(email=email, username=email, is_active=True)
    user.set_password(PASSWORD)
    user.save()
    UserRole.objects.create(user=user, role=role)
    return user


def make_student(world, matricule):
    from accounts.models import StudentProfile

    user = make_user(f"{matricule.lower()}@test.edu", "student")
    student = StudentProfile.objects.create(
        user=user, matricule=matricule, first_name="Other", last_name="Student",
        email=f"{matricule.lower()}@test.edu", program=world["program"],
        current_level=world["level"], admission_year=world["year"],
        status="active", phone="+237699887766")
    return student, user


def send_emergency(client, **overrides):
    payload = {
        "category": "medical",
        "priority": "urgent",
        "subject": "Medical emergency at Block B",
        "message": "A student collapsed in the corridor and needs help now.",
        "location": "Block B hostel, Room 214",
    }
    payload.update(overrides)
    resp = client.post("/api/student/emergency-contacts", payload, format="json")
    assert resp.status_code == 201, resp.content
    return resp.data


@pytest.mark.django_db
def test_student_sends_emergency_and_admin_is_alerted(api, world):
    from communication.models import EmergencyContact, Notification

    client = auth(api, world["student_user"])
    created = send_emergency(client)
    assert created["status"] == "open"
    assert created["priority"] == "urgent"
    assert created["category"] == "medical"
    assert created["student_matricule"] == world["student"].matricule
    assert created["is_read_by_admin"] is False
    assert created["needs_admin_attention"] is True
    # Phone/email snapshotted from the student profile.
    assert created["contact_email"] == "student@test.edu"

    contact = EmergencyContact.objects.get(pk=created["id"])
    assert contact.student_id == world["student"].id

    # Admin got an in-app emergency notification; the student did not.
    admin_notes = Notification.objects.filter(
        user=world["admin"], notification_type="emergency")
    assert admin_notes.count() == 1
    assert "emergency" in admin_notes.first().title.lower()
    assert Notification.objects.filter(
        user=world["student_user"], notification_type="emergency").count() == 0

    # Urgent/high also sends a best-effort email copy to admins.
    assert len(mail.outbox) == 1
    assert "admin@test.edu" in mail.outbox[0].to
    assert "Emergency" in mail.outbox[0].subject

    # The student tracks it; audit recorded the creation.
    resp = client.get("/api/student/emergency-contacts")
    assert resp.status_code == 200
    assert len(resp.data["contacts"]) == 1
    from core.models import AuditLog

    assert AuditLog.objects.filter(
        action="communication.emergency_contact_created").exists()


@pytest.mark.django_db
def test_admin_inbox_reply_resolve_and_student_reads_reply(api, world):
    from communication.models import EmergencyContact, Notification

    student_client = auth(api, world["student_user"])
    created = send_emergency(student_client)
    admin = auth(api, world["admin"])

    # Summary + inbox list.
    resp = admin.get("/api/admin/emergency-contacts/summary/")
    assert resp.data == {"open": 1, "in_progress": 0, "urgent_open": 1,
                         "unread": 1, "total": 1}
    resp = admin.get("/api/admin/emergency-contacts/")
    assert resp.data["count"] == 1
    assert resp.data["results"][0]["subject"] == created["subject"]

    # Opening marks it read for admins.
    resp = admin.post(f"/api/admin/emergency-contacts/{created['id']}/mark-read/", {})
    assert resp.status_code == 200
    assert resp.data["is_read_by_admin"] is True
    resp = admin.get("/api/admin/emergency-contacts/summary/")
    assert resp.data["unread"] == 0

    # Reply: student notified, first-response stamped, status advances.
    resp = admin.patch(f"/api/admin/emergency-contacts/{created['id']}/", {
        "admin_reply": "Help is on the way — stay on the phone.",
    }, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["status"] == "in_progress"
    assert resp.data["first_response_at"]
    student_notes = Notification.objects.filter(
        user=world["student_user"], notification_type="emergency")
    assert student_notes.count() == 1
    assert "replied" in student_notes.first().title.lower()

    # Student sees an unread reply count, then marks it read.
    resp = student_client.get("/api/student/emergency-contacts")
    assert resp.data["unread_replies"] == 1
    assert resp.data["contacts"][0]["admin_reply"].startswith("Help is")
    resp = student_client.post(
        f"/api/student/emergency-contacts/{created['id']}/read", {})
    assert resp.status_code == 200
    resp = student_client.get("/api/student/emergency-contacts")
    assert resp.data["unread_replies"] == 0

    # Resolve stamps who/when.
    resp = admin.patch(f"/api/admin/emergency-contacts/{created['id']}/", {
        "status": "resolved"}, format="json")
    assert resp.status_code == 200
    assert resp.data["resolved_by"] == world["admin"].id
    assert resp.data["resolved_at"]

    # Cases cannot be deleted or admin-created.
    resp = admin.delete(f"/api/admin/emergency-contacts/{created['id']}/")
    assert_error(resp, "EMERGENCY_CONTACT_NOT_DELETABLE")
    resp = admin.post("/api/admin/emergency-contacts/", {
        "subject": "x", "message": "y"}, format="json")
    assert_error(resp, "EMERGENCY_CONTACT_STUDENT_ONLY")
    assert EmergencyContact.objects.count() == 1



@pytest.mark.django_db
def test_emergency_contacts_are_private_and_admin_only(api, world):
    owner = auth(api, world["student_user"])
    created = send_emergency(owner)

    # Another student sees nothing of it and cannot mark it read.
    other_student, other_user = make_student(world, "UB26-COMP-9101")
    other = auth(api, other_user)
    resp = other.get("/api/student/emergency-contacts")
    assert resp.data["contacts"] == []
    resp = other.post(
        f"/api/student/emergency-contacts/{created['id']}/read", {})
    assert_error(resp, "EMERGENCY_CONTACT_NOT_FOUND")

    # Non-admin roles cannot open the admin inbox or summary.
    for user in (world["student_user"], world["lecturer_user"],
                 world["hod_user"], world["registrar"]):
        client = auth(api, user)
        resp = client.get("/api/admin/emergency-contacts/")
        assert resp.status_code == 403, (user.email, resp.status_code)
        resp = client.get("/api/admin/emergency-contacts/summary/")
        assert resp.status_code == 403

    # Anonymous users are refused.
    resp = api.get("/api/student/emergency-contacts")
    assert resp.status_code == 401

    # Validation: short subject / bad priority are rejected.
    resp = owner.post("/api/student/emergency-contacts", {
        "category": "other", "priority": "urgent", "subject": "hi",
        "message": "too short"}, format="json")
    assert resp.status_code == 400
    resp = owner.post("/api/student/emergency-contacts", {
        "category": "other", "priority": "now", "subject": "ok subject",
        "message": "a long enough message here"}, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_directory_comes_from_settings_and_hides_blanks(api, world, settings):
    client = auth(api, world["student_user"])

    # Default: names exist but blank phones are hidden, no fake numbers.
    resp = client.get("/api/student/emergency-directory")
    assert resp.status_code == 200
    assert resp.data["contacts"] == []

    settings.EMERGENCY_CONTACTS = {
        "security_name": "Campus Security", "security_phone": "+237 690 000 000",
        "medical_name": "Clinic", "medical_phone": "+237 691 111 111",
        "welfare_name": "", "welfare_phone": "",
        "admin_name": "", "admin_phone": "",
    }
    settings.EMERGENCY_CONTACT_EMAIL = "emergency@example.edu"
    resp = client.get("/api/student/emergency-directory")
    labels = [c["label"] for c in resp.data["contacts"]]
    assert labels == ["Campus security", "Campus medical / clinic"]
    assert resp.data["contacts"][0]["phone"] == "+237 690 000 000"
    assert resp.data["emergency_email"] == "emergency@example.edu"

