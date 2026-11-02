import threading
import unittest

from django.contrib.auth.models import Permission
from django.db import connection, connections
from django.test import TransactionTestCase

from core import approvals
from core.approvals import ApprovalError
from core.models import Approval, ApprovalRequest, ApprovalType, Notification

from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_company, make_user

MERCH, MANAGER = ["dummyapp.change_garment"], ["dummyapp.approve_garment"]


class ApprovalFlowTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        approvals.sync_all_chains()
        self.maker = make_user("maker@example.com", self.company_a, perms=["dummyapp.view_garment", "dummyapp.add_garment"])
        self.merch = make_user("merch@example.com", self.company_a, perms=["dummyapp.view_garment", *MERCH])
        self.manager = make_user("mgr@example.com", self.company_a, perms=["dummyapp.view_garment", *MANAGER])
        self.g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1")

    def _submit(self):
        return approvals.submit_for_approval(self.maker, self.g)

    def test_full_approval_sets_flags_only_at_final_step(self):
        req = self._submit()
        self.g.refresh_from_db()
        self.assertTrue(self.g.is_confirm)
        self.assertFalse(self.g.is_approval)
        approvals.submit_approval_step(self.merch, req.pk, "approve")
        self.g.refresh_from_db()
        self.assertFalse(self.g.is_approval)
        approvals.submit_approval_step(self.manager, req.pk, "approve")
        self.g.refresh_from_db()
        self.assertTrue(self.g.is_approval)
        self.assertIsNotNone(self.g.approved_at)
        self.assertEqual(approvals.status_for(self.g), "approved")

    def test_wrong_user_cannot_act_and_cannot_skip_steps(self):
        req = self._submit()
        with self.assertRaises(ApprovalError):
            approvals.submit_approval_step(self.manager, req.pk, "approve")      # not the current step
        with self.assertRaises(ApprovalError):
            approvals.submit_approval_step(self.maker, req.pk, "approve")
        self.assertFalse(approvals.can_act(self.manager, req))

    def test_self_approval_is_off(self):
        both = make_user("both@example.com", self.company_a, perms=["dummyapp.view_garment", "dummyapp.add_garment", *MERCH])
        req = approvals.submit_for_approval(both, self.g)
        with self.assertRaises(ApprovalError):
            approvals.submit_approval_step(both, req.pk, "approve")

    def test_other_company_user_cannot_see_or_act(self):
        other = make_user("o@example.com", self.company_b, perms=["dummyapp.view_garment", *MERCH])
        req = self._submit()
        self.assertFalse(approvals.can_view(other, req))
        self.assertFalse(approvals.can_act(other, req))

    def test_reject_returns_to_submitter_and_resubmit_increments_round(self):
        req = self._submit()
        approvals.submit_approval_step(self.merch, req.pk, "reject", comment="fix")
        req.refresh_from_db()
        self.assertEqual(req.status, "rejected")
        self.g.refresh_from_db()
        self.assertFalse(self.g.is_confirm)
        again = approvals.resubmit(self.maker, self.g)
        self.assertEqual(again.round, 2)

    def test_stale_expected_sequence_is_refused(self):
        req = self._submit()
        approvals.submit_approval_step(self.merch, req.pk, "approve")
        with self.assertRaises(ApprovalError):
            approvals.submit_approval_step(self.merch, req.pk, "approve", expected_sequence=1000)

    def test_one_pending_request_per_object(self):
        self._submit()
        with self.assertRaises(ApprovalError):
            self._submit()

    def test_locked_while_pending_and_cancel_resets_flags(self):
        req = self._submit()
        self.assertTrue(approvals.is_locked(self.g))
        approvals.cancel(self.maker, self.g)
        self.g.refresh_from_db()
        self.assertFalse(self.g.is_confirm)
        self.assertFalse(approvals.is_locked(self.g))

    def test_history_is_written_and_notifications_sent_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            req = self._submit()
        self.assertTrue(Approval.objects.filter(request=req, action="submit").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.merch).exists())

    def test_add_step_requires_permission_and_cannot_remove_the_final_step(self):
        req = self._submit()
        with self.assertRaises(ApprovalError):
            approvals.add_step_to_request(self.maker, req.pk, {"code": "x", "name": "X", "permission": ""})
        root = make_user("root@example.com", self.company_a, superuser=True)
        approvals.add_step_to_request(root, req.pk, {"code": "legal", "name": "Legal", "permission": ""})
        req.refresh_from_db()
        self.assertEqual(req.chain[-1]["code"], "manager")

    def test_sync_chain_is_idempotent_and_never_deletes(self):
        n = ApprovalType.objects.count()
        approvals.sync_all_chains()
        self.assertEqual(ApprovalType.objects.count(), n)

    def test_final_step_cannot_be_skippable(self):
        from django.core.exceptions import ImproperlyConfigured

        with self.assertRaises(ImproperlyConfigured):
            approvals.register_chain("bad", "Bad", [approvals.StepSpec("only", "Only", skip_rule="x")])

    def test_runs_without_django_admin_installed(self):
        from django.apps import apps

        self.assertFalse(apps.is_installed("django.contrib.admin"))
        self.assertTrue(ApprovalRequest.objects.count() >= 0)


@unittest.skipUnless(connection.vendor == "postgresql", "row-lock race needs a real multi-connection database (PostgreSQL)")
class ApprovalRaceTests(TransactionTestCase):
    """Two independent DB sessions approve the same step at the same moment: exactly one may win."""

    def test_two_sessions_cannot_both_approve_the_same_step(self):
        company = make_company("R")
        approvals.sync_all_chains()
        maker = make_user("m@example.com", company, perms=["dummyapp.view_garment", "dummyapp.add_garment"])
        m1 = make_user("m1@example.com", company, perms=["dummyapp.view_garment", *MERCH])
        m2 = make_user("m2@example.com", company, perms=["dummyapp.view_garment", *MERCH])
        garment = Garment.objects.create(company=company, name="Race", style_no="R")
        req = approvals.submit_for_approval(maker, garment)
        barrier, results = threading.Barrier(2), []

        def worker(user):
            try:
                barrier.wait(5)
                approvals.submit_approval_step(user, req.pk, "approve", expected_sequence=req.current_sequence)
                results.append("ok")
            except ApprovalError:
                results.append("refused")
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(u,)) for u in (m1, m2)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
        self.assertEqual(sorted(results), ["ok", "refused"])
        self.assertEqual(Approval.objects.filter(request=req, action="approve").count(), 1)


def _errors(response) -> str:
    import re

    return " | ".join(re.findall(r'(?:invalid-feedback|errorlist|alert-danger|text-danger)[^>]*>(.*?)</', response.content.decode(), re.S))[:800]


class AppConfiguredApprovalTests(CoreTestCase):
    """Approvals built by a superuser in the app: any list-page model, step conditions, finish notices, no code."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group
        from django.contrib.contenttypes.models import ContentType

        from core.models import ApprovalStep, ApprovalTypeStep

        from tests.dummyapp.models import Fabric

        self.Fabric = Fabric
        self.admin = make_user("root@example.com", self.company_a, superuser=True)
        self.maker = make_user("fmaker@example.com", self.company_a, perms=["dummyapp.view_fabric", "dummyapp.add_fabric", "dummyapp.change_fabric"])
        self.checker = make_user("fcheck@example.com", self.company_a, perms=["dummyapp.view_fabric"])
        self.boss = make_user("fboss@example.com", self.company_a, perms=["dummyapp.view_fabric"])
        self.watch_group = Group.objects.create(name="store")
        self.watcher = make_user("watch@example.com", self.company_a)
        self.watcher.groups.add(self.watch_group)
        self.outsider = make_user("watch-b@example.com", self.company_b)
        self.atype = ApprovalType.objects.create(code="fabric-ok", name="Fabric approval",
                                                 content_type=ContentType.objects.get_for_model(Fabric, for_concrete_model=False))
        check = ApprovalStep.objects.create(code="fcheck", name="Check")
        final = ApprovalStep.objects.create(code="fboss", name="Boss")
        ApprovalTypeStep.objects.create(approval_type=self.atype, step=check, position=10).approver_users.add(self.checker)
        ApprovalTypeStep.objects.create(approval_type=self.atype, step=final, position=20, is_final=True).approver_users.add(self.boss)
        self.fabric = Fabric.objects.create(company=self.company_a, name="Denim")

    def _approve_all(self):
        req = approvals.submit_for_approval(self.maker, self.fabric)
        approvals.submit_approval_step(self.checker, req.pk, "approve")
        approvals.submit_approval_step(self.boss, req.pk, "approve")
        return req

    def test_any_list_page_model_becomes_approvable_without_code(self):
        self.assertTrue(approvals.has_approval(self.fabric))
        self.assertTrue(approvals.can_submit(self.maker, self.fabric))
        req = self._approve_all()
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")
        self.assertEqual(approvals.status_for(self.fabric), "approved")
        self.assertTrue(approvals.is_locked(self.fabric))

    def test_everyone_told_to_approve_finds_it_in_their_queue(self):
        """Regression: a named approver without the model's view permission was notified but had an empty queue."""
        from django.urls import reverse

        self.checker.user_permissions.clear()
        from core.permissions import PAGE_PERMS

        self.checker.user_permissions.add(Permission.objects.get(content_type__app_label="core", codename=PAGE_PERMS["approvals"].split(".")[1]))
        with self.captureOnCommitCallbacks(execute=True):
            req = approvals.submit_for_approval(self.maker, self.fabric)
        told = set(Notification.objects.filter(title__startswith="Approval needed").values_list("recipient_id", flat=True))
        self.assertEqual(told, {self.checker.pk})
        for uid in told:
            user = type(self.checker).objects.get(pk=uid)
            self.assertIn(req, approvals.get_pending_approvals(user))
            self.assertIn(req.object_repr, self.login(user).get(reverse("core:approval_queue")).content.decode())
        approvals.submit_approval_step(self.checker, req.pk, "approve")

    def test_superuser_is_not_told_to_approve_own_request(self):
        """Regression: with no other approver the superuser fallback notified the submitter, whose queue stays empty."""
        from core.models import ApprovalTypeStep

        for row in ApprovalTypeStep.objects.filter(approval_type=self.atype):
            row.approver_users.clear()
        with self.captureOnCommitCallbacks(execute=True):
            req = approvals.submit_for_approval(self.admin, self.fabric)
        self.assertFalse(Notification.objects.filter(recipient=self.admin, title__startswith="Approval needed").exists())
        self.assertTrue(Notification.objects.filter(recipient=self.admin, title__startswith="No approver").exists())
        self.assertEqual(approvals.get_pending_approvals(self.admin), [])
        other = make_user("root2@example.com", self.company_a, superuser=True)
        self.assertIn(req, approvals.get_pending_approvals(other))

    def test_detail_shows_panel_and_list_shows_status_and_hides_edit_when_locked(self):
        from django.urls import reverse

        client = self.login(self.maker)
        detail = client.get(reverse("core:resource_detail", args=["fabrics", self.fabric.pk])).content.decode()
        self.assertIn("Submit for approval", detail)
        r = client.post(reverse("core:approval_submit", args=["fabrics", self.fabric.pk, "submit"]))
        self.assertEqual(r.status_code, 302)
        listing = client.get(reverse("core:resource_list", args=["fabrics"])).content.decode()
        self.assertIn("<th>Approval</th>", listing)
        self.assertIn("Pending", listing)
        self.assertNotIn(reverse("core:resource_edit", args=["fabrics", self.fabric.pk]), listing)
        self.assertEqual(client.get(reverse("core:resource_edit", args=["fabrics", self.fabric.pk])).status_code, 403)
        detail = client.get(reverse("core:resource_detail", args=["fabrics", self.fabric.pk])).content.decode()
        self.assertIn("Withdraw request", detail)
        client.post(reverse("core:approval_submit", args=["fabrics", self.fabric.pk, "cancel"]))
        self.assertEqual(approvals.status_for(self.fabric), "cancelled")
        self.assertFalse(approvals.is_locked(self.fabric))

    def test_models_without_an_approval_show_no_column(self):
        from django.urls import reverse

        self.atype.is_active = False
        self.atype.save()
        body = self.login(self.maker).get(reverse("core:resource_list", args=["fabrics"])).content.decode()
        self.assertNotIn("<th>Approval</th>", body)
        self.assertFalse(approvals.has_approval(self.fabric))

    def test_company_specific_type_wins(self):
        from django.contrib.contenttypes.models import ContentType

        from core.approvals.registry import bound_type

        own = ApprovalType.objects.create(code="fabric-a", name="Fabric A", company=self.company_a,
                                          content_type=ContentType.objects.get_for_model(self.Fabric, for_concrete_model=False))
        self.assertEqual(bound_type(self.fabric, self.company_a.pk), own)
        self.assertEqual(bound_type(self.fabric, self.company_b.pk), self.atype)

    def test_finish_notices_go_to_configured_users_and_groups_with_email(self):
        from django.core import mail

        self.atype.notify_on, self.atype.notify_email = ApprovalType.NOTIFY_APPROVED, True
        self.atype.save()
        self.atype.notify_groups.add(self.watch_group)
        self.atype.notify_users.add(self.outsider)                     # other company: never told
        with self.captureOnCommitCallbacks(execute=True):
            self._approve_all()
        approved = Notification.objects.filter(title__startswith="Approved")
        self.assertEqual(set(approved.values_list("recipient__email", flat=True)), {"fmaker@example.com", "watch@example.com"})
        self.assertEqual(sorted(addr for m in mail.outbox if m.subject.startswith("Approved") for addr in m.to),
                         ["fmaker@example.com", "watch@example.com"])

    def test_rejection_notice_only_when_asked(self):
        self.atype.notify_on = ApprovalType.NOTIFY_APPROVED
        self.atype.save()
        self.atype.notify_users.add(self.watcher)
        with self.captureOnCommitCallbacks(execute=True):
            req = approvals.submit_for_approval(self.maker, self.fabric)
            approvals.submit_approval_step(self.checker, req.pk, "reject", comment="no")
        self.assertFalse(Notification.objects.filter(recipient=self.watcher).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.maker, title__startswith="Rejected").exists())

    def test_superuser_builds_an_approval_in_the_app_end_to_end(self):
        import json

        from django.contrib.contenttypes.models import ContentType
        from django.urls import reverse

        from core.models import ApprovalStep

        review = ApprovalStep.objects.create(code="g-review", name="Review")
        sign = ApprovalStep.objects.create(code="g-sign", name="Sign off")
        ct = ContentType.objects.get_for_model(Garment, for_concrete_model=False)
        ApprovalType.objects.filter(code="garment").delete()             # no code chain row in the way of the UI one
        data = {"name": "Expensive garments", "content_type": str(ct.pk), "reject_mode": "return_to_submitter",
                "notify_on": "", "is_active": "on",
                "steps-TOTAL_FORMS": "2", "steps-INITIAL_FORMS": "0", "steps-MIN_NUM_FORMS": "0", "steps-MAX_NUM_FORMS": "1000",
                "steps-0-step": str(review.pk), "steps-0-position": "0", "steps-0-is_enabled": "on",
                "steps-0-condition": json.dumps({"field": "price", "op": "gte", "value": "100"}),
                "steps-1-step": str(sign.pk), "steps-1-position": "1", "steps-1-is_enabled": "on", "steps-1-is_final": "on",
                "steps-1-condition": ""}
        r = self.login(self.admin).post(reverse("core:resource_new", args=["approval-types"]), data)
        self.assertEqual(r.status_code, 302, _errors(r))
        atype = ApprovalType.objects.get(name="Expensive garments")
        self.assertEqual(atype.content_type, ct)
        self.assertEqual(atype.type_steps.get(step=review).condition["items"][0]["field"], "price")
        bad = {**data, "name": "Expensive garments 2", "steps-0-condition": json.dumps({"field": "secret_note", "op": "iexact", "value": "x"})}
        r = self.login(self.admin).post(reverse("core:resource_new", args=["approval-types"]), bad)
        self.assertEqual(r.status_code, 400)                             # unknown field refused on the form

    def test_added_steps_are_all_saved_in_the_order_shown(self):
        """Rows added in the modal: blank positions are filled in, a blank extra row is ignored, the shown order is kept."""
        from django.contrib.contenttypes.models import ContentType
        from django.urls import reverse

        from core.models import ApprovalStep

        a, b, c = (ApprovalStep.objects.create(code=f"o-{n}", name=n) for n in ("first", "second", "third"))
        ct = ContentType.objects.get_for_model(Garment, for_concrete_model=False)
        ApprovalType.objects.filter(code="garment").delete()
        data = {"name": "Ordered", "content_type": str(ct.pk), "reject_mode": "return_to_submitter",
                "notify_on": "", "is_active": "on",
                "steps-TOTAL_FORMS": "5", "steps-INITIAL_FORMS": "0", "steps-MIN_NUM_FORMS": "0", "steps-MAX_NUM_FORMS": "1000",
                "steps-0-position": "0", "steps-0-is_enabled": "on", "steps-0-condition": "{}",     # untouched blank row
                "initial-steps-0-condition": "{}",
                "steps-1-step": str(b.pk), "steps-1-position": "", "steps-1-is_enabled": "on", "steps-1-condition": "",
                "steps-2-step": str(c.pk), "steps-2-position": "3", "steps-2-is_enabled": "on", "steps-2-is_final": "on",
                "steps-2-condition": "",
                "steps-4-step": str(a.pk), "steps-4-position": "1", "steps-4-is_enabled": "on", "steps-4-condition": ""}
        r = self.login(self.admin).post(reverse("core:resource_new", args=["approval-types"]), data)
        self.assertEqual(r.status_code, 302, _errors(r))
        steps = list(ApprovalType.objects.get(name="Ordered").type_steps.order_by("position").values_list("step__code", "position"))
        self.assertEqual(steps, [("o-first", 0), ("o-third", 1), ("o-second", 2)])

    def test_step_errors_raise_the_form_alert(self):
        from django.contrib.contenttypes.models import ContentType
        from django.urls import reverse

        ct = ContentType.objects.get_for_model(Garment, for_concrete_model=False)
        data = {"name": "Bad", "content_type": str(ct.pk), "reject_mode": "return_to_submitter",
                "notify_on": "", "is_active": "on",
                "steps-TOTAL_FORMS": "1", "steps-INITIAL_FORMS": "0", "steps-MIN_NUM_FORMS": "0", "steps-MAX_NUM_FORMS": "1000",
                "steps-0-position": "0", "steps-0-is_final": "on", "steps-0-condition": ""}       # filled in, but no step chosen
        r = self.login(self.admin).post(reverse("core:resource_new", args=["approval-types"]), data, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "Please correct the errors below.", status_code=400)

    def test_form_offers_only_list_page_models(self):
        from core.crud.access import Ctx
        from core.crud.registry import get_resource

        from core.crud.approvalform import approval_type_form_class

        cfg = get_resource("approval-types")
        ctx = Ctx(user=self.admin, company_ids=frozenset({self.company_a.pk}), company_id=self.company_a.pk,
                  member_ids=frozenset({self.company_a.pk}))
        form = approval_type_form_class(cfg, ctx)()
        labels = [label for _value, label in form.fields["content_type"].choices]
        self.assertIn("Garments", labels)
        self.assertNotIn("Fabrics", labels)               # no is_confirm / is_approval / approved_at columns => not approvable
        self.assertNotIn("Approval types", labels)


class ApprovalConditionAndSyncTests(CoreTestCase):
    """Step conditions on a code chain, and code sync never overwriting what an administrator changed."""

    def setUp(self):
        super().setUp()
        approvals.sync_all_chains()
        self.atype = ApprovalType.objects.get(code="garment")
        self.maker = make_user("maker@example.com", self.company_a, perms=["dummyapp.view_garment", "dummyapp.add_garment"])
        self.merch = make_user("merch@example.com", self.company_a, perms=["dummyapp.view_garment", *MERCH])
        self.manager = make_user("mgr@example.com", self.company_a, perms=["dummyapp.view_garment", *MANAGER])

    def _merch_step(self):
        return self.atype.type_steps.get(step__code="merch")

    def test_condition_skips_step_for_records_that_do_not_match(self):
        step = self._merch_step()
        step.condition = {"field": "price", "op": "gte", "value": "100"}
        step.full_clean()
        step.save()
        cheap = Garment.objects.create(company=self.company_a, name="Cheap", style_no="C", price=10)
        dear = Garment.objects.create(company=self.company_a, name="Dear", style_no="D", price=500)
        r1 = approvals.submit_for_approval(self.maker, cheap)
        self.assertEqual(r1.step_at(r1.current_sequence)["code"], "manager")
        self.assertTrue(Approval.objects.filter(request=r1, action="skip", step_name="Merchandiser").exists())
        r2 = approvals.submit_for_approval(self.maker, dear)
        self.assertEqual(r2.step_at(r2.current_sequence)["code"], "merch")

    def test_invalid_conditions_are_refused_on_save(self):
        from django.contrib.contenttypes.models import ContentType
        from django.core.exceptions import ValidationError

        self.atype.content_type = ContentType.objects.get_for_model(Garment, for_concrete_model=False)
        self.atype.save()
        step = self._merch_step()
        for bad in ({"field": "secret_note", "op": "iexact", "value": "x"}, {"field": "price", "op": "regex", "value": "."}, "price > 5"):
            step.condition = bad
            with self.assertRaises(ValidationError, msg=str(bad)):
                step.full_clean()
        final = self.atype.type_steps.get(is_final=True)
        final.condition = {"field": "price", "op": "gte", "value": "1"}
        with self.assertRaises(ValidationError):
            final.full_clean()

    def test_sync_never_overwrites_steps_edited_in_the_app(self):
        step = self._merch_step()
        step.permission, step.position = "dummyapp.approve_garment", 5
        step.save()
        approvals.sync_all_chains()
        g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T")
        approvals.submit_for_approval(self.maker, g)
        step.refresh_from_db()
        self.assertEqual((step.permission, step.position), ("dummyapp.approve_garment", 5))
