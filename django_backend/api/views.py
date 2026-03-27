from __future__ import annotations

import csv
import json
import time
from datetime import timedelta
from typing import Iterable

from django.db.models import Count
from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import filters, mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import AlertEvent, CorrectiveAction, Defect, FiveWhyEntry, RCAAnalysis, WorkflowHistory
from .serializers import (
    AlertEventSerializer,
    CorrectiveActionSerializer,
    DefectSerializer,
    DefectWorkflowTransitionSerializer,
    FiveWhyEntrySerializer,
    RCAAnalysisSerializer,
    WorkflowHistorySerializer,
)


# PUBLIC_INTERFACE
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """
    Simple healthcheck endpoint.

    Returns:
        {"message": "Server is up!"}
    """
    return Response({"message": "Server is up!"})


def _allowed_defect_transitions() -> dict[str, set[str]]:
    """
    Define allowed workflow transitions for defects.

    This is the core workflow validation table. Business rules that depend on related
    objects are validated in DefectWorkflowTransitionSerializer.validate().
    """
    S = Defect.Status
    return {
        S.NEW: {S.TRIAGED, S.REJECTED},
        S.TRIAGED: {S.IN_PROGRESS, S.RCA, S.REJECTED},
        S.IN_PROGRESS: {S.RCA, S.ACTIONS, S.REJECTED},
        S.RCA: {S.ACTIONS, S.REJECTED},
        S.ACTIONS: {S.VERIFIED, S.REJECTED},
        S.VERIFIED: {S.CLOSED, S.REJECTED},
        # Terminal states by default:
        S.CLOSED: set(),
        S.REJECTED: set(),
    }


def _defect_queryset_with_prefetch():
    """Common queryset optimization for defect list/detail."""
    return (
        Defect.objects.all()
        .select_related("rca")
        .prefetch_related("corrective_actions", "workflow_history", "rca__five_whys")
    )


# PUBLIC_INTERFACE
class DefectViewSet(viewsets.ModelViewSet):
    """
    CRUD API for defects + workflow transition + CSV export.

    Filtering is implemented with query params (basic text search + field filters).
    """

    serializer_class = DefectSerializer
    queryset = _defect_queryset_with_prefetch()
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        "defect_id",
        "title",
        "description",
        "product",
        "component",
        "environment",
        "owner",
        "assignee",
        "reported_by",
        "tags",
    ]
    ordering_fields = ["created_at", "updated_at", "detected_on", "due_date", "severity", "priority", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        """
        Apply filter query params supported by the React app:
        - status, severity, priority, product, component, owner, assignee, tag
        - overdue=true
        """
        qs = _defect_queryset_with_prefetch()

        status_q = self.request.query_params.get("status")
        severity = self.request.query_params.get("severity")
        priority = self.request.query_params.get("priority")
        product = self.request.query_params.get("product")
        component = self.request.query_params.get("component")
        owner = self.request.query_params.get("owner")
        assignee = self.request.query_params.get("assignee")
        tag = self.request.query_params.get("tag")
        overdue = self.request.query_params.get("overdue")

        if status_q:
            qs = qs.filter(status=status_q)
        if severity:
            qs = qs.filter(severity=severity)
        if priority:
            qs = qs.filter(priority=priority)
        if product:
            qs = qs.filter(product__icontains=product)
        if component:
            qs = qs.filter(component__icontains=component)
        if owner:
            qs = qs.filter(owner__icontains=owner)
        if assignee:
            qs = qs.filter(assignee__icontains=assignee)
        if tag:
            qs = qs.filter(tags__icontains=tag)

        if overdue is not None and overdue.lower() in ("1", "true", "yes"):
            today = timezone.localdate()
            qs = qs.filter(due_date__isnull=False, due_date__lt=today, closed_at__isnull=True)

        return qs

    @swagger_auto_schema(
        method="post",
        operation_summary="Workflow transition a defect",
        operation_description=(
            "Transition a defect to a new workflow status with strict validation and audit history. "
            "Writes a WorkflowHistory record on success."
        ),
        request_body=DefectWorkflowTransitionSerializer,
        responses={200: DefectSerializer, 400: "Validation error"},
        tags=["defects"],
    )
    @action(detail=True, methods=["post"], url_path="transition")
    def transition(self, request, pk=None):
        """Transition a defect (validated) and return updated defect."""
        defect = self.get_object()

        serializer = DefectWorkflowTransitionSerializer(
            data=request.data,
            context={"defect": defect, "allowed_transitions": _allowed_defect_transitions()},
        )
        serializer.is_valid(raise_exception=True)
        updated = serializer.apply()

        # Re-fetch with relationships for consistent payload
        updated = _defect_queryset_with_prefetch().get(pk=updated.pk)
        return Response(DefectSerializer(updated).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        method="get",
        operation_summary="List defect workflow history",
        operation_description="Return workflow history entries for a defect ordered by created_at.",
        responses={200: WorkflowHistorySerializer(many=True)},
        tags=["defects"],
    )
    @action(detail=True, methods=["get"], url_path="history")
    def history(self, request, pk=None):
        """Return a defect's workflow history."""
        defect = self.get_object()
        history_qs = defect.workflow_history.all().order_by("created_at")
        return Response(WorkflowHistorySerializer(history_qs, many=True).data)

    @swagger_auto_schema(
        method="get",
        operation_summary="Export defects as CSV",
        operation_description=(
            "Export current defect query (same filters as list endpoint) as CSV. "
            "Useful for audits and offline analysis."
        ),
        manual_parameters=[
            openapi.Parameter("status", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("severity", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("priority", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("product", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("component", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("owner", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("assignee", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("tag", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("overdue", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        tags=["exports"],
    )
    @action(detail=False, methods=["get"], url_path="export")
    def export_csv(self, request):
        """Export filtered defects to CSV."""
        qs = self.filter_queryset(self.get_queryset()).order_by("-created_at")

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="defects_export.csv"'

        writer = csv.writer(response)
        writer.writerow(
            [
                "defect_id",
                "title",
                "status",
                "severity",
                "priority",
                "product",
                "component",
                "environment",
                "owner",
                "assignee",
                "reported_by",
                "detected_on",
                "due_date",
                "closed_at",
                "tags",
                "created_at",
                "updated_at",
            ]
        )

        for d in qs.iterator():
            writer.writerow(
                [
                    d.defect_id,
                    d.title,
                    d.status,
                    d.severity,
                    d.priority,
                    d.product,
                    d.component,
                    d.environment,
                    d.owner,
                    d.assignee,
                    d.reported_by,
                    d.detected_on,
                    d.due_date,
                    d.closed_at,
                    d.tags,
                    d.created_at,
                    d.updated_at,
                ]
            )

        return response


# PUBLIC_INTERFACE
class RCAAnalysisViewSet(viewsets.ModelViewSet):
    """CRUD API for RCA analyses."""

    serializer_class = RCAAnalysisSerializer
    queryset = RCAAnalysis.objects.all().prefetch_related("five_whys").select_related("defect")
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["defect__defect_id", "root_cause", "contributing_factors", "completed_by"]
    ordering_fields = ["created_at", "updated_at", "completed_at", "status"]
    ordering = ["-updated_at"]

    @swagger_auto_schema(
        method="post",
        operation_summary="Mark RCA complete",
        operation_description=(
            "Set RCA status to COMPLETE and optionally set completed_by. "
            "Also sets completed_at to now."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={"completed_by": openapi.Schema(type=openapi.TYPE_STRING)},
        ),
        responses={200: RCAAnalysisSerializer},
        tags=["rca"],
    )
    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        """Mark an RCA as complete."""
        rca: RCAAnalysis = self.get_object()
        completed_by = (request.data or {}).get("completed_by", "") or ""

        rca.status = RCAAnalysis.Status.COMPLETE
        rca.completed_by = completed_by
        rca.completed_at = timezone.now()
        rca.save(update_fields=["status", "completed_by", "completed_at", "updated_at"])

        return Response(RCAAnalysisSerializer(rca).data)


# PUBLIC_INTERFACE
class FiveWhyEntryViewSet(viewsets.ModelViewSet):
    """CRUD API for Five-Why entries."""

    serializer_class = FiveWhyEntrySerializer
    queryset = FiveWhyEntry.objects.all().select_related("rca", "rca__defect")
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["rca__defect__defect_id", "problem_statement", "why_text"]
    ordering_fields = ["created_at", "updated_at", "why_number"]
    ordering = ["why_number"]

    def get_queryset(self):
        """Support filtering by rca id and defect id."""
        qs = super().get_queryset()
        rca_id = self.request.query_params.get("rca")
        defect_id = self.request.query_params.get("defect")
        if rca_id:
            qs = qs.filter(rca_id=rca_id)
        if defect_id:
            qs = qs.filter(rca__defect_id=defect_id)
        return qs


# PUBLIC_INTERFACE
class CorrectiveActionViewSet(viewsets.ModelViewSet):
    """CRUD API for corrective actions."""

    serializer_class = CorrectiveActionSerializer
    queryset = CorrectiveAction.objects.all().select_related("defect")
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["defect__defect_id", "title", "description", "owner"]
    ordering_fields = ["created_at", "updated_at", "due_date", "status", "action_type"]
    ordering = ["-created_at"]

    def get_queryset(self):
        """Filter by defect, owner, status, overdue=true."""
        qs = super().get_queryset()
        defect_id = self.request.query_params.get("defect")
        owner = self.request.query_params.get("owner")
        status_q = self.request.query_params.get("status")
        overdue = self.request.query_params.get("overdue")

        if defect_id:
            qs = qs.filter(defect_id=defect_id)
        if owner:
            qs = qs.filter(owner__icontains=owner)
        if status_q:
            qs = qs.filter(status=status_q)

        if overdue is not None and overdue.lower() in ("1", "true", "yes"):
            today = timezone.localdate()
            qs = qs.filter(due_date__isnull=False, due_date__lt=today).exclude(
                status__in=[CorrectiveAction.Status.DONE, CorrectiveAction.Status.CANCELED]
            )

        return qs

    @swagger_auto_schema(
        method="post",
        operation_summary="Mark a corrective action done",
        operation_description="Sets status DONE and completed_at=now.",
        responses={200: CorrectiveActionSerializer},
        tags=["actions"],
    )
    @action(detail=True, methods=["post"], url_path="mark-done")
    def mark_done(self, request, pk=None):
        """Mark corrective action as done."""
        action_obj: CorrectiveAction = self.get_object()
        action_obj.status = CorrectiveAction.Status.DONE
        action_obj.completed_at = timezone.now()
        action_obj.save(update_fields=["status", "completed_at", "updated_at"])
        return Response(CorrectiveActionSerializer(action_obj).data)


# PUBLIC_INTERFACE
class WorkflowHistoryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read-only API for workflow history (used by dashboards/audits)."""

    serializer_class = WorkflowHistorySerializer
    queryset = WorkflowHistory.objects.all().select_related("defect")
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        """Filter by defect id."""
        qs = super().get_queryset()
        defect_id = self.request.query_params.get("defect")
        if defect_id:
            qs = qs.filter(defect_id=defect_id)
        return qs


# PUBLIC_INTERFACE
class AlertEventViewSet(viewsets.ModelViewSet):
    """CRUD API for alerts, including acknowledgement action."""

    serializer_class = AlertEventSerializer
    queryset = AlertEvent.objects.all().select_related("defect", "corrective_action", "rule")
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["title", "message", "defect__defect_id", "corrective_action__title"]
    ordering_fields = ["created_at", "updated_at", "level", "acknowledged"]
    ordering = ["-created_at"]

    def get_queryset(self):
        """Filter by acknowledged, level, defect, action."""
        qs = super().get_queryset()
        acknowledged = self.request.query_params.get("acknowledged")
        level = self.request.query_params.get("level")
        defect_id = self.request.query_params.get("defect")
        corrective_action_id = self.request.query_params.get("corrective_action")

        if acknowledged is not None:
            if acknowledged.lower() in ("1", "true", "yes"):
                qs = qs.filter(acknowledged=True)
            elif acknowledged.lower() in ("0", "false", "no"):
                qs = qs.filter(acknowledged=False)
        if level:
            qs = qs.filter(level=level)
        if defect_id:
            qs = qs.filter(defect_id=defect_id)
        if corrective_action_id:
            qs = qs.filter(corrective_action_id=corrective_action_id)
        return qs

    @swagger_auto_schema(
        method="post",
        operation_summary="Acknowledge an alert",
        operation_description="Marks an alert as acknowledged with optional acknowledged_by.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={"acknowledged_by": openapi.Schema(type=openapi.TYPE_STRING)},
        ),
        responses={200: AlertEventSerializer},
        tags=["alerts"],
    )
    @action(detail=True, methods=["post"], url_path="ack")
    def acknowledge(self, request, pk=None):
        """Acknowledge an alert event."""
        alert: AlertEvent = self.get_object()
        acknowledged_by = (request.data or {}).get("acknowledged_by", "") or ""

        alert.acknowledged = True
        alert.acknowledged_by = acknowledged_by
        alert.acknowledged_at = timezone.now()
        alert.save(update_fields=["acknowledged", "acknowledged_by", "acknowledged_at", "updated_at"])

        return Response(AlertEventSerializer(alert).data)


# ---- Alerts / analytics / polling-friendly endpoints ----

# PUBLIC_INTERFACE
@api_view(["GET"])
@permission_classes([AllowAny])
def alerts_overdue(request):
    """
    Overdue & critical alert summary endpoint (polling-friendly).

    Returns:
        {
          "timestamp": "...",
          "overdue_defects": [...],
          "overdue_actions": [...],
          "critical_defects": [...],
          "unacknowledged_alerts": [...]
        }
    """
    today = timezone.localdate()

    overdue_defects = Defect.objects.filter(
        due_date__isnull=False,
        due_date__lt=today,
        closed_at__isnull=True,
    ).order_by("-updated_at")[:200]

    overdue_actions = CorrectiveAction.objects.filter(
        due_date__isnull=False,
        due_date__lt=today,
    ).exclude(status__in=[CorrectiveAction.Status.DONE, CorrectiveAction.Status.CANCELED]).order_by(
        "-updated_at"
    )[:200]

    critical_defects = Defect.objects.filter(severity=Defect.Severity.CRITICAL).exclude(
        status__in=[Defect.Status.CLOSED, Defect.Status.REJECTED]
    ).order_by("-updated_at")[:200]

    unack_alerts = AlertEvent.objects.filter(acknowledged=False).order_by("-created_at")[:200]

    return Response(
        {
            "timestamp": timezone.now().isoformat(),
            "overdue_defects": DefectSerializer(overdue_defects, many=True).data,
            "overdue_actions": CorrectiveActionSerializer(overdue_actions, many=True).data,
            "critical_defects": DefectSerializer(critical_defects, many=True).data,
            "unacknowledged_alerts": AlertEventSerializer(unack_alerts, many=True).data,
        }
    )


# PUBLIC_INTERFACE
@api_view(["GET"])
@permission_classes([AllowAny])
def dashboard_analytics(request):
    """
    Dashboard analytics aggregations endpoint.

    Returns lightweight aggregations used for dashboard tiles/charts.
    """
    # Status counts
    by_status = list(Defect.objects.values("status").annotate(count=Count("id")).order_by("status"))

    # Severity counts for open defects
    by_severity_open = list(
        Defect.objects.exclude(status__in=[Defect.Status.CLOSED, Defect.Status.REJECTED])
        .values("severity")
        .annotate(count=Count("id"))
        .order_by("severity")
    )

    # Actions status counts
    actions_by_status = list(
        CorrectiveAction.objects.values("status").annotate(count=Count("id")).order_by("status")
    )

    # Overdue counts
    today = timezone.localdate()
    overdue_defects_count = Defect.objects.filter(
        due_date__isnull=False, due_date__lt=today, closed_at__isnull=True
    ).count()
    overdue_actions_count = CorrectiveAction.objects.filter(
        due_date__isnull=False, due_date__lt=today
    ).exclude(status__in=[CorrectiveAction.Status.DONE, CorrectiveAction.Status.CANCELED]).count()

    # Recent activity: last 20 workflow transitions
    recent_transitions = WorkflowHistory.objects.select_related("defect").order_by("-created_at")[:20]

    # Aging: buckets by detected_on
    now_date = timezone.localdate()
    buckets = [
        ("0-7d", now_date - timedelta(days=7), now_date),
        ("8-30d", now_date - timedelta(days=30), now_date - timedelta(days=8)),
        ("31-90d", now_date - timedelta(days=90), now_date - timedelta(days=31)),
        ("90d+", None, now_date - timedelta(days=91)),
    ]

    aging = []
    for label, start, end in buckets:
        if label == "90d+":
            count = Defect.objects.filter(detected_on__lt=end).count()
        else:
            count = Defect.objects.filter(detected_on__gte=start, detected_on__lte=end).count()
        aging.append({"bucket": label, "count": count})

    return Response(
        {
            "timestamp": timezone.now().isoformat(),
            "defects_by_status": by_status,
            "open_defects_by_severity": by_severity_open,
            "actions_by_status": actions_by_status,
            "overdue_defects_count": overdue_defects_count,
            "overdue_actions_count": overdue_actions_count,
            "recent_transitions": WorkflowHistorySerializer(recent_transitions, many=True).data,
            "defect_aging_buckets": aging,
        }
    )


def _current_change_cursor() -> int:
    """
    Compute a monotonic-ish cursor based on the max updated timestamps across key models.

    This is used for polling-friendly 'since cursor' endpoints. It's not perfect for
    concurrent updates but is sufficient for a demo and low-volume app.
    """
    def _ts(dt):
        if not dt:
            return 0
        return int(dt.timestamp())

    max_defect = Defect.objects.order_by("-updated_at").values_list("updated_at", flat=True).first()
    max_action = CorrectiveAction.objects.order_by("-updated_at").values_list("updated_at", flat=True).first()
    max_alert = AlertEvent.objects.order_by("-updated_at").values_list("updated_at", flat=True).first()
    max_hist = WorkflowHistory.objects.order_by("-updated_at").values_list("updated_at", flat=True).first()
    return max(_ts(max_defect), _ts(max_action), _ts(max_alert), _ts(max_hist))


# PUBLIC_INTERFACE
@api_view(["GET"])
@permission_classes([AllowAny])
def changes_poll(request):
    """
    Polling-friendly changes endpoint.

    Query params:
      - since: integer cursor (epoch seconds). If omitted, returns current cursor and empty payload.

    Returns:
      {
        "cursor": <int>,
        "changed": {
          "defects": [...],
          "actions": [...],
          "alerts": [...],
          "workflow": [...]
        }
      }
    """
    since = request.query_params.get("since")
    cursor_now = _current_change_cursor()

    if not since:
        return Response({"cursor": cursor_now, "changed": {"defects": [], "actions": [], "alerts": [], "workflow": []}})

    try:
        since_int = int(since)
    except ValueError:
        return Response({"detail": "Invalid 'since' cursor; must be integer epoch seconds."}, status=400)

    since_dt = timezone.datetime.fromtimestamp(since_int, tz=timezone.utc)

    defects = _defect_queryset_with_prefetch().filter(updated_at__gt=since_dt).order_by("-updated_at")[:200]
    actions = CorrectiveAction.objects.select_related("defect").filter(updated_at__gt=since_dt).order_by("-updated_at")[
        :200
    ]
    alerts = AlertEvent.objects.select_related("defect", "corrective_action", "rule").filter(
        updated_at__gt=since_dt
    ).order_by("-updated_at")[:200]
    workflow = WorkflowHistory.objects.select_related("defect").filter(updated_at__gt=since_dt).order_by("-updated_at")[
        :200
    ]

    return Response(
        {
            "cursor": cursor_now,
            "changed": {
                "defects": DefectSerializer(defects, many=True).data,
                "actions": CorrectiveActionSerializer(actions, many=True).data,
                "alerts": AlertEventSerializer(alerts, many=True).data,
                "workflow": WorkflowHistorySerializer(workflow, many=True).data,
            },
        }
    )


def _sse_format(event: str, data_obj) -> str:
    """Format an SSE message."""
    data = json.dumps(data_obj, default=str)
    return f"event: {event}\ndata: {data}\n\n"


# PUBLIC_INTERFACE
@api_view(["GET"])
@permission_classes([AllowAny])
def changes_sse(request):
    """
    Minimal Server-Sent Events endpoint for real-time-ish updates.

    Notes:
      - This is intentionally minimal and demo-friendly (uses periodic polling of DB).
      - Frontend can prefer /changes/poll/ if SSE is unavailable in deployment.
      - Query params:
          * since: cursor (epoch seconds). Optional.
          * heartbeat: seconds between heartbeats (default 10)
          * duration: seconds to keep the stream open (default 25) for proxy friendliness.

    Returns:
        StreamingHttpResponse (text/event-stream)
    """
    since = request.query_params.get("since")
    heartbeat = request.query_params.get("heartbeat", "10")
    duration = request.query_params.get("duration", "25")

    try:
        since_int = int(since) if since else None
        heartbeat_s = max(2, int(heartbeat))
        duration_s = max(5, int(duration))
    except ValueError:
        return Response({"detail": "Invalid query params. since/heartbeat/duration must be integers."}, status=400)

    start = time.time()
    last_cursor = since_int or _current_change_cursor()

    def event_stream() -> Iterable[str]:
        nonlocal last_cursor
        # Initial hello message
        yield _sse_format("hello", {"cursor": last_cursor, "timestamp": timezone.now().isoformat()})

        while time.time() - start < duration_s:
            cursor_now = _current_change_cursor()
            if cursor_now > last_cursor:
                since_dt = timezone.datetime.fromtimestamp(last_cursor, tz=timezone.utc)

                defects = _defect_queryset_with_prefetch().filter(updated_at__gt=since_dt).order_by("-updated_at")[:50]
                actions = CorrectiveAction.objects.select_related("defect").filter(updated_at__gt=since_dt).order_by(
                    "-updated_at"
                )[:50]
                alerts = AlertEvent.objects.select_related("defect", "corrective_action", "rule").filter(
                    updated_at__gt=since_dt
                ).order_by("-updated_at")[:50]

                payload = {
                    "cursor": cursor_now,
                    "changed": {
                        "defects": DefectSerializer(defects, many=True).data,
                        "actions": CorrectiveActionSerializer(actions, many=True).data,
                        "alerts": AlertEventSerializer(alerts, many=True).data,
                    },
                }
                yield _sse_format("changes", payload)
                last_cursor = cursor_now
            else:
                yield _sse_format("heartbeat", {"cursor": cursor_now, "timestamp": timezone.now().isoformat()})

            time.sleep(heartbeat_s)

        yield _sse_format("close", {"cursor": last_cursor, "timestamp": timezone.now().isoformat()})

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp
