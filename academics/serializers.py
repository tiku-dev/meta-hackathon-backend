from rest_framework import serializers

from .models import (
    AcademicUnit, AcademicYear, AttendanceRecord, Course, CourseOffering,
    CourseRegistration, Level, Program, ProgramCourse, RegistrationExemption,
    Semester, TimetableSession,
)


class AcademicUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicUnit
        fields = ["id", "name", "unit_type", "parent", "code", "is_active",
                  "created_at", "updated_at"]


class ProgramSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = Program
        fields = ["id", "department", "department_name", "name", "award",
                  "duration_years", "is_active", "created_at", "updated_at"]


class LevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Level
        fields = ["id", "program", "label", "order", "default_max_credits",
                  "created_at", "updated_at"]


class AcademicYearSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicYear
        fields = ["id", "label", "start_date", "end_date", "status",
                  "created_at", "updated_at"]


class SemesterSerializer(serializers.ModelSerializer):
    academic_year_label = serializers.CharField(source="academic_year.label", read_only=True)

    class Meta:
        model = Semester
        fields = ["id", "academic_year", "academic_year_label", "semester_type",
                  "start_date", "end_date", "registration_opens_at",
                  "registration_deadline", "fee_deadline", "status",
                  "registration_open", "created_at", "updated_at"]


class CourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = ["id", "code", "title", "description", "credits", "department",
                  "is_active", "created_at", "updated_at"]


class ProgramCourseSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="course.code", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    credits = serializers.IntegerField(source="course.credits", read_only=True)

    class Meta:
        model = ProgramCourse
        fields = ["id", "program", "level", "course", "course_code",
                  "course_title", "credits", "semester_type", "is_required",
                  "prerequisite", "created_at", "updated_at"]

class CourseOfferingSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="course.code", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    credits = serializers.IntegerField(source="course.credits", read_only=True)
    lecturer_name = serializers.SerializerMethodField()
    semester_label = serializers.SerializerMethodField()
    enrolled_count = serializers.SerializerMethodField()

    class Meta:
        model = CourseOffering
        fields = ["id", "course", "course_code", "course_title", "credits",
                  "program", "level", "semester", "semester_label", "lecturer",
                  "lecturer_name", "capacity", "enrolled_count", "status",
                  "created_at", "updated_at"]

    def get_lecturer_name(self, obj):
        return obj.lecturer.full_name if obj.lecturer_id else None

    def get_semester_label(self, obj):
        return str(obj.semester) if obj.semester_id else None

    def get_enrolled_count(self, obj):
        return obj.registrations.filter(status="registered").count()


class TimetableSessionSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="offering.course.code", read_only=True)
    course_title = serializers.CharField(source="offering.course.title", read_only=True)
    offering_id = serializers.IntegerField(source="offering.id", read_only=True)
    weekday_label = serializers.SerializerMethodField()

    class Meta:
        model = TimetableSession
        fields = ["id", "offering", "offering_id", "course_code", "course_title",
                  "weekday", "weekday_label", "session_date", "start_time",
                  "end_time", "room", "campus", "is_published",
                  "created_at", "updated_at"]

    def get_weekday_label(self, obj):
        return obj.get_weekday_display() if obj.weekday is not None else None


class CourseRegistrationSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="offering.course.code", read_only=True)
    course_title = serializers.CharField(source="offering.course.title", read_only=True)
    credits = serializers.IntegerField(source="offering.course.credits", read_only=True)
    student_matricule = serializers.CharField(source="student.matricule", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)

    class Meta:
        model = CourseRegistration
        fields = ["id", "student", "student_matricule", "student_name", "offering",
                  "course_code", "course_title", "credits", "semester", "status",
                  "registered_at", "admin_exception", "created_at", "updated_at"]
        read_only_fields = ["id", "student", "semester", "registered_at",
                            "admin_exception", "created_at", "updated_at"]


class AttendanceRecordSerializer(serializers.ModelSerializer):
    student_matricule = serializers.CharField(source="student.matricule", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = ["id", "session", "student", "student_matricule", "student_name",
                  "status", "marked_by", "marked_at", "note", "created_at", "updated_at"]
        read_only_fields = ["id", "marked_by", "marked_at", "created_at", "updated_at"]


class RegistrationExemptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RegistrationExemption
        fields = ["id", "student", "semester", "exemption_type", "reason",
                  "granted_by", "extra_credits", "is_active",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "granted_by", "created_at", "updated_at"]
