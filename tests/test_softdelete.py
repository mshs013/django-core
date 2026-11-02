from core.models import Company, Department
from core.signals import post_restore, post_soft_delete

from tests.dummyapp.models import Fabric, Garment

from .base import CoreTestCase


class SoftDeleteTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1")

    def test_delete_hides_row_but_keeps_it(self):
        self.g.delete()
        self.assertFalse(Garment.objects.filter(pk=self.g.pk).exists())
        self.assertTrue(Garment.deleted_objects.filter(pk=self.g.pk).exists())
        self.assertTrue(Garment.global_objects.filter(pk=self.g.pk).exists())

    def test_queryset_delete_soft_deletes_each_row_and_sends_signals(self):
        seen = []
        post_soft_delete.connect(lambda sender, instance, **k: seen.append(instance.pk), sender=Garment, weak=False)
        Garment.objects.create(company=self.company_a, name="Two", style_no="T2")
        Garment.objects.all().delete()
        self.assertEqual(Garment.objects.count(), 0)
        self.assertEqual(Garment.global_objects.count(), 2)
        self.assertEqual(len(seen), 2)

    def test_restore(self):
        self.g.delete()
        row = Garment.deleted_objects.get(pk=self.g.pk)
        row.restore()
        self.assertTrue(Garment.objects.filter(pk=self.g.pk).exists())

    def test_hard_delete_is_explicit(self):
        self.g.hard_delete()
        self.assertFalse(Garment.global_objects.filter(pk=self.g.pk).exists())

    def test_default_manager_is_alive_only_for_every_soft_model(self):
        for model in (Company, Department, Garment, Fabric):
            self.assertEqual(model._default_manager.__class__.__name__.lstrip("_"), model.objects.__class__.__name__.lstrip("_"))
            self.assertEqual(model._default_manager.filter(deleted_at__isnull=False).count(), 0)

    def test_unique_name_may_be_reused_after_soft_delete(self):
        d = Department.objects.create(name="Cutting")
        d.delete()
        Department.objects.create(name="Cutting")

    def test_system_check_passes_for_every_registered_soft_model(self):
        from core.checks import soft_delete_managers

        self.assertEqual(soft_delete_managers(None), [])
