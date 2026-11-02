from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from core import approvals
from core.mail import service
from core.mail.service import MailError
from core.models import MailFilter, MailLabel, Mailbox, NotificationRule
from core.services import context

from tests.dummyapp.models import Garment

from .base import CoreTestCase, make_user


class MailServiceTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.ann = make_user("ann@example.com", self.company_a)
        self.bob = make_user("bob@example.com", self.company_a)
        self.cat = make_user("cat@example.com", self.company_a)
        self.dan = make_user("dan@example.com", self.company_a)
        self.far = make_user("far@example.com", self.company_b)                     # no shared company with Ann

    def inbox(self, user, name="inbox", **kw):
        return list(service.folder(user, name, **kw))

    def test_send_creates_personal_copies_and_hides_bcc(self):
        m = service.send(self.ann, to=[self.bob], cc=[self.cat], bcc=[self.dan], subject="Hi", body="Hello")
        self.assertEqual([c.message for c in self.inbox(self.bob)], [m])
        self.assertEqual([c.message for c in self.inbox(self.ann, "sent")], [m])
        self.assertEqual(self.inbox(self.ann), [])                                  # the sender's copy is not in her inbox
        bob_view = service.participants(Mailbox.objects.get(owner=self.bob, message=m))
        self.assertEqual((bob_view["to"], bob_view["cc"], bob_view["bcc"]), ([self.bob], [self.cat], []))
        self.assertEqual(service.participants(Mailbox.objects.get(owner=self.ann, message=m))["bcc"], [self.dan])
        self.assertEqual(service.participants(Mailbox.objects.get(owner=self.dan, message=m))["bcc"], [self.dan])

    def test_recipients_must_share_a_company_unless_superuser(self):
        with self.assertRaises(MailError):
            service.send(self.ann, to=[self.far], subject="x")
        root = make_user("root@example.com", self.company_a, superuser=True)
        service.send(root, to=[self.far], subject="from the admin")
        self.assertEqual(len(self.inbox(self.far)), 1)
        with self.assertRaises(MailError):
            service.send(self.ann, to=[], subject="nobody")
        with self.assertRaises(MailError):
            service.send(self.ann, to=[self.bob])                                   # no subject, no text

    def test_state_is_personal_and_bulk_touches_only_own_copies(self):
        m = service.send(self.ann, to=[self.bob, self.cat], subject="Hi", body="x")
        bob_copy = Mailbox.objects.get(owner=self.bob, message=m)
        cat_copy = Mailbox.objects.get(owner=self.cat, message=m)
        self.assertEqual(service.bulk(self.bob, [bob_copy.pk, cat_copy.pk], "star"), 1)
        self.assertEqual(service.bulk(self.bob, [bob_copy.pk], "trash"), 1)
        self.assertEqual(self.inbox(self.bob), [])
        self.assertEqual(len(self.inbox(self.bob, "trash")), 1)
        self.assertEqual(len(self.inbox(self.cat)), 1)                              # Cat is not affected
        self.assertEqual(service.bulk(self.cat, [cat_copy.pk], "delete"), 0)        # delete forever only from the trash
        self.assertEqual(service.bulk(self.bob, [bob_copy.pk], "delete"), 1)
        self.assertEqual(self.inbox(self.bob, "trash"), [])
        self.assertEqual(service.unread_count(self.cat), 1)
        service.bulk(self.cat, [cat_copy.pk], "read")
        self.assertEqual(service.unread_count(self.cat), 0)

    def test_reply_reply_all_and_threads(self):
        m = service.send(self.ann, to=[self.bob], cc=[self.cat], subject="Plan", body="Draft")
        bob_copy = Mailbox.objects.get(owner=self.bob, message=m)
        r1 = service.reply(self.bob, bob_copy, "Looks good")
        self.assertEqual(r1.subject, "Re: Plan")
        self.assertEqual(set(Mailbox.objects.filter(message=r1).exclude(role="from").values_list("owner__email", flat=True)), {"ann@example.com"})
        r2 = service.reply(self.bob, bob_copy, "Adding Cat", everyone=True)
        self.assertEqual(set(Mailbox.objects.filter(message=r2).exclude(role="from").values_list("owner__email", flat=True)),
                         {"ann@example.com", "cat@example.com"})
        ann_copy = Mailbox.objects.get(owner=self.ann, message=m)
        self.assertEqual([c.message for c in service.thread_copies(self.ann, ann_copy)], [m, r1, r2])
        self.assertEqual(r2.thread, m)

    def test_forward_quotes_and_keeps_attachments(self):
        m = service.send(self.ann, to=[self.bob], subject="Spec", body="See file",
                         files=[SimpleUploadedFile("spec.txt", b"size chart", "text/plain")])
        fwd = service.forward(self.bob, Mailbox.objects.get(owner=self.bob, message=m), [self.cat.pk], "FYI")
        self.assertEqual(fwd.subject, "Fwd: Spec")
        self.assertIn("See file", fwd.body)
        self.assertEqual(fwd.attachments.get().file, m.attachments.get().file)

    def test_filters_label_and_skip_inbox_on_delivery_and_apply_now(self):
        label = MailLabel.objects.create(owner=self.bob, name="Orders")
        flt = MailFilter.objects.create(owner=self.bob, name="orders", subject_contains="order", label=label, skip_inbox=True, mark_read=True)
        service.send(self.ann, to=[self.bob], subject="New ORDER 42", body="x")
        service.send(self.ann, to=[self.bob], subject="Lunch?", body="x")
        self.assertEqual([c.message.subject for c in self.inbox(self.bob)], ["Lunch?"])
        filed = self.inbox(self.bob, label=label)
        self.assertEqual([c.message.subject for c in filed], ["New ORDER 42"])
        self.assertTrue(filed[0].is_read)
        flt.subject_contains = "lunch"
        flt.save()
        self.assertEqual(service.apply_filter_to_existing(flt), 1)
        self.assertEqual(self.inbox(self.bob), [])
        empty = MailFilter.objects.create(owner=self.bob, name="matches nothing", label=label)
        self.assertFalse(service._matches(empty, filed[0].message))

    def test_search(self):
        service.send(self.ann, to=[self.bob], subject="Yarn price", body="cotton")
        service.send(self.cat, to=[self.bob], subject="Other", body="polyester")
        self.assertEqual([c.message.subject for c in self.inbox(self.bob, q="cotton")], ["Yarn price"])
        self.assertEqual([c.message.subject for c in self.inbox(self.bob, q="cat@example.com")], ["Other"])

    def test_labels_belong_to_their_owner(self):
        m = service.send(self.ann, to=[self.bob], subject="x", body="y")
        foreign = MailLabel.objects.create(owner=self.cat, name="Cat's")
        with self.assertRaises(MailError):
            service.bulk(self.bob, [Mailbox.objects.get(owner=self.bob, message=m).pk], "label", foreign)


class MailViewTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.ann = make_user("ann@example.com", self.company_a)
        self.bob = make_user("bob@example.com", self.company_a)
        self.eve = make_user("eve@example.com", self.company_a)

    def test_compose_with_attachment_read_and_download(self):
        client = self.login(self.ann)
        r = client.post(reverse("core:mail_compose"), {"to": [str(self.bob.pk)], "subject": "Hello", "body": "<script>alert(1)</script>",
                                                       "files": [SimpleUploadedFile("note.txt", b"hi there", "text/plain")]})
        self.assertEqual(r.status_code, 302)
        bob = self.login(self.bob)
        inbox = bob.get(reverse("core:mail_inbox")).content.decode()
        self.assertIn("Hello", inbox)
        copy = Mailbox.objects.get(owner=self.bob)
        page = bob.get(reverse("core:mail_thread", args=[copy.pk])).content.decode()
        self.assertIn("&lt;script&gt;", page)                                       # plain text, escaped
        self.assertNotIn("<script>alert(1)</script>", page)
        copy.refresh_from_db()
        self.assertTrue(copy.is_read)
        file_id = copy.message.attachments.get().file_id
        self.assertEqual(bob.get(reverse("core:mail_file", args=[file_id])).status_code, 200)
        self.assertEqual(self.login(self.eve).get(reverse("core:mail_file", args=[file_id])).status_code, 404)
        self.assertEqual(self.login(self.eve).get(reverse("core:mail_thread", args=[copy.pk])).status_code, 404)

    def test_compose_errors_and_reply_from_the_page(self):
        client = self.login(self.ann)
        self.assertEqual(client.post(reverse("core:mail_compose"), {"subject": "nobody"}).status_code, 400)
        service.send(self.ann, to=[self.bob], subject="Q", body="?")
        copy = Mailbox.objects.get(owner=self.bob)
        r = self.login(self.bob).post(reverse("core:mail_thread", args=[copy.pk]), {"action": "reply", "body": "Answer"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Mailbox.objects.filter(owner=self.ann, message__subject="Re: Q").exists())

    def test_labels_filters_pages_and_bulk_from_the_list(self):
        client = self.login(self.bob)
        client.post(reverse("core:mail_labels"), {"action": "create", "name": "Yarn", "color": "green"})
        label = MailLabel.objects.get(owner=self.bob, name="Yarn")
        self.assertEqual(client.post(reverse("core:mail_filters"), {"name": "nothing"}).status_code, 400)      # no criteria/action
        r = client.post(reverse("core:mail_filters"), {"name": "yarn", "subject_contains": "yarn", "label": str(label.pk), "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        service.send(self.ann, to=[self.bob], subject="Yarn stock", body="x")
        body = client.get(reverse("core:mail_label", args=[label.pk])).content.decode()
        self.assertIn("Yarn stock", body)
        copy = Mailbox.objects.get(owner=self.bob)
        client.post(reverse("core:mail_inbox"), {"ids": [str(copy.pk)], "action": "trash"})
        self.assertIn("Yarn stock", client.get(reverse("core:mail_folder", args=["trash"])).content.decode())

    def test_top_bar_shows_unread_mail(self):
        service.send(self.ann, to=[self.bob], subject="Ping", body="x")
        client = self.login(self.bob)
        page = client.get(reverse("core:dashboard")).content.decode()
        self.assertIn('id="core-mail-badge"', page)
        self.assertIn(">1<", client.get(reverse("core:mail_badge")).content.decode())


class RuleMailTests(CoreTestCase):
    """Superuser setup: "when the approval completes, mail the related users" is a notification rule with the mail channel."""

    def test_approved_rule_mails_submitter_and_approvers_from_the_system(self):
        approvals.sync_all_chains()
        view = ["dummyapp.view_garment"]
        maker = make_user("maker@example.com", self.company_a, perms=[*view, "dummyapp.add_garment"])
        merch = make_user("merch@example.com", self.company_a, perms=[*view, "dummyapp.change_garment"])
        manager = make_user("mgr@example.com", self.company_a, perms=[*view, "dummyapp.approve_garment"])
        rule = NotificationRule(name="Approved mail", content_type=ContentType.objects.get_for_model(Garment, for_concrete_model=False),
                                event="approval_approved", notify_submitter=True, notify_approvers=True, notify_actor=True,
                                send_in_app=False, send_mail=True, title="Approved: {record}", body="{record} was approved by {actor}.")
        rule.full_clean()
        rule.save()
        g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1")
        req = approvals.submit_for_approval(maker, g)
        approvals.submit_approval_step(merch, req.pk, "approve")
        with self.captureOnCommitCallbacks(execute=True), context.acting_as(manager, "", {self.company_a.pk}, self.company_a):
            approvals.submit_approval_step(manager, req.pk, "approve")
        got = {u.email for u in (maker, merch, manager) if service.folder(u, "inbox").filter(message__subject="Approved: Tee").exists()}
        self.assertEqual(got, {"maker@example.com", "merch@example.com", "mgr@example.com"})
        copy = Mailbox.objects.filter(owner=maker).get()
        self.assertIsNone(copy.message.sender)
        self.assertEqual(copy.message.body, "Tee was approved by mgr@example.com.")
        self.assertTrue(copy.message.url.startswith("/r/garments/"))
        with self.assertRaises(MailError):
            service.reply(maker, copy, "thanks")                                    # nobody to answer
        from core.models import Notification

        self.assertFalse(Notification.objects.filter(title="Approved: Tee", kind__startswith="rule:").exists())   # bell off for this rule

    def test_rule_needs_a_channel(self):
        from django.core.exceptions import ValidationError

        rule = NotificationRule(name="x", content_type=ContentType.objects.get_for_model(Garment, for_concrete_model=False),
                                event="created", send_in_app=False)
        with self.assertRaises(ValidationError):
            rule.full_clean()

    def test_nothing_is_mailed_when_the_change_rolls_back(self):
        from django.db import transaction

        bob = make_user("bob@example.com", self.company_a, perms=["dummyapp.view_garment"])
        rule = NotificationRule.objects.create(name="new", content_type=ContentType.objects.get_for_model(Garment, for_concrete_model=False),
                                               event="created", send_in_app=False, send_mail=True)
        rule.notify_users.add(bob)
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    Garment.objects.create(company=self.company_a, name="Ghost", style_no="G")
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
        self.assertFalse(Mailbox.objects.filter(owner=bob).exists())


class MailQueryCountTests(CoreTestCase):
    def test_inbox_page_queries_do_not_grow_with_the_number_of_messages(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        ann, bob = make_user("ann@example.com", self.company_a), make_user("bob@example.com", self.company_a)
        client = self.login(bob)

        def sql():
            with CaptureQueriesContext(connection) as q:
                client.get(reverse("core:mail_inbox"))
            return [x["sql"] for x in q.captured_queries]

        service.send(ann, to=[bob], subject="one", body="x")
        sql()                                                                       # warm-up: session, caches
        few = sql()
        for i in range(10):
            service.send(ann, to=[bob], subject=f"m{i}", body="x", files=[SimpleUploadedFile(f"f{i}.txt", b"x", "text/plain")])
        many = sql()
        self.assertEqual(len(many), len(few), "\n".join(q[:160] for q in many if q not in few))
