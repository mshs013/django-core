"""Approval step labels + Observation, queue tabs per approval type, job-finished notices, Tabler top-bar badges."""
from django.urls import reverse

from core import approvals
from core.approvals import ApprovalError
from core.models import Approval, ApprovalStep, ApprovalType, ExportJob, MailMessage, Notification

from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_user

class ApprovalQueueTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        approvals.sync_all_chains()
        self.maker = make_user("maker@example.com", self.company_a, perms=["dummyapp.view_garment", "dummyapp.add_garment"])
        self.merch = make_user("merch@example.com", self.company_a, perms=["dummyapp.view_garment", "dummyapp.change_garment"])
        self.g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1")
        self.atype = ApprovalType.objects.get(code="garment")
        self.atype.queue_fields, self.atype.reference_field = "name, style_no", "style_no"
        self.atype.save()
        first = self.atype.type_steps.order_by("position").first().step
        first.pass_label, first.fail_label, first.button_text, first.allow_observation = "Sign", "Return", "Sign", True
        first.save()

    def test_code_is_generated_and_unique(self):
        a = ApprovalStep.objects.create(name="Check it")
        b = ApprovalStep.objects.create(name="Check it")
        self.assertEqual(a.code, "check-it")
        self.assertEqual(b.code, "check-it-2")

    def test_queue_shows_type_tab_columns_and_step_labels(self):
        approvals.submit_for_approval(self.maker, self.g)
        html = self.login(self.merch).get(reverse("core:approval_queue")).content.decode()
        self.assertIn(self.atype.name, html)                   # tab
        self.assertIn("Style no", html.replace("Style No", "Style no"))
        self.assertIn(">Sign<", html.replace("</span>", "<"))  # pass label + button text
        self.assertIn("Return", html)
        self.assertIn('value="observe"', html)

    def test_observation_passes_the_step_and_mails_creator(self):
        req = approvals.submit_for_approval(self.maker, self.g)
        with self.assertRaises(ApprovalError):
            approvals.submit_approval_step(self.merch, req.pk, "observe")              # comment required
        with self.captureOnCommitCallbacks(execute=True):
            approvals.submit_approval_step(self.merch, req.pk, "observe", comment="Fix the FOB")
        req.refresh_from_db()
        self.assertEqual(req.status, "pending")                                  # moved on to the second (manager) step
        self.assertEqual(req.current_sequence, req.chain[1]["sequence"])
        self.assertTrue(req.history.filter(action=Approval.OBSERVE, comment="Fix the FOB").exists())
        mail = MailMessage.objects.get(subject__startswith="Observation for")
        self.assertIn("T1", mail.subject)
        self.assertIn("Observation is Fix the FOB", mail.body)
        self.assertTrue(mail.copies.filter(owner=self.maker).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.maker, title=mail.subject).exists())

    def test_observation_refused_when_step_does_not_offer_it(self):
        ApprovalStep.objects.update(allow_observation=False)
        self.atype.type_steps.update()
        req = approvals.submit_for_approval(self.maker, self.g)  # chain snapshot taken now, without observation
        with self.assertRaises(ApprovalError):
            approvals.submit_approval_step(self.merch, req.pk, "observe", comment="x")

    def test_queue_post_returns_to_queue(self):
        req = approvals.submit_for_approval(self.maker, self.g)
        client = self.login(self.merch)
        back = reverse("core:approval_queue") + f"?type={self.atype.pk}"
        r = client.post(reverse("core:approval_detail", args=[req.pk]),
                             {"action": "approve", "expected_sequence": req.current_sequence, "next": back})
        self.assertRedirects(r, back, fetch_redirect_response=False)


class JobNoticeAndBadgeTests(CoreTestCase):
    def test_finished_job_notifies_its_owner(self):
        from core.tasks.jobs import _tell_owner

        user = make_user("u@example.com", self.company_a)
        job = ExportJob.objects.create(owner=user, target_type="resource", target_key="garments", export_format="csv")
        with self.captureOnCommitCallbacks(execute=True):
            with _tell_owner("core.ExportJob", job.pk, "Export", "export_jobs", owner="owner"):
                ExportJob.objects.filter(pk=job.pk).update(status=ExportJob.DONE)
        note = Notification.objects.get(recipient=user, kind="job")
        self.assertTrue(note.title.startswith("Export completed"))
        self.assertEqual(note.level, "success")

    def test_badges_use_tabler_and_target_themselves(self):
        user = make_user("b@example.com", self.company_a)
        Notification.objects.create(recipient=user, title="Hi")
        html = self.login(user).get(reverse("core:notification_badge")).content.decode()
        self.assertIn('hx-target="this"', html)                 # else it inherits the bell's "next .note-menu" target
        self.assertIn("badge-notification", html)


class WithdrawTests(ApprovalQueueTests):
    def test_only_the_submitter_may_withdraw(self):
        other = make_user("other@example.com", self.company_a, perms=["dummyapp.view_garment", "dummyapp.add_garment"])
        approvals.submit_for_approval(self.maker, self.g)
        self.assertTrue(approvals.can_cancel(self.maker, self.g))
        self.assertFalse(approvals.can_cancel(self.merch, self.g))       # the approver
        self.assertFalse(approvals.can_cancel(other, self.g))            # could submit, but did not
        with self.assertRaises(ApprovalError):
            approvals.cancel(self.merch, self.g)
        approvals.cancel(self.maker, self.g)
        self.assertEqual(approvals.status_for(self.g), "cancelled")
