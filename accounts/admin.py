from django.contrib import admin

from .models import ActivationToken, LecturerProfile, StudentProfile, User, UserRole


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "account_status", "is_staff")
    search_fields = ("email", "first_name", "last_name")


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "unit_scope", "is_active")
    list_filter = ("role", "is_active")


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("matricule", "first_name", "last_name", "email", "status")
    list_filter = ("status", "program")
    search_fields = ("matricule", "first_name", "last_name", "email")
    readonly_fields = ("matricule", "matricule_issued_at")


@admin.register(LecturerProfile)
class LecturerProfileAdmin(admin.ModelAdmin):
    list_display = ("lecturer_code", "first_name", "last_name", "department", "is_hod")
    list_filter = ("is_hod", "department")
    search_fields = ("lecturer_code", "first_name", "last_name")


@admin.register(ActivationToken)
class ActivationTokenAdmin(admin.ModelAdmin):
    list_display = ("student", "expires_at", "used_at", "revoked_at")
    readonly_fields = [f.name for f in ActivationToken._meta.fields]
