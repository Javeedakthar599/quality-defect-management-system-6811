from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AlertEventViewSet,
    CorrectiveActionViewSet,
    DefectViewSet,
    FiveWhyEntryViewSet,
    RCAAnalysisViewSet,
    WorkflowHistoryViewSet,
    alerts_overdue,
    changes_poll,
    changes_sse,
    dashboard_analytics,
    health,
)

router = DefaultRouter()
router.register(r"defects", DefectViewSet, basename="defect")
router.register(r"rca", RCAAnalysisViewSet, basename="rca")
router.register(r"five-whys", FiveWhyEntryViewSet, basename="fivewhy")
router.register(r"actions", CorrectiveActionViewSet, basename="action")
router.register(r"workflow", WorkflowHistoryViewSet, basename="workflow")
router.register(r"alerts", AlertEventViewSet, basename="alert")

urlpatterns = [
    path("health/", health, name="Health"),
    path("", include(router.urls)),
    path("analytics/dashboard/", dashboard_analytics, name="dashboard-analytics"),
    path("alerts/overdue/", alerts_overdue, name="alerts-overdue"),
    path("changes/poll/", changes_poll, name="changes-poll"),
    path("changes/sse/", changes_sse, name="changes-sse"),
]
