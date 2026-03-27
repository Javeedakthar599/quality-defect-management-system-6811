from django.contrib import admin

from .models import (
    AlertEvent,
    CorrectiveAction,
    Defect,
    FiveWhyEntry,
    NotificationRule,
    RCAAnalysis,
    WorkflowHistory,
)


@admin.register(Defect)
class DefectAdmin(admin.ModelAdmin):
    list_display = (
        "defect_id",
        "title",
        "status",
        "severity",
        "priority",
        "owner",
        "assignee",
        "detected_on",
        "due_date",
        "created_at",
    )
    list_filter = ("status", "severity", "priority", "product", "component", "environment")
    search_fields = ("defect_id", "title", "description", "product", "component", "owner", "assignee")
    ordering = ("-created_at",)


@admin.register(WorkflowHistory)
class WorkflowHistoryAdmin(admin.ModelAdmin):
    list_display = ("defect", "from_status", "to_status", "changed_by", "created_at")
    list_filter = ("from_status", "to_status")
    search_fields = ("defect__defect_id", "changed_by", "comment")
    ordering = ("-created_at",)


@admin.register(RCAAnalysis)
class RCAAnalysisAdmin(admin.ModelAdmin):
    list_display = ("defect", "status", "completed_by", "completed_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("defect__defect_id", "root_cause", "contributing_factors", "completed_by")


@admin.register(FiveWhyEntry)
class FiveWhyEntryAdmin(admin.ModelAdmin):
    list_display = ("rca", "why_number", "problem_statement", "created_at")
    list_filter = ("why_number",)
    search_fields = ("rca__defect__defect_id", "why_text", "problem_statement")
    ordering = ("rca", "why_number")


@admin.register(CorrectiveAction)
class CorrectiveActionAdmin(admin.ModelAdmin):
    list_display = ("defect", "title", "status", "action_type", "owner", "due_date", "completed_at")
    list_filter = ("status", "action_type")
    search_fields = ("defect__defect_id", "title", "description", "owner")
    ordering = ("-created_at",)


@admin.register(NotificationRule)
class NotificationRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled", "event_type", "channel", "min_severity", "updated_at")
    list_filter = ("enabled", "event_type", "channel", "min_severity")
    search_fields = ("name",)


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ("level", "title", "acknowledged", "defect", "corrective_action", "created_at")
    list_filter = ("level", "acknowledged")
    search_fields = ("title", "message", "defect__defect_id", "corrective_action__title")
    ordering = ("-created_at",)
