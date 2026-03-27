from __future__ import annotations

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    """Abstract base class that provides created/updated timestamps."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        abstract = True


class Defect(TimestampedModel):
    """
    A single quality defect record.

    Tracks core metadata about the defect, its severity/priority, ownership, and its
    current workflow status.
    """

    class Severity(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    class Priority(models.TextChoices):
        P4 = "P4", "P4"
        P3 = "P3", "P3"
        P2 = "P2", "P2"
        P1 = "P1", "P1"

    class Status(models.TextChoices):
        NEW = "NEW", "New"
        TRIAGED = "TRIAGED", "Triaged"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        RCA = "RCA", "RCA"
        ACTIONS = "ACTIONS", "Actions"
        VERIFIED = "VERIFIED", "Verified"
        CLOSED = "CLOSED", "Closed"
        REJECTED = "REJECTED", "Rejected"

    defect_id = models.CharField(
        max_length=32,
        unique=True,
        help_text="Human-friendly identifier (e.g., DF-1001).",
    )
    title = models.CharField(max_length=200, db_index=True)
    description = models.TextField(blank=True)
    detected_on = models.DateField(default=timezone.localdate, db_index=True)

    product = models.CharField(max_length=120, blank=True, db_index=True)
    component = models.CharField(max_length=120, blank=True, db_index=True)
    environment = models.CharField(max_length=120, blank=True, db_index=True)

    severity = models.CharField(
        max_length=16, choices=Severity.choices, default=Severity.MEDIUM, db_index=True
    )
    priority = models.CharField(
        max_length=2, choices=Priority.choices, default=Priority.P3, db_index=True
    )
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.NEW, db_index=True
    )

    reported_by = models.CharField(max_length=120, blank=True, db_index=True)
    owner = models.CharField(max_length=120, blank=True, db_index=True)
    assignee = models.CharField(max_length=120, blank=True, db_index=True)

    tags = models.CharField(
        max_length=300,
        blank=True,
        help_text="Comma-separated tags for simple filtering.",
    )

    due_date = models.DateField(null=True, blank=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "severity"]),
            models.Index(fields=["status", "priority"]),
            models.Index(fields=["product", "component"]),
            models.Index(fields=["owner", "assignee"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.defect_id}: {self.title}"


class WorkflowHistory(TimestampedModel):
    """
    Records every workflow transition for a defect, including who changed it and why.
    """

    defect = models.ForeignKey(
        Defect, on_delete=models.CASCADE, related_name="workflow_history"
    )

    from_status = models.CharField(max_length=24, choices=Defect.Status.choices)
    to_status = models.CharField(max_length=24, choices=Defect.Status.choices)

    changed_by = models.CharField(max_length=120, blank=True, db_index=True)
    comment = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["defect", "created_at"]),
            models.Index(fields=["to_status", "created_at"]),
        ]
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                check=~models.Q(from_status=models.F("to_status")),
                name="workflow_from_status_not_equal_to_status",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.defect.defect_id}: {self.from_status} -> {self.to_status}"


class RCAAnalysis(TimestampedModel):
    """
    Root Cause Analysis attached to a defect.

    Usually completed as 5-Why entries, plus a final root cause statement.
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        COMPLETE = "COMPLETE", "Complete"

    defect = models.OneToOneField(Defect, on_delete=models.CASCADE, related_name="rca")

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    root_cause = models.TextField(blank=True)
    contributing_factors = models.TextField(blank=True)

    completed_by = models.CharField(max_length=120, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["status", "completed_at"])]

    def __str__(self) -> str:  # pragma: no cover
        return f"RCA for {self.defect.defect_id}"


class FiveWhyEntry(TimestampedModel):
    """
    One entry of a '5-Why' analysis.

    Enforces 1..5 ordering for each RCAAnalysis.
    """

    rca = models.ForeignKey(
        RCAAnalysis, on_delete=models.CASCADE, related_name="five_whys"
    )
    why_number = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1)],
        help_text="1..5",
    )
    problem_statement = models.CharField(max_length=300, blank=True)
    why_text = models.TextField()

    class Meta:
        ordering = ["why_number"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(why_number__gte=1) & models.Q(why_number__lte=5),
                name="fivewhy_why_number_between_1_and_5",
            ),
            models.UniqueConstraint(
                fields=["rca", "why_number"], name="fivewhy_unique_number_per_rca"
            ),
        ]
        indexes = [
            models.Index(fields=["rca", "why_number"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return f"Why {self.why_number} for {self.rca.defect.defect_id}"


class CorrectiveAction(TimestampedModel):
    """
    A corrective (or preventive) action created to address a defect's root cause.
    """

    class ActionType(models.TextChoices):
        CORRECTIVE = "CORRECTIVE", "Corrective"
        PREVENTIVE = "PREVENTIVE", "Preventive"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        DONE = "DONE", "Done"
        CANCELED = "CANCELED", "Canceled"
        OVERDUE = "OVERDUE", "Overdue"

    defect = models.ForeignKey(
        Defect, on_delete=models.CASCADE, related_name="corrective_actions"
    )

    title = models.CharField(max_length=200, db_index=True)
    description = models.TextField(blank=True)

    action_type = models.CharField(
        max_length=16,
        choices=ActionType.choices,
        default=ActionType.CORRECTIVE,
        db_index=True,
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True
    )

    owner = models.CharField(max_length=120, blank=True, db_index=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["defect", "status"]),
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["owner", "status"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.defect.defect_id} - {self.title}"


class NotificationRule(TimestampedModel):
    """
    Metadata defining which events should generate alerts/notifications.

    This is intentionally generic so the frontend/backend can interpret rules without a
    dedicated notification provider integration.
    """

    class Channel(models.TextChoices):
        IN_APP = "IN_APP", "In-app"
        EMAIL = "EMAIL", "Email"

    class EventType(models.TextChoices):
        DEFECT_OVERDUE = "DEFECT_OVERDUE", "Defect overdue"
        ACTION_OVERDUE = "ACTION_OVERDUE", "Action overdue"
        STATUS_CHANGED = "STATUS_CHANGED", "Status changed"
        HIGH_SEVERITY = "HIGH_SEVERITY", "High severity"

    name = models.CharField(max_length=120, unique=True)
    enabled = models.BooleanField(default=True, db_index=True)

    event_type = models.CharField(max_length=32, choices=EventType.choices, db_index=True)
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.IN_APP)

    # If set, only apply the rule when defect severity >= this threshold in severity order.
    min_severity = models.CharField(
        max_length=16, choices=Defect.Severity.choices, null=True, blank=True, db_index=True
    )

    # JSON-ish string for simple parameterization (kept as TextField to avoid DB-specific JSON constraints).
    parameters = models.TextField(
        blank=True,
        help_text="Optional parameters in JSON format (stored as text for portability).",
    )

    class Meta:
        indexes = [
            models.Index(fields=["enabled", "event_type"]),
            models.Index(fields=["channel", "enabled"]),
        ]

    def __str__(self) -> str:  # pragma: no cover
        return self.name


class AlertEvent(TimestampedModel):
    """
    A persisted alert/notification event generated for a defect or corrective action.

    This acts as an audit trail and a simple in-app notification store.
    """

    class Level(models.TextChoices):
        INFO = "INFO", "Info"
        WARNING = "WARNING", "Warning"
        CRITICAL = "CRITICAL", "Critical"

    rule = models.ForeignKey(
        NotificationRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    defect = models.ForeignKey(
        Defect, null=True, blank=True, on_delete=models.CASCADE, related_name="alert_events"
    )
    corrective_action = models.ForeignKey(
        CorrectiveAction,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="alert_events",
    )

    level = models.CharField(max_length=16, choices=Level.choices, default=Level.INFO, db_index=True)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)

    acknowledged = models.BooleanField(default=False, db_index=True)
    acknowledged_by = models.CharField(max_length=120, blank=True, db_index=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["acknowledged", "level"]),
            models.Index(fields=["defect", "created_at"]),
            models.Index(fields=["corrective_action", "created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(defect__isnull=False)
                    | models.Q(corrective_action__isnull=False)
                ),
                name="alertevent_requires_defect_or_action",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.level}: {self.title}"
