"""Admin reports + audit-log API (blueprint SS8/SS11.11)."""
import csv

from django.db.models import Count, Sum
from django.http import HttpResponse
from rest_framework import serializers, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import BusinessError
from core.permissions import IsAdmin, has_role

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.CharField(source="actor.email", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = ["id", "actor", "actor_email", "actor_ip", "request_id", "action",
                  "entity_type", "entity_id", "reason", "summary", "created_at"]


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.select_related("actor").all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAdmin]
    filterset_fields = ["action", "entity_type", "entity_id", "actor"]
    search_fields = ["action", "reason"]


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


class ReportView(APIView):
    """GET /api/admin/reports/:report – JSON or ?format=csv export."""

    permission_classes = [IsAuthenticated]

    REPORTS = ("students", "fee-balances", "payments", "registrations", "results")

    def get(self, request, report):
        if report not in self.REPORTS:
            raise BusinessError(f"Unknown report '{report}'.", code="REPORT_NOT_FOUND")
        if not has_role(request.user, "admin", "finance", "registrar"):
            raise BusinessError("You may not view reports.", code="FORBIDDEN")
        handler = getattr(self, f"_report_{report.replace('-', '_')}")
        return handler(request)

    def _report_students(self, request):
        from accounts.models import StudentProfile

        qs = StudentProfile.objects.select_related("program", "current_level")
        if request.query_params.get("export") == "csv":
            return _csv_response(
                "students.csv",
                ["matricule", "name", "email", "program", "level", "status"],
                [(s.matricule, s.full_name, s.email,
                  s.program.name if s.program_id else "",
                  s.current_level.label if s.current_level_id else "", s.status)
                 for s in qs])
        by_status = list(qs.values("status").annotate(count=Count("id")))
        return Response({"total": qs.count(), "by_status": by_status})

    def _report_fee_balances(self, request):
        from finance.models import Invoice

        qs = Invoice.objects.select_related("student")
        if request.query_params.get("export") == "csv":
            return _csv_response(
                "fee-balances.csv",
                ["matricule", "name", "total_due", "amount_paid", "balance",
                 "currency", "status"],
                [(i.student.matricule, i.student.full_name, i.total_due,
                  i.amount_paid, i.balance, i.currency, i.status) for i in qs])
        totals = qs.aggregate(total_due=Sum("total_due"), total_paid=Sum("amount_paid"))
        return Response({
            "invoices": qs.count(),
            "total_due": str(totals["total_due"] or 0),
            "total_paid": str(totals["total_paid"] or 0),
            "outstanding": qs.exclude(status__in=("paid", "waived", "cancelled")).count(),
        })

    def _report_payments(self, request):
        from finance.models import PaymentAttempt

        qs = PaymentAttempt.objects.all()
        if request.query_params.get("export") == "csv":
            return _csv_response(
                "payments.csv",
                ["reference", "provider", "amount", "currency", "status", "created_at"],
                [(p.reference, p.provider, p.amount, p.currency, p.status,
                  p.created_at.isoformat()) for p in qs])
        by_status = list(qs.values("status").annotate(count=Count("id"),
                                                      amount=Sum("amount")))
        return Response({"total": qs.count(), "by_status": by_status})

    def _report_registrations(self, request):
        from academics.models import CourseRegistration

        qs = CourseRegistration.objects.select_related(
            "student", "offering__course")
        if request.query_params.get("export") == "csv":
            return _csv_response(
                "registrations.csv",
                ["matricule", "name", "course", "credits", "status", "registered_at"],
                [(r.student.matricule, r.student.full_name,
                  r.offering.course.code, r.offering.course.credits, r.status,
                  r.registered_at.isoformat()) for r in qs])
        by_course = list(
            qs.filter(status="registered")
            .values("offering__course__code")
            .annotate(count=Count("id")).order_by("-count")[:50])
        return Response({"total": qs.count(),
                         "active": qs.filter(status="registered").count(),
                         "by_course": by_course})

    def _report_results(self, request):
        from results.models import AssessmentResult

        qs = AssessmentResult.objects.select_related(
            "registration__student", "registration__offering__course", "grade_band")
        if request.query_params.get("export") == "csv":
            return _csv_response(
                "results.csv",
                ["matricule", "course", "ca", "final", "total", "grade", "status"],
                [(r.registration.student.matricule,
                  r.registration.offering.course.code, r.ca_mark, r.final_mark,
                  r.total, r.grade_band.letter if r.grade_band_id else "",
                  r.status) for r in qs])
        by_status = list(qs.values("status").annotate(count=Count("id")))
        published = qs.filter(status__in=("published", "locked"))
        passed = published.filter(is_pass=True).count()
        return Response({
            "total": qs.count(), "by_status": by_status,
            "published": published.count(), "passed": passed,
            "pass_rate": round(100 * passed / published.count(), 1)
            if published.count() else None,
        })


class AdminOverviewView(APIView):
    """GET /api/admin/overview – dashboard counters (blueprint SS7)."""

    permission_classes = [IsAdmin]

    def get(self, request):
        from accounts.models import StudentProfile
        from academics.models import CourseRegistration, Semester
        from finance.models import Invoice, PaymentAttempt
        from results.models import AssessmentResult

        semester = Semester.objects.filter(status="open").first()
        return Response({
            "students_pending_activation": StudentProfile.objects.filter(
                status="pending").count(),
            "students_active": StudentProfile.objects.filter(status="active").count(),
            "unpaid_invoices": Invoice.objects.filter(status="unpaid").count(),
            "partially_paid_invoices": Invoice.objects.filter(
                status="partially_paid").count(),
            "payments_needing_review": PaymentAttempt.objects.filter(
                status="needs_review").count(),
            "registrations": CourseRegistration.objects.filter(
                status="registered").count(),
            "ungraded_offerings": AssessmentResult.objects.filter(
                status="draft").count(),
            "current_semester": (
                {"id": semester.id, "label": str(semester),
                 "registration_deadline": semester.registration_deadline,
                 "fee_deadline": semester.fee_deadline} if semester else None),
        })
