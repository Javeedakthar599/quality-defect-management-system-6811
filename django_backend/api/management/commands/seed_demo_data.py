from __future__ import annotations

import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from api.models import CorrectiveAction, Defect, RCAAnalysis

# Deterministic randomness so demo data is stable across runs (when seeded once).
_RANDOM_SEED = 6811


def _pick_weighted(items_with_weights: list[tuple[str, int]]) -> str:
    """Pick one item from a list of (value, weight)."""
    population = [v for v, _w in items_with_weights]
    weights = [w for _v, w in items_with_weights]
    return random.choices(population, weights=weights, k=1)[0]


def _mk_defect_id(n: int) -> str:
    """Create a human-friendly defect id."""
    return f"DEMO-DF-{1000 + n}"


class Command(BaseCommand):
    help = "Seed the database with realistic demo data (defects, RCAs, corrective actions)."

    # PUBLIC_INTERFACE
    def add_arguments(self, parser):
        """Add CLI args for the command."""
        parser.add_argument(
            "--count",
            type=int,
            default=20,
            help="Number of defects to create (minimum 20 recommended).",
        )

    # PUBLIC_INTERFACE
    def handle(self, *args, **options):
        """
        Seed demo data into the database.

        Creates:
          - >=20 defects with mixed severity/status/owners/areas and valid due dates
          - >=10 defects with an RCA record
          - 3-5 corrective actions for some defects, mixing completed/open/overdue

        Duplicate prevention:
          - If any Defect rows already exist, the command exits without modifying data.

        Returns:
            None. Writes progress to stdout.
        """
        count = int(options.get("count") or 20)
        if count < 20:
            count = 20

        if Defect.objects.exists():
            self.stdout.write("Defects already exist; skipping demo seeding to prevent duplicates.")
            return

        self.stdout.write("Seeding demo data...")

        random.seed(_RANDOM_SEED)
        today = timezone.localdate()
        now = timezone.now()

        owners = ["Quality Lead", "Ops Manager", "Manufacturing Eng", "Process Eng", "Supplier Quality", "IT Support"]
        assignees = ["Process Eng", "Manufacturing Eng", "Maintenance", "Quality Tech", "IT Support", "Operations"]
        products = ["Pump-X", "Valve-Z", "Actuator-A", "Sensor-S", "Controller-C"]
        components = ["Weld Joint", "Fastener", "Labeling", "PCB Assembly", "Seals", "Firmware", "Packaging"]
        environments = ["Line A", "Line B", "Line C", "Packout", "Incoming Inspection", "Test Lab"]
        reporters = ["QA Inspector", "Operator", "Customer Complaint", "Warehouse", "Audit", "Automated Test"]

        # Map requirements' terms to the project's Defect model choices:
        # - Severity: (Critical, Major, Minor) -> (CRITICAL, HIGH, MEDIUM)
        # - Status: (Open, In Analysis, Actions In Progress, Closed) -> (NEW, RCA, ACTIONS, CLOSED)
        # We also sprinkle some IN_PROGRESS for realism.
        severity_pool = [
            (Defect.Severity.CRITICAL, 2),  # "Critical"
            (Defect.Severity.HIGH, 5),      # "Major"
            (Defect.Severity.MEDIUM, 7),    # "Minor"
            (Defect.Severity.LOW, 2),
        ]
        status_pool = [
            (Defect.Status.NEW, 6),         # "Open"
            (Defect.Status.RCA, 5),         # "In Analysis"
            (Defect.Status.ACTIONS, 5),     # "Actions In Progress"
            (Defect.Status.IN_PROGRESS, 3),
            (Defect.Status.CLOSED, 3),
        ]
        priority_pool = [(Defect.Priority.P1, 2), (Defect.Priority.P2, 4), (Defect.Priority.P3, 7), (Defect.Priority.P4, 2)]

        # Ensure minimums per requirements
        min_closed = 5
        min_open = 5  # interpret as NEW/IN_PROGRESS as "open"
        min_rca_count = 10

        # Pre-assign certain indices to guarantee constraints.
        all_indices = list(range(1, count + 1))
        random.shuffle(all_indices)

        closed_indices = set(all_indices[:min_closed])
        remaining = [i for i in all_indices if i not in closed_indices]
        open_indices = set(remaining[:min_open])
        remaining = [i for i in remaining if i not in open_indices]
        rca_indices = set(remaining[:min_rca_count])
        # Some closed should also have RCA for "full lifecycle completed".
        rca_indices.update(set(list(closed_indices)[: max(2, min_closed // 2)]))

        created_defects = 0
        created_rcas = 0
        created_actions = 0
        overdue_actions_created = 0

        defect_objs: list[Defect] = []

        with transaction.atomic():
            # Create defects
            for i in range(1, count + 1):
                if i in closed_indices:
                    status = Defect.Status.CLOSED
                elif i in open_indices:
                    status = random.choice([Defect.Status.NEW, Defect.Status.IN_PROGRESS])
                else:
                    status = _pick_weighted(status_pool)

                severity = _pick_weighted(severity_pool)
                priority = _pick_weighted(priority_pool)

                product = random.choice(products)
                component = random.choice(components)
                environment = random.choice(environments)

                owner = random.choice(owners)
                assignee = random.choice(assignees)
                reported_by = random.choice(reporters)

                detected_on = today - timedelta(days=random.randint(0, 120))

                # Due date rules:
                # - closed defects should have due dates in the past, but closure recorded.
                # - some open defects should be overdue (to show dashboard overdue).
                if status == Defect.Status.CLOSED:
                    due_date = detected_on + timedelta(days=random.randint(5, 20))
                    closed_at = now - timedelta(days=random.randint(1, 30))
                else:
                    # 30% overdue, 70% future/near future
                    if random.random() < 0.30:
                        due_date = today - timedelta(days=random.randint(1, 15))
                    else:
                        due_date = today + timedelta(days=random.randint(3, 30))
                    closed_at = None

                defect = Defect.objects.create(
                    defect_id=_mk_defect_id(i),
                    title=random.choice(
                        [
                            "Leak test failure at final assembly",
                            "Incorrect label template applied",
                            "Torque spec not met on critical fastener",
                            "Intermittent sensor calibration drift",
                            "Firmware update causes reboot loop",
                            "Packaging seal integrity failure",
                            "Supplier lot variance detected in incoming inspection",
                            "Paint adhesion flake-off after cure",
                        ]
                    ),
                    description=random.choice(
                        [
                            "Issue observed intermittently; initial containment applied. Investigate contributing factors and corrective actions.",
                            "Detected during routine QA checks. Potential impact to customer experience and compliance.",
                            "Escalated due to risk; immediate triage required and cross-functional involvement recommended.",
                            "Trend indicates process drift; verify tool calibration and maintenance records.",
                        ]
                    ),
                    detected_on=detected_on,
                    product=product,
                    component=component,
                    environment=environment,
                    severity=severity,
                    priority=priority,
                    status=status,
                    reported_by=reported_by,
                    owner=owner,
                    assignee=assignee,
                    tags="demo,seed," + random.choice(["process", "supplier", "test", "assembly", "packaging"]),
                    due_date=due_date,
                    closed_at=closed_at,
                )
                defect_objs.append(defect)
                created_defects += 1

                # Add RCA for at least 10 defects
                if i in rca_indices:
                    RCAAnalysis.objects.create(
                        defect=defect,
                        status=RCAAnalysis.Status.COMPLETE if status in (Defect.Status.ACTIONS, Defect.Status.CLOSED) else RCAAnalysis.Status.DRAFT,
                        root_cause=(
                            random.choice(
                                [
                                    "Process parameter drift due to worn tooling and insufficient in-process monitoring.",
                                    "Incorrect work instruction revision used after a changeover; operator training gap.",
                                    "Supplier material variance not captured by incoming sampling plan.",
                                    "Environmental conditions in test lab out of tolerance during measurement window.",
                                ]
                            )
                            if status != Defect.Status.NEW
                            else ""
                        ),
                        contributing_factors=random.choice(
                            [
                                "Preventive maintenance interval extended; checklist not updated.",
                                "Change control incomplete; label template cache not invalidated.",
                                "Tool battery degradation; lack of spare rotation policy.",
                                "Test fixture alignment sensitivity; insufficient calibration frequency.",
                            ]
                        ),
                        completed_by=random.choice(["", "Process Eng", "Manufacturing Eng", "Quality Lead"]),
                        completed_at=(now - timedelta(days=random.randint(1, 20))) if status in (Defect.Status.ACTIONS, Defect.Status.CLOSED) else None,
                    )
                    created_rcas += 1

            # Create corrective actions: 3-5 actions for "some defects"
            # We'll apply actions to ~60% of defects for a rich demo.
            defects_with_actions = random.sample(defect_objs, k=max(12, int(round(count * 0.6))))
            for defect in defects_with_actions:
                action_count = random.randint(3, 5)
                for a_i in range(action_count):
                    # Mix: some completed, some open, some overdue
                    # Aim to guarantee >=3 overdue overall.
                    want_overdue = (overdue_actions_created < 3 and random.random() < 0.80) or (random.random() < 0.20)
                    if defect.status == Defect.Status.CLOSED:
                        # Closed defects: mostly completed actions
                        status = random.choice([CorrectiveAction.Status.DONE, CorrectiveAction.Status.DONE, CorrectiveAction.Status.CANCELED])
                        due_date = today - timedelta(days=random.randint(10, 60))
                        completed_at = now - timedelta(days=random.randint(5, 50))
                    else:
                        if want_overdue:
                            status = random.choice([CorrectiveAction.Status.OVERDUE, CorrectiveAction.Status.OPEN, CorrectiveAction.Status.IN_PROGRESS])
                            due_date = today - timedelta(days=random.randint(1, 14))
                            completed_at = None
                        else:
                            status = random.choice([CorrectiveAction.Status.OPEN, CorrectiveAction.Status.IN_PROGRESS, CorrectiveAction.Status.DONE])
                            due_date = today + timedelta(days=random.randint(3, 30))
                            completed_at = (now - timedelta(days=random.randint(1, 10))) if status == CorrectiveAction.Status.DONE else None

                    if due_date < today and completed_at is None:
                        overdue_actions_created += 1

                    CorrectiveAction.objects.create(
                        defect=defect,
                        title=random.choice(
                            [
                                "Update work instruction and retrain operators",
                                "Replace worn tooling and validate process parameters",
                                "Add in-process verification step to checklist",
                                "Increase incoming inspection sampling for supplier lot",
                                "Implement cache invalidation / template versioning",
                                "Calibrate test fixture and log results",
                                "Introduce battery health check and spare rotation",
                                "Perform containment and 100% sort of suspect inventory",
                            ]
                        ),
                        description=random.choice(
                            [
                                "Define the change, document it, and ensure it is sustained via audit checks.",
                                "Execute corrective action, verify effectiveness, and update control plan accordingly.",
                                "Implement preventive measures to reduce recurrence risk.",
                            ]
                        ),
                        action_type=random.choice([CorrectiveAction.ActionType.CORRECTIVE, CorrectiveAction.ActionType.PREVENTIVE]),
                        status=status,
                        owner=random.choice(["Maintenance", "Quality Lead", "Manufacturing Eng", "Operations", "IT Support", "Supplier Quality"]),
                        due_date=due_date,
                        completed_at=completed_at,
                    )
                    created_actions += 1

        # Debug logs required by requirements
        self.stdout.write(f"Created {created_defects} defects")
        self.stdout.write(f"Created {created_actions} actions")
        self.stdout.write(f"Created {created_rcas} RCAs")
        self.stdout.write(self.style.SUCCESS("Demo data seeding completed successfully."))
