from django.contrib import admin

from .models import (
    AcademicUnit, AcademicYear, AttendanceRecord, Course, CourseOffering,
    CourseRegistration, Level, Program, ProgramCourse, RegistrationExemption,
    Semester, TimetableSession,
)

for model in (AcademicUnit, Program, Level, AcademicYear, Semester, Course,
              ProgramCourse, CourseOffering, RegistrationExemption,
              TimetableSession):
    admin.site.register(model)


@admin.register(CourseRegistration)
class CourseRegistrationAdmin(admin.ModelAdmin):
    list_display = ("student", "offering", "status", "registered_at")
    list_filter = ("status", "semester")


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("session", "student", "status", "marked_at")
    list_filter = ("status",)
