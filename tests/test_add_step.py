"""Add a step to one pending approval request: who may, before / after the waiting step, approvers + creator mailed, a rejection at
the added step returns the record to its creator."""
from django.urls import reverse

from core import approvals
from core.approvals import ApprovalError
from core.models import ApprovalType, Mailbox

from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_user

MERCH, MANAGER = ["dummyapp.change_garment"], ["dummyapp.approve_garment"]


class AddStepTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        approvals.sync_all_chains()
        self.maker = make_user("maker@example.com", self.company_a, perms=["dummyapp.view_garment", "dummyapp.add_garment"])
        self.merch = make_user("merch@example.com", self.company_a, perms=["dummyapp.view_garment", *MERCH])
        self.manager = make_user("mgr@example.com", self.company_a, perms=["dummyapp.view_garment", *MANAGER])
        self.lead = make_user("lead@example.com", self.company_a, perms=["dummyapp.view_garment"])      # may add steps on garments
        self.extra = make_user("extra@example.com", self.company_a)                                    # named approver only
        self.atype = ApprovalType.objects.get(code="garment")
        self.atype.step_adders.add(self.lead)
        self.g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1")
        self.req = approvals.submit_for_approval(self.maker, self.g)

    def add(self, position, user=None, **kw):
        return approvals.add_step_to_request(user or self.lead, self.req.pk, {"name": "QA check", "approver_user_ids": [self.extra.pk]},
                                             position=position, **kw)

    def mails(self, user):
        return list(Mailbox.objects.filter(owner=user, role=Mailbox.TO).values_list("message__subject", flat=True))

    def test_admin_request_view_shows_readable_steps_and_links_to_the_request_page(self):
        req = self.add("after")
        self.assertEqual(req.steps_summary, "[Merchandiser] → QA check (added) → Manager")
        root = make_user("root@example.com", self.company_a, superuser=True)
        html = self.login(root).get(reverse("core:resource_detail", args=["approval-requests", req.pk])).content.decode()
        self.assertIn(f'href="{reverse("core:approval_detail", args=[req.pk])}"', html)
        self.assertIn("QA check (added)", html)
        self.assertNotIn("approver_user_ids", html)                 # no raw chain JSON

    def test_only_listed_people_permission_holders_and_superusers_may_add(self):
        self.assertTrue(approvals.can_add_step(self.lead, self.req))
        self.assertFalse(approvals.can_add_step(self.merch, self.req))
        with self.assertRaises(ApprovalError):
            self.add("after", user=self.merch)
        other_type = ApprovalType.objects.create(name="Other")
        other_type.step_adders.add(self.merch)                      # listed on another type: no rights on this one
        self.assertFalse(approvals.can_add_step(self.merch, self.req))
        holder = make_user("holder@example.com", self.company_a, perms=["dummyapp.view_garment", "core.add_step_to_request"])
        self.assertTrue(approvals.can_add_step(holder, self.req))   # the global permission covers every type
        blind = make_user("blind@example.com", self.company_a)
        self.atype.step_adders.add(blind)
        self.assertFalse(approvals.can_add_step(blind, self.req))   # must be able to see the record

    def test_before_the_waiting_step_goes_to_the_new_approver_first(self):
        merch_seq = self.req.current_sequence
        req = self.add("before")
        added = next(s for s in req.chain if s["kind"] == "extra")
        self.assertEqual(req.current_sequence, added["sequence"])
        self.assertLess(added["sequence"], merch_seq)
        self.assertFalse(approvals.can_act(self.merch, req))
        self.assertTrue(approvals.can_act(self.extra, req))         # named approver: no permission needed
        approvals.submit_approval_step(self.extra, req.pk, "approve")
        req.refresh_from_db()
        self.assertEqual(req.step_at(req.current_sequence)["code"], "merch")   # back to the step that was waiting
        approvals.submit_approval_step(self.merch, req.pk, "approve")
        approvals.submit_approval_step(self.manager, req.pk, "approve")
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")

    def test_after_the_waiting_step_comes_next_then_the_rest(self):
        req = self.add("after")
        self.assertEqual(req.step_at(req.current_sequence)["code"], "merch")
        self.assertFalse(approvals.can_act(self.extra, req))
        approvals.submit_approval_step(self.merch, req.pk, "approve")
        req.refresh_from_db()
        self.assertEqual(req.step_at(req.current_sequence)["name"], "QA check")
        approvals.submit_approval_step(self.extra, req.pk, "approve")
        req.refresh_from_db()
        self.assertEqual(req.step_at(req.current_sequence)["code"], "manager")

    def test_approvers_and_creator_are_mailed(self):
        self.add("after", comment="Needs a QA look")
        self.assertEqual(self.mails(self.extra), ["Approval step added: Tee"])
        self.assertEqual(self.mails(self.maker), ["Approval step added: Tee"])     # no created_by on the row: the submitter

    def test_rejection_at_the_added_step_returns_to_the_creator(self):
        self.atype.reject_mode = ApprovalType.PREVIOUS_STEP          # would normally send it back to the previous step
        self.atype.save()
        req = self.add("after")
        req.reject_mode = ApprovalType.PREVIOUS_STEP
        req.save()
        approvals.submit_approval_step(self.merch, req.pk, "approve")
        approvals.submit_approval_step(self.extra, req.pk, "reject", comment="Stitching is off")
        req.refresh_from_db()
        self.assertEqual(req.status, "rejected")
        self.g.refresh_from_db()
        self.assertFalse(self.g.is_confirm)
        self.assertIn("Rejected: Tee", self.mails(self.maker))

    def test_never_after_the_final_step_and_approvers_must_belong_to_the_company(self):
        approvals.submit_approval_step(self.merch, self.req.pk, "approve")              # final step waiting now
        with self.assertRaises(ApprovalError):
            self.add("after")
        self.add("before")                                                              # before the final step is fine
        outsider = make_user("out@example.com", self.company_b)
        with self.assertRaises(ApprovalError):
            approvals.add_step_to_request(self.lead, self.req.pk, {"name": "X", "approver_user_ids": [outsider.pk]})
        with self.assertRaises(ApprovalError):                                          # the submitter could never decide it
            approvals.add_step_to_request(self.lead, self.req.pk, {"name": "Y", "approver_user_ids": [self.maker.pk]})

    def test_steps_named_alike_get_their_own_codes(self):
        self.add("after")
        req = self.add("after")
        codes = [s["code"] for s in req.chain if s["kind"] == "extra"]
        self.assertEqual(sorted(codes), ["qa-check", "qa-check-2"])

    def test_detail_page_form_and_post(self):
        client = self.login(self.lead)
        page = client.get(reverse("core:approval_detail", args=[self.req.pk])).content.decode()
        self.assertIn('id="add-step"', page)
        self.assertIn(f'value="{self.extra.pk}"', page)
        self.assertNotIn(f'<option value="{self.maker.pk}"', page)                      # not the submitter
        url = reverse("core:approval_detail", args=[self.req.pk])
        client.post(url, {"action": "add_step", "name": "QA", "position": "before", "expected_sequence": self.req.current_sequence})
        self.req.refresh_from_db()
        self.assertEqual(len(self.req.chain), 2)                                        # no approver picked: refused
        client.post(url, {"action": "add_step", "name": "QA", "position": "before", "approvers": [self.extra.pk],
                          "expected_sequence": self.req.current_sequence})
        self.req.refresh_from_db()
        self.assertEqual(self.req.step_at(self.req.current_sequence)["name"], "QA")
        self.assertNotIn('id="add-step"', self.login(self.merch).get(url).content.decode())


class MigrateOnPostgresRegressionTests(CoreTestCase):
    def test_notification_rules_ignore_migration_bookkeeping(self):
        """``migrate`` saves rows of historical models and of django_migrations before core's tables exist. A rule lookup there
        failed inside the migration's transaction and, on PostgreSQL, rolled it back (contenttypes.0001 'OK', then
        'relation django_content_type does not exist')."""
        from django.apps import apps
        from django.db.migrations.recorder import MigrationRecorder
        from django.db.migrations.state import ProjectState

        from core.models import NotificationRule
        from core.notifications import rules

        NotificationRule.objects.create(name="r", content_type_id=1, event="created", title="t")
        rules.invalidate()
        self.assertFalse(rules._watching(MigrationRecorder.Migration))
        historical = ProjectState.from_apps(apps).apps.get_model("dummyapp", "Garment")
        self.assertFalse(rules._watching(historical))
