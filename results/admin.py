from django.contrib import admin

from .models import (
    AssessmentResult, GradeBand, GradingPolicy, ResultCorrection,
    TranscriptRequest,
)


class GradeBandInline(admin.TabularInline):
    model = GradeBand
    extra = 1


@admin.register(GradingPolicy)
class GradingPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "ca_max", "exam_max", "pass_mark", "is_active")
    inlines = [GradeBandInline]


@admin.register(AssessmentResult)
class AssessmentResultAdmin(admin.ModelAdmin):
    list_display = ("registration", "ca_mark", "final_mark", "total", "status")
    list_filter = ("status",)
    readonly_fields = ("total", "grade_band", "grade_points", "is_pass")


admin.site.register(ResultCorrection)
admin.site.register(TranscriptRequest)
