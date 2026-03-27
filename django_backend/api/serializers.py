from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .models import (
    AlertEvent,
    CorrectiveAction,
    Defect,
    FiveWhyEntry,
    NotificationRule,
    RCAAnalysis,
    WorkflowHistory,
)


def _normalize_tags(tags: str) -> str:
    """Normalize a comma-separated tag string."""
    parts = [p.strip() for p in (tags or "").split(",")]
    parts = [p for p in parts if p]
    return ",".join(parts)


# PUBLIC_INTERFACE
class WorkflowHistorySerializer(serializers.ModelSerializer):
    """Serializer for workflow history entries."""

    class Meta:
        model = WorkflowHistory
        fields = [
            "id",
            "defect",
            "from_status",
            "to_status",
            "changed_by",
            "comment",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# PUBLIC_INTERFACE
class FiveWhyEntrySerializer(serializers.ModelSerializer):
    """Serializer for Five-Why entries."""

    class Meta:
        model = FiveWhyEntry
        fields = [
            "id",
            "rca",
            "why_number",
            "problem_statement",
            "why_text",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# PUBLIC_INTERFACE
class RCAAnalysisSerializer(serializers.ModelSerializer):
    """Serializer for RCA, including nested Five-Why entries (read-only)."""

    five_whys = FiveWhyEntrySerializer(many=True, read_only=True)

    class Meta:
        model = RCAAnalysis
        fields = [
            "id",
            "defect",
            "status",
            "root_cause",
            "contributing_factors",
            "completed_by",
            "completed_at",
            "five_whys",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# PUBLIC_INTERFACE
class CorrectiveActionSerializer(serializers.ModelSerializer):
    """Serializer for corrective actions."""

    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = CorrectiveAction
        fields = [
            "id",
            "defect",
            "title",
            "description",
            "action_type",
            "status",
            "owner",
            "due_date",
            "completed_at",
            "is_overdue",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_overdue", "created_at", "updated_at"]

    def get_is_overdue(self, obj: CorrectiveAction) -> bool:
        """Compute overdue flag based on due_date and completion."""
        if not obj.due_date or obj.completed_at:
            return False
        return timezone.localdate() > obj.due_date


# PUBLIC_INTERFACE
class AlertEventSerializer(serializers.ModelSerializer):
    """Serializer for alert events."""

    class Meta:
        model = AlertEvent
        fields = [
            "id",
            "rule",
            "defect",
            "corrective_action",
            "level",
            "title",
            "message",
            "acknowledged",
            "acknowledged_by",
            "acknowledged_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# PUBLIC_INTERFACE
class NotificationRuleSerializer(serializers.ModelSerializer):
    """Serializer for notification rules."""

    class Meta:
        model = NotificationRule
        fields = [
            "id",
            "name",
            "enabled",
            "event_type",
            "channel",
            "min_severity",
            "parameters",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# PUBLIC_INTERFACE
class DefectSerializer(serializers.ModelSerializer):
    """
    Serializer for defects.

    Includes nested read-only relationships for dashboard/detail pages.
    """

    rca = RCAAnalysisSerializer(read_only=True)
    corrective_actions = CorrectiveActionSerializer(many=True, read_only=True)
    workflow_history = WorkflowHistorySerializer(many=True, read_only=True)

    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Defect
        fields = [
            "id",
            "defect_id",
            "title",
            "description",
            "detected_on",
            "product",
            "component",
            "environment",
            "severity",
            "priority",
            "status",
            "reported_by",
            "owner",
            "assignee",
            "tags",
            "due_date",
            "closed_at",
            "is_overdue",
            "rca",
            "corrective_actions",
            "workflow_history",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_overdue", "created_at", "updated_at"]

    def validate_tags(self, value: str) -> str:
        """Normalize tags to a clean comma-separated string."""
        return _normalize_tags(value)

    def get_is_overdue(self, obj: Defect) -> bool:
        """Compute overdue flag based on due_date and closure."""
        if not obj.due_date or obj.closed_at:
            return False
        return timezone.localdate() > obj.due_date


# PUBLIC_INTERFACE
class DefectWorkflowTransitionSerializer(serializers.Serializer):
    """
    Serializer for workflow transition requests.

    Used by /defects/{id}/transition/ to enforce strict workflow validation and
    to write WorkflowHistory audit records.
    """

    to_status = serializers.ChoiceField(choices=Defect.Status.choices)
    changed_by = serializers.CharField(required=False, allow_blank=True, max_length=120)
    comment = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        """Validate that the transition is allowed under the workflow rules."""
        defect: Defect = self.context["defect"]
        to_status = attrs["to_status"]

        allowed = self.context["allowed_transitions"].get(defect.status, set())
        if to_status not in allowed:
            raise serializers.ValidationError(
                {
                    "to_status": (
                        f"Invalid transition from {defect.status} to {to_status}. "
                        f"Allowed: {sorted(list(allowed))}"
                    )
                }
            )

        # Business rules:
        # - moving to ACTIONS requires RCA complete
        # - moving to VERIFIED requires all corrective actions DONE or CANCELED
        # - moving to CLOSED requires VERIFIED
        if to_status == Defect.Status.ACTIONS:
            rca = getattr(defect, "rca", None)
            if not rca or rca.status != RCAAnalysis.Status.COMPLETE:
                raise serializers.ValidationError(
                    {"to_status": "RCA must be COMPLETE before moving to ACTIONS."}
                )

        if to_status == Defect.Status.VERIFIED:
            open_actions = defect.corrective_actions.exclude(
                status__in=[CorrectiveAction.Status.DONE, CorrectiveAction.Status.CANCELED]
            ).count()
            if open_actions > 0:
                raise serializers.ValidationError(
                    {"to_status": "All corrective actions must be DONE or CANCELED before VERIFIED."}
                )

        if to_status == Defect.Status.CLOSED and defect.status != Defect.Status.VERIFIED:
            raise serializers.ValidationError(
                {"to_status": "Defect must be VERIFIED before it can be CLOSED."}
            )

        return attrs

    # PUBLIC_INTERFACE
    def apply(self) -> Defect:
        """
        Apply the transition, update defect, and write WorkflowHistory.

        Returns:
            The updated Defect instance.
        """
        defect: Defect = self.context["defect"]
        to_status = self.validated_data["to_status"]
        changed_by = self.validated_data.get("changed_by", "") or ""
        comment = self.validated_data.get("comment", "") or ""

        with transaction.atomic():
            from_status = defect.status
            if from_status == to_status:
                return defect

            defect.status = to_status

            # Auto-close timestamps
            if to_status == Defect.Status.CLOSED:
                defect.closed_at = timezone.now()
            elif from_status == Defect.Status.CLOSED and to_status != Defect.Status.CLOSED:
                # If reopening for some reason (not allowed by rules by default), clear closed_at.
                defect.closed_at = None

            defect.save(update_fields=["status", "closed_at", "updated_at"])

            WorkflowHistory.objects.create(
                defect=defect,
                from_status=from_status,
                to_status=to_status,
                changed_by=changed_by,
                comment=comment,
            )

        return defect
