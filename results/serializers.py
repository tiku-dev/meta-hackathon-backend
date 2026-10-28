from rest_framework import serializers

from .models import (
    AssessmentResult, GradeBand, GradingPolicy, ResultCorrection,
    TranscriptRequest,
)


class GradeBandSerializer(serializers.ModelSerializer):
    class Meta:
        model = GradeBand
        fields = ["id", "policy", "min_score", "max_score", "letter", "points",
                  "is_pass", "label", "created_at", "updated_at"]


class GradingPolicySerializer(serializers.ModelSerializer):
    bands = GradeBandSerializer(many=True, read_only=True)

    class Meta:
        model = GradingPolicy
        fields = ["id", "name", "academic_unit", "program", "academic_year",
                  "ca_max", "exam_max", "pass_mark", "rounding_places",
                  "gpa_scale", "gpa_weighted_by_credits", "is_active", "bands",
                  "created_at", "updated_at"]


class AssessmentResultSerializer(serializers.ModelSerializer):
    course_code = serializers.CharField(source="registration.offering.course.code",
                                        read_only=True)
    course_title = serializers.CharField(source="registration.offering.course.title",
                                         read_only=True)
    credits = serializers.IntegerField(source="registration.offering.course.credits",
                                       read_only=True)
    student_matricule = serializers.CharField(
        source="registration.student.matricule", read_only=True)
    student_name = serializers.CharField(source="registration.student.full_name",
                                         read_only=True)
    grade_letter = serializers.CharField(source="grade_band.letter",
                                         read_only=True, default=None)

    class Meta:
        model = AssessmentResult
        fields = ["id", "registration", "course_code", "course_title", "credits",
                  "student_matricule", "student_name", "ca_mark", "final_mark",
                  "total", "grade_band", "grade_letter", "grade_points", "is_pass",
                  "status", "entered_by", "published_by", "published_at",
                  "change_reason", "created_at", "updated_at"]
        read_only_fields = ["id", "total", "grade_band", "grade_points", "is_pass",
                            "status", "entered_by", "published_by", "published_at",
                            "created_at", "updated_at"]


class MarkEntrySerializer(serializers.Serializer):
    registration_id = serializers.IntegerField()
    ca_mark = serializers.DecimalField(max_digits=6, decimal_places=2,
                                       required=False, allow_null=True)
    final_mark = serializers.DecimalField(max_digits=6, decimal_places=2,
                                          required=False, allow_null=True)


class BulkMarksSerializer(serializers.Serializer):
    submit = serializers.BooleanField(default=False)
    marks = MarkEntrySerializer(many=True)


class CorrectionSerializer(serializers.Serializer):
    ca_mark = serializers.DecimalField(max_digits=6, decimal_places=2,
                                       required=False, allow_null=True)
    final_mark = serializers.DecimalField(max_digits=6, decimal_places=2,
                                          required=False, allow_null=True)
    reason = serializers.CharField()


class ResultCorrectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResultCorrection
        fields = ["id", "result", "old_ca", "old_final", "old_total", "new_ca",
                  "new_final", "new_total", "reason", "changed_by", "created_at"]


class TranscriptRequestSerializer(serializers.ModelSerializer):
    student_matricule = serializers.CharField(source="student.matricule", read_only=True)
    student_name = serializers.CharField(source="student.full_name", read_only=True)

    class Meta:
        model = TranscriptRequest
        fields = ["id", "student", "student_matricule", "student_name",
                  "requested_by", "is_official", "status", "issue_number",
                  "issued_by", "issued_at", "document_path",
                  "created_at", "updated_at"]
        read_only_fields = ["id", "status", "issue_number", "issued_by",
                            "issued_at", "document_path", "created_at", "updated_at"]
