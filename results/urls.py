from django.urls import include, path
from core.routers import OptionalSlashRouter as DefaultRouter

from .views import (
    GradeBandViewSet, GradingPolicyViewSet, HodApproveResultsView,
    HodMarksFormSaveView, HodMarksOfferingsView, HodMarksRosterView,
    HodMarksUploadTemplateView, HodMarksUploadView, LecturerMarksView,
    LockResultsView, OfferingResultsView, PublishResultsView,
    ResultCorrectionView, StudentResultsView, StudentTranscriptView,
    TranscriptViewSet,
)

router = DefaultRouter(trailing_slash="/?")
router.register("admin/grading-policies", GradingPolicyViewSet, basename="grading-policies")
router.register("admin/grade-bands", GradeBandViewSet, basename="grade-bands")
router.register("admin/transcripts", TranscriptViewSet, basename="transcripts")

urlpatterns = [
    path("lecturer/offerings/<int:offering_id>/results", LecturerMarksView.as_view(),
         name="lecturer-results"),
    path("hod/offerings/<int:offering_id>/approve-results",
         HodApproveResultsView.as_view(), name="hod-approve-results"),
    path("hod/marks-upload/template", HodMarksUploadTemplateView.as_view(),
         name="hod-marks-upload-template"),
    path("hod/marks-upload", HodMarksUploadView.as_view(),
         name="hod-marks-upload"),
    path("hod/marks-form/offerings", HodMarksOfferingsView.as_view(),
         name="hod-marks-form-offerings"),
    path("hod/marks-form/roster", HodMarksRosterView.as_view(),
         name="hod-marks-form-roster"),
    path("hod/marks-form/save", HodMarksFormSaveView.as_view(),
         name="hod-marks-form-save"),
    path("admin/offerings/<int:offering_id>/publish-results",
         PublishResultsView.as_view(), name="publish-results"),
    path("admin/offerings/<int:offering_id>/lock-results",
         LockResultsView.as_view(), name="lock-results"),
    path("admin/offerings/<int:offering_id>/results",
         OfferingResultsView.as_view(), name="offering-results"),
    path("admin/results/<int:result_id>/correct", ResultCorrectionView.as_view(),
         name="result-correct"),
    path("student/results", StudentResultsView.as_view(), name="student-results"),
    path("student/transcript", StudentTranscriptView.as_view(), name="student-transcript"),
    path("", include(router.urls)),
]
