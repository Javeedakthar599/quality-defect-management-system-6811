from __future__ import annotations

from django.core.management import BaseCommand, call_command
from django.db import transaction

from api.models import (
    AlertEvent,
    CorrectiveAction,
    Defect,
    FiveWhyEntry,
    NotificationRule,
    RCAAnalysis,
    WorkflowHistory,
)


class Command(BaseCommand):
    help = "Seed the database with demo data for the Quality Defect Management System."

    # PUBLIC_INTERFACE
    def add_arguments(self, parser):
        """Add CLI args for the command."""
        parser.add_argument(
            "--flush-api",
            action="store_true",
            help="Delete existing API app data before loading the demo fixture.",
        )

    # PUBLIC_INTERFACE
    def handle(self, *args, **options):
        """
        Seed demo data into the database.

        Parameters:
            --flush-api: If provided, deletes existing records from API tables before seeding.

        Returns:
            None. Writes progress to stdout.
        """
        flush_api = bool(options.get("flush_api"))

        with transaction.atomic():
            if flush_api:
                # Delete in dependency order to avoid FK issues.
                AlertEvent.objects.all().delete()
                FiveWhyEntry.objects.all().delete()
                RCAAnalysis.objects.all().delete()
                CorrectiveAction.objects.all().delete()
                WorkflowHistory.objects.all().delete()
                NotificationRule.objects.all().delete()
                Defect.objects.all().delete()

            call_command("loaddata", "demo_seed.json", app_label="api", verbosity=1)

        self.stdout.write(self.style.SUCCESS("Demo seed data loaded successfully."))
