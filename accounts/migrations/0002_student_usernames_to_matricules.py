"""Students sign in with their matricule: copy it into ``User.username``.

Student accounts created before this change have ``username = email``.
Every student account that is already linked to a profile gets its
matricule as username. Staff accounts are untouched — usernames are
unique and no staff username can collide with a matricule.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    StudentProfile = apps.get_model("accounts", "StudentProfile")
    User = apps.get_model("accounts", "User")
    for student in StudentProfile.objects.select_related("user").exclude(
        user__isnull=True
    ).exclude(matricule__isnull=True).exclude(matricule=""):
        user = student.user
        if user is None or user.username == student.matricule:
            continue
        clash = (
            User.objects.filter(username=student.matricule)
            .exclude(pk=user.pk)
            .exists()
        )
        if clash:
            continue
        user.username = student.matricule
        user.save(update_fields=["username"])


def backwards(apps, schema_editor):
    StudentProfile = apps.get_model("accounts", "StudentProfile")
    for student in StudentProfile.objects.select_related("user").exclude(
        user__isnull=True
    ).exclude(matricule__isnull=True).exclude(matricule=""):
        user = student.user
        if user is not None and user.username == student.matricule:
            user.username = user.email
            user.save(update_fields=["username"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
