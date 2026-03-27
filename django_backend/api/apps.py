from __future__ import annotations

import logging
from typing import Any

from django.apps import AppConfig
from django.core.management import call_command
from django.db import OperationalError, ProgrammingError
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


def _maybe_seed_demo_data(sender: Any, **kwargs) -> None:
    """
    Seed demo data if the database is empty.

    Uses a simple duplicate-prevention check:
      - if any Defect exists -> do nothing
      - else -> run `seed_demo_data`

    This function is connected to post_migrate to ensure tables exist.
    """
    # Import locally so app registry is ready and to avoid import-time side effects.
    from api.models import Defect  # noqa: WPS433

    try:
        if Defect.objects.exists():
            logger.debug("Auto-seed check: defects already exist; skipping seeding.")
            return
    except (OperationalError, ProgrammingError) as exc:
        # DB might not be ready (during startup/migration). Avoid crashing.
        logger.debug("Auto-seed check skipped (database not ready): %s", exc)
        return

    logger.debug("Seeding demo data...")
    # Run quietly (command itself prints progress to stdout; keep Django logs as debug)
    call_command("seed_demo_data")
    logger.debug("Auto-seed finished.")


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self) -> None:
        """
        Connect the auto-seeding hook.

        We use post_migrate so seeding occurs only after the schema exists.
        """
        post_migrate.connect(_maybe_seed_demo_data, sender=self, dispatch_uid="api_auto_seed_demo_data")
