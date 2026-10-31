from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from core.seeder import engine
from core.seeder.registry import seedable_models


class Command(BaseCommand):
    help = "Fill every registered model with demo data (or --purge what the seeder made). Never run against production data."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=10, help="rows per model, Department/Designation included (default 10)")
        parser.add_argument("--models", default="", help="comma separated app_label.Model names (default: all seedable models)")
        parser.add_argument("--companies", type=int, default=0, help="extra companies to create (one is made when none exist)")
        parser.add_argument("--users", type=int, default=5, help=f"demo users to create (password {engine.PASSWORD!r})")
        parser.add_argument("--no-activity", action="store_true", help="skip approval requests, decisions and mail")
        parser.add_argument("--seed", type=int, default=None, help="any whole number (e.g. 42); the same seed on the same starting data replays the same values. Default: random")
        parser.add_argument("--list", action="store_true", help="list the seedable models and exit")
        parser.add_argument("--purge", nargs="?", const="all", default=None, metavar="BATCH",
                            help="delete seeded rows (all batches, or the given batch id)")
        parser.add_argument("--force", action="store_true", help="run even when SEEDER_ENABLED is off (DEBUG=False)")

    def handle(self, *args, **opts):
        if opts["list"]:
            for model in engine.ordered(seedable_models()):
                self.stdout.write(f"{engine.label(model):40} {model._base_manager.count():>7} rows")
            for b in engine.batches():
                self.stdout.write(f"batch {b['batch']}  {b['rows']} rows  {b['at']:%Y-%m-%d %H:%M}")
            return
        if not engine.enabled() and not opts["force"]:
            raise CommandError("The seeder is disabled here (DEBUG is off). Use --force only on a throw-away database.")
        if opts["purge"]:
            deleted, problems = engine.purge(None if opts["purge"] == "all" else opts["purge"])
            self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} seeded rows."))
            for p in problems:
                self.stdout.write(self.style.WARNING(f"  kept {p}"))
            return
        chosen = None
        if opts["models"]:
            try:
                chosen = [apps.get_model(name.strip()) for name in opts["models"].split(",") if name.strip()]
            except (LookupError, ValueError) as exc:
                raise CommandError(str(exc)) from exc
        result = engine.run(models_=chosen, count=opts["count"], companies=opts["companies"], users=opts["users"],
                            activity=not opts["no_activity"], seed=opts["seed"])
        for name, n in result.created.items():
            self.stdout.write(f"  {name:40} +{n}")
        for name, n in result.activity.items():
            self.stdout.write(f"  {name:40} +{n}")
        for name, why in result.skipped.items():
            self.stdout.write(self.style.WARNING(f"  skipped {name}: {why}"))
        self.stdout.write(self.style.SUCCESS(f"Batch {result.batch}: {result.total} rows. Demo users sign in with {engine.PASSWORD!r}."))
