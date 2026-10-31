from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from academics.models import CourseOffering, CourseRegistration, Semester
from core.audit import audit
from core.exceptions import BusinessError
from core.permissions import IsAcademicsAdmin, IsHOD, IsRegistrar, has_role

from .models import (
    AssessmentResult, GradeBand, GradingPolicy, TranscriptRequest,
)
from .serializers import (
    AssessmentResultSerializer, BulkMarksSerializer, CorrectionSerializer,
    GradeBandSerializer, GradingPolicySerializer, ResultCorrectionSerializer,
    TranscriptRequestSerializer,
)
from .services import active_policy, compute, compute_gpa


def _lecturer_profile(user):
    return getattr(user, "lecturer_profile", None)


def _hod_department_id(user):
    profile = _lecturer_profile(user)
    return profile.department_id if profile and profile.is_hod else None


def _get_offering(offering_id):
    offering = CourseOffering.objects.filter(id=offering_id).select_related(
        "course").first()
    if offering is None:
        raise BusinessError("Offering not found.", code="OFFERING_NOT_FOUND")
    return offering


def _can_view_offering(user, offering) -> bool:
    if has_role(user, "admin", "registrar"):
        return True
    profile = _lecturer_profile(user)
    if profile and offering.lecturer_id == profile.id:
        return True
    if has_role(user, "hod") and offering.course.department_id == _hod_department_id(user):
        return True
    return False


def _recompute(result, policy):
    outcome = compute(result.ca_mark, result.final_mark, policy)
    if "errors" in outcome:
        return outcome["errors"]
    result.total = outcome["total"]
    result.grade_band = outcome["band"]
    result.grade_points = outcome["points"]
    result.is_pass = outcome["is_pass"]
    return None


class GradingPolicyViewSet(viewsets.ModelViewSet):
    queryset = GradingPolicy.objects.prefetch_related("bands").all()
    serializer_class = GradingPolicySerializer
    filterset_fields = ["program", "academic_unit", "academic_year", "is_active"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAcademicsAdmin()]

    def perform_create(self, serializer):
        serializer.save()
        audit("results.policy_created", actor=self.request.user,
              entity=serializer.instance)

    def perform_update(self, serializer):
        serializer.save()
        audit("results.policy_updated", actor=self.request.user,
              entity=serializer.instance)


class GradeBandViewSet(viewsets.ModelViewSet):
    queryset = GradeBand.objects.all()
    serializer_class = GradeBandSerializer
    filterset_fields = ["policy"]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAcademicsAdmin()]


class LecturerMarksView(APIView):
    """PUT /api/lecturer/offerings/:id/results – save draft/submitted marks.

    Blueprint SS8: lecturer enters CA/final for their own assigned roster.
    Published/locked results cannot be touched (RESULTS_LOCKED).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, offering_id):
        offering = _get_offering(offering_id)
        if not _can_view_offering(request.user, offering):
            raise BusinessError("You cannot view this offering.",
                                code="NOT_YOUR_OFFERING")
        results = AssessmentResult.objects.filter(
            registration__offering=offering).select_related(
            "registration__student", "registration__offering__course", "grade_band")
        policy = active_policy()
        return Response({
            "offering_id": offering.id,
            "policy": GradingPolicySerializer(policy).data if policy else None,
            "results": AssessmentResultSerializer(results, many=True).data,
        })

    def put(self, request, offering_id):
        offering = _get_offering(offering_id)
        profile = _lecturer_profile(request.user)
        # The assigned lecturer — or the HOD in charge of the department
        # the course belongs to (the HOD owns departmental marks).
        is_department_hod = (
            profile is not None and profile.is_hod
            and offering.course.department_id is not None
            and offering.course.department_id == profile.department_id)
        if not (has_role(request.user, "admin")
                or (profile and offering.lecturer_id == profile.id)
                or (is_department_hod and has_role(request.user, "hod"))):
            raise BusinessError("You do not teach this offering.",
                                code="NOT_YOUR_OFFERING")
        serializer = BulkMarksSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submit = serializer.validated_data["submit"]
        entries = serializer.validated_data["marks"]

        # A published/locked result set rejects edits outright - even
        # though publishing flips registrations to "completed".
        if AssessmentResult.objects.filter(
                registration__offering=offering,
                status__in=("published", "locked")).exists():
            raise BusinessError(
                "Results for this offering are published and locked.",
                code="RESULTS_LOCKED")
        roster = {
            r.id: r
            for r in CourseRegistration.objects.filter(
                offering=offering, status__in=("registered", "completed"))
        }
        if not roster:
            return Response({"saved": [], "errors": []})
        policy = active_policy(next(iter(roster.values())).student)
        if policy is None:
            raise BusinessError("No active grading policy is configured.",
                                code="NO_GRADING_POLICY")

        saved, errors = [], []
        for entry in entries:
            reg_id = entry["registration_id"]
            registration = roster.get(reg_id)
            if registration is None:
                errors.append({"registration_id": reg_id,
                               "error": "Student is not registered for this offering."})
                continue
            result, _ = AssessmentResult.objects.get_or_create(
                registration=registration)
            if result.status in ("published", "locked"):
                raise BusinessError(
                    "Results for this offering are published and locked.",
                    code="RESULTS_LOCKED")
            if result.status == "hod_approved":
                errors.append({"registration_id": reg_id,
                               "error": "Result already approved by the HOD."})
                continue
            if entry.get("ca_mark") is not None:
                result.ca_mark = entry["ca_mark"]
            if entry.get("final_mark") is not None:
                result.final_mark = entry["final_mark"]
            field_errors = _recompute(result, policy)
            if field_errors:
                errors.append({"registration_id": reg_id,
                               "errors": field_errors})
                continue
            result.entered_by = request.user
            result.status = "submitted" if submit else "draft"
            result.save()
            saved.append(result)
        audit("results.marks_saved", actor=request.user, entity=offering,
              summary={"submitted": submit, "count": len(saved)})
        return Response({
            "saved": AssessmentResultSerializer(saved, many=True).data,
            "errors": errors,
        })


def _hod_upload_department_id(user):
    """Department an HOD marks uploader is scoped to.

    Returns the HOD's own department id, or ``None`` for an unscoped
    super admin. Raises for anyone else (lecturer, registrar-only, or an
    HOD role without a headed department).
    """
    if has_role(user, "super_admin"):
        return None
    department_id = _hod_department_id(user)
    if department_id is not None:
        return department_id
    if has_role(user, "admin"):
        return None
    raise BusinessError(
        "Only a Head of Department can upload marks. Your account is not "
        "linked to a department as its HOD.",
        code="NOT_DEPARTMENT_HOD", http_status=status.HTTP_403_FORBIDDEN)


class HodMarksUploadTemplateView(APIView):
    """GET /api/hod/marks-upload/template – downloadable CSV template."""

    permission_classes = [IsHOD]

    def get(self, request):
        from .hod_marks import CSV_TEMPLATE

        response = HttpResponse(CSV_TEMPLATE, content_type="text/csv")
        response["Content-Disposition"] = (
            'attachment; filename="hod-marks-template.csv"')
        return response


class HodMarksUploadView(APIView):
    """POST /api/hod/marks-upload – HOD uploads CA/exam marks by CSV.

    Form fields: ``file`` (the CSV), ``academic_year`` (id),
    ``semester`` (id, must belong to that year) and optional
    ``dry_run`` ("1"/"true" to validate + preview without saving).

    Marks are routed to each student by matricule and published straight
    to them; students whose fees have not cleared are held back (not
    saved) and reported in ``held_fees``.
    """

    permission_classes = [IsHOD]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        from .hod_marks import import_hod_marks_csv

        department_id = _hod_upload_department_id(request.user)
        upload = request.FILES.get("file")
        if upload is None:
            raise BusinessError("A CSV file is required (field name: file).",
                                code="CSV_REQUIRED")
        year_id = request.data.get("academic_year")
        semester_id = request.data.get("semester")
        if not year_id or not semester_id:
            raise BusinessError(
                "academic_year and semester are required: choose the "
                "academic year and semester these marks belong to.",
                code="SEMESTER_REQUIRED")
        semester = (
            Semester.objects.filter(id=semester_id, academic_year_id=year_id)
            .select_related("academic_year").first())
        if semester is None:
            raise BusinessError(
                "That semester does not belong to the selected academic "
                "year.", code="SEMESTER_NOT_IN_YEAR")
        try:
            csv_text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise BusinessError(
                "The file could not be read as text. Upload a UTF-8 CSV "
                "file.", code="CSV_UNREADABLE")
        dry_run = str(request.data.get("dry_run", "")).lower() in (
            "1", "true", "yes")
        report = import_hod_marks_csv(
            request.user, semester=semester, department_id=department_id,
            csv_text=csv_text, dry_run=dry_run)
        return Response(report)


class HodApproveResultsView(APIView):
    """POST /api/hod/offerings/:id/approve-results – HOD approves drafts."""

    permission_classes = [IsAuthenticated]

    def post(self, request, offering_id):
        offering = _get_offering(offering_id)
        if not (has_role(request.user, "admin")
                or (has_role(request.user, "hod")
                    and offering.course.department_id == _hod_department_id(request.user))):
            raise BusinessError("Only the department HOD may approve these results.",
                                code="OUT_OF_DEPARTMENT_SCOPE")
        results = AssessmentResult.objects.filter(
            registration__offering=offering, status="submitted")
        count = results.update(status="hod_approved", approved_by=request.user)
        audit("results.hod_approved", actor=request.user, entity=offering,
              reason=request.data.get("reason", ""), summary={"count": count})
        return Response({"offering_id": offering.id, "approved": count})


class PublishResultsView(APIView):
    """POST /api/admin/offerings/:id/publish-results – publish + lock."""

    permission_classes = [IsRegistrar]

    def post(self, request, offering_id):
        offering = _get_offering(offering_id)
        results = list(AssessmentResult.objects.filter(
            registration__offering=offering,
            status__in=("submitted", "hod_approved")))
        now = timezone.now()
        for result in results:
            result.status = "published"
            result.published_by = request.user
            result.published_at = now
            result.save(update_fields=["status", "published_by", "published_at",
                                       "updated_at"])
            result.registration.status = "completed"
            result.registration.save(update_fields=["status", "updated_at"])
            if result.registration.student.user_id:
                from communication.services import notify

                notify(result.registration.student.user, "result",
                       "Result published",
                       f"Your result for {offering.course.code} is published: "
                       f"{result.total} "
                       f"({result.grade_band.letter if result.grade_band_id else ''}).")
        audit("results.published", actor=request.user, entity=offering,
              summary={"count": len(results)})
        return Response({"offering_id": offering.id, "published": len(results)})


class LockResultsView(APIView):
    """POST /api/admin/offerings/:id/lock-results – lock a published set."""

    permission_classes = [IsRegistrar]

    def post(self, request, offering_id):
        offering = _get_offering(offering_id)
        count = AssessmentResult.objects.filter(
            registration__offering=offering, status="published").update(status="locked")
        audit("results.locked", actor=request.user, entity=offering,
              summary={"count": count})
        return Response({"offering_id": offering.id, "locked": count})


class ResultCorrectionView(APIView):
    """POST /api/admin/results/:id/correct – audited post-publication fix."""

    permission_classes = [IsRegistrar]

    def post(self, request, result_id):
        result = AssessmentResult.objects.filter(id=result_id).select_related(
            "registration__student").first()
        if result is None:
            raise BusinessError("Result not found.", code="RESULT_NOT_FOUND")
        serializer = CorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        policy = active_policy(result.registration.student)
        old = (result.ca_mark, result.final_mark, result.total)
        if data.get("ca_mark") is not None:
            result.ca_mark = data["ca_mark"]
        if data.get("final_mark") is not None:
            result.final_mark = data["final_mark"]
        field_errors = _recompute(result, policy)
        if field_errors:
            raise BusinessError(str(field_errors), code="INVALID_MARKS")
        from .models import ResultCorrection

        correction = ResultCorrection.objects.create(
            result=result, old_ca=old[0], old_final=old[1], old_total=old[2],
            new_ca=result.ca_mark, new_final=result.final_mark,
            new_total=result.total, reason=data["reason"],
            changed_by=request.user)
        result.change_reason = data["reason"]
        result.save()
        audit("results.corrected", actor=request.user, entity=result,
              reason=data["reason"],
              summary={"old_total": str(old[2]), "new_total": str(result.total)})
        return Response(ResultCorrectionSerializer(correction).data,
                        status=status.HTTP_201_CREATED)


class StudentResultsView(APIView):
    """GET /api/student/results – only own *published* results + GPA."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        student = getattr(request.user, "student_profile", None)
        if student is None:
            raise BusinessError("Your account is not linked to a student profile.",
                                code="NOT_A_STUDENT")
        results = AssessmentResult.objects.filter(
            registration__student=student,
            status__in=("published", "locked")).select_related(
            "registration__offering__course", "grade_band")
        policy = active_policy(student)
        gpa = compute_gpa(
            [(r, r.registration.offering.course.credits) for r in results],
            policy) if policy else None
        return Response({
            "results": AssessmentResultSerializer(results, many=True).data,
            "gpa": str(gpa) if gpa is not None else None,
            "policy": GradingPolicySerializer(policy).data if policy else None,
        })

class TranscriptViewSet(viewsets.ModelViewSet):
    serializer_class = TranscriptRequestSerializer
    filterset_fields = ["student", "status", "is_official"]

    def get_queryset(self):
        qs = TranscriptRequest.objects.select_related("student").all()
        user = self.request.user
        if has_role(user, "admin", "registrar"):
            return qs
        if has_role(user, "student"):
            return qs.filter(student__user=user)
        return qs.none()

    def get_permissions(self):
        if self.action in ("issue",):
            return [IsRegistrar()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        user = self.request.user
        if has_role(user, "student"):
            student = getattr(user, "student_profile", None)
            serializer.save(student=student, requested_by=user, is_official=False)
        else:
            serializer.save(requested_by=user)
        audit("transcript.requested", actor=user, entity=serializer.instance)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """Unofficial PDF (labelled DRAFT); official needs issue first."""
        from .transcripts import build_transcript_pdf

        transcript = self.get_object()
        official = transcript.is_official and transcript.status == "issued"
        return build_transcript_pdf(transcript.student, official=official)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        """Registrar issues an official transcript with an issue number."""
        from .models import TranscriptRequest as TR

        transcript = self.get_object()
        if transcript.status == "issued":
            raise BusinessError("This transcript was already issued.",
                                code="ALREADY_ISSUED")
        transcript.is_official = True
        transcript.status = "issued"
        transcript.issued_by = request.user
        transcript.issued_at = timezone.now()
        transcript.issue_number = f"TRX-{transcript.id:06d}"
        policy = active_policy(transcript.student)
        transcript.policy_snapshot = {
            "policy": policy.name if policy else None,
            "issued_at": transcript.issued_at.isoformat(),
        }
        transcript.save(update_fields=["is_official", "status", "issued_by",
                                       "issued_at", "issue_number",
                                       "policy_snapshot", "updated_at"])
        audit("transcript.issued", actor=request.user, entity=transcript,
              summary={"issue_number": transcript.issue_number})
        return Response(TranscriptRequestSerializer(transcript).data)


class StudentTranscriptView(APIView):
    """GET /api/student/transcript – own draft (unofficial) transcript PDF."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        student = getattr(request.user, "student_profile", None)
        if student is None:
            raise BusinessError("Your account is not linked to a student profile.",
                                code="NOT_A_STUDENT")
        from .transcripts import build_transcript_pdf

        return build_transcript_pdf(student, official=False)


class OfferingResultsView(APIView):
    """GET /api/admin/offerings/:id/results – review a result set."""

    permission_classes = [IsAuthenticated]

    def get(self, request, offering_id):
        offering = _get_offering(offering_id)
        if not _can_view_offering(request.user, offering):
            raise BusinessError("You cannot view these results.",
                                code="NOT_YOUR_OFFERING")
        results = AssessmentResult.objects.filter(
            registration__offering=offering).select_related(
            "registration__student", "registration__offering__course", "grade_band")
        return Response(AssessmentResultSerializer(results, many=True).data)
