from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from core import approvals
from core.models import Company, Department

from core.seeder import engine, register_seeder, unregister_seeder
from core.seeder.models import SeedRecord
from core.seeder.registry import seedable_models
from tests.dummyapp.models import Buyer, Fabric, Garment, GarmentLine, LineOption, Order

from .base import CoreTestCase, make_user


class SeederTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        approvals.sync_all_chains()

    def test_every_registered_business_model_is_seedable_and_core_config_is_not(self):
        names = {engine.label(m) for m in seedable_models()}
        self.assertTrue({"dummyapp.Garment", "dummyapp.Order", "dummyapp.Buyer", "core.Company", "core.User"} <= names)
        self.assertFalse(names & {"core.AuditLog", "core.ApprovalType", "core.MenuItem", "core.NotificationRule"})

    def test_run_fills_models_in_dependency_order_with_inlines_users_and_activity(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = engine.run(count=4, users=4, seed=7)
        self.assertEqual(result.skipped, {})
        self.assertEqual(Order.objects.count(), 4)                 # needs buyers: seeded first
        self.assertTrue(all(o.buyer.company_id == o.company_id for o in Order.objects.all()))
        self.assertGreaterEqual(GarmentLine.objects.count(), 4)
        self.assertTrue(LineOption.objects.exists())               # nested inline
        self.assertTrue(Department.objects.exists())
        users = get_user_model().objects.filter(groups__name=engine.ROLE_NAME)
        self.assertEqual(users.count(), 4)
        clerk = users.first()
        self.assertTrue(self.client.login(email=clerk.email, password=engine.PASSWORD))
        self.assertTrue(clerk.has_perm("dummyapp.view_garment"))
        self.assertGreater(result.activity["mail messages"], 0)
        self.assertGreater(result.activity["approval requests"], 0)
        self.assertEqual(SeedRecord.objects.filter(batch=result.batch).count() >= result.total, True)

    def test_count_applies_to_departments_and_designations_too(self):
        from core.models import Designation

        before = Department.objects.count(), Designation.objects.count()
        result = engine.run(count=3, users=0, activity=False, models_=[Department, Designation])
        self.assertEqual(result.created, {"core.Department": 3, "core.Designation": 3})
        self.assertEqual((Department.objects.count(), Designation.objects.count()), (before[0] + 3, before[1] + 3))
        engine.run(count=7, users=0, activity=False, models_=[Department])
        self.assertEqual(Department.objects.count(), before[0] + 10)

    def test_page_rejects_a_bad_random_seed(self):
        admin = make_user("root@example.com", self.company_a, superuser=True)
        resp = self.login(admin).post(reverse("seeder:index"), {"action": "generate", "models": ["core.Department"], "count": 2,
                                                               "users": 0, "seed": "abc"}, follow=True)
        self.assertContains(resp, "Random seed must be a whole number")
        self.assertFalse(SeedRecord.objects.exists())
        self.login(admin).post(reverse("seeder:index"), {"action": "generate", "models": ["core.Department"], "count": 2,
                                                         "users": 0, "seed": "-5"})
        self.assertEqual(SeedRecord.objects.count(), 2)

    def test_purge_removes_only_seeded_rows(self):
        own = Fabric.objects.create(company=self.company_a, name="Typed by a person")
        with self.captureOnCommitCallbacks(execute=True):
            engine.run(count=3, users=2, seed=1)
        deleted, problems = engine.purge()
        self.assertGreater(deleted, 0)
        self.assertEqual(problems, [])
        self.assertEqual(list(Fabric.global_objects.all()), [own])
        self.assertFalse(Garment.global_objects.exists())
        self.assertEqual(set(Company.objects.all()), {self.company_a, self.company_b})
        self.assertFalse(get_user_model().objects.filter(email__endswith="@example.com", groups__name=engine.ROLE_NAME).exists())
        self.assertFalse(SeedRecord.objects.exists())

    def test_registered_field_values_and_skips_are_used(self):
        register_seeder(Buyer, fields={"name": lambda ctx, n: f"Buyer #{n}"}, count=2)
        register_seeder(Order, skip=True)
        try:
            engine.run(count=3, users=1, activity=False, models_=[Buyer, Order])
        finally:
            unregister_seeder(Buyer)
            unregister_seeder(Order)
        self.assertEqual(Buyer.objects.count(), 2)
        self.assertTrue(all(b.name.startswith("Buyer #") for b in Buyer.objects.all()))

    def test_command_and_page(self):
        out = StringIO()
        call_command("seed_data", "--count", "2", "--users", "1", "--no-activity", "--models", "dummyapp.Buyer,dummyapp.Order", stdout=out)
        self.assertIn("dummyapp.Order", out.getvalue(), out.getvalue())
        self.assertEqual(Order.objects.count(), 2, out.getvalue())
        self.assertEqual(Order.objects.count(), 2)
        admin = make_user("root@example.com", self.company_a, superuser=True)
        clerk = make_user("clerk@example.com", self.company_a)
        self.assertEqual(self.login(clerk).get(reverse("seeder:index")).status_code, 403)
        client = self.login(admin)
        page = client.get(reverse("seeder:index")).content.decode()
        self.assertIn("dummyapp.Garment", page)
        r = client.post(reverse("seeder:index"), {"action": "generate", "models": ["dummyapp.Fabric"], "count": "3", "users": "0"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Fabric.objects.count(), 3)
        client.post(reverse("seeder:index"), {"action": "purge"})
        self.assertEqual(Fabric.objects.count() + Order.objects.count(), 0)

    @override_settings(SEEDER_ENABLED=False)
    def test_disabled_seeder_refuses_to_write(self):
        admin = make_user("root@example.com", self.company_a, superuser=True)
        self.login(admin).post(reverse("seeder:index"), {"action": "generate", "models": ["dummyapp.Fabric"], "count": "3"})
        self.assertFalse(Fabric.objects.exists())
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("seed_data", stdout=StringIO())

    def test_core_never_imports_the_seeder(self):
        """The seeder must stay removable: only the seeder itself (and the optional URL include) may refer to it."""
        from pathlib import Path

        import core

        root = Path(core.__file__).parent
        hits = [str(p) for p in root.rglob("*.py") if "seeder" not in p.relative_to(root).parts and "core.seeder" in p.read_text("utf8")]
        self.assertEqual(hits, [])
