"""Mail and notifications work in every company but say which company they come from."""
from django.urls import reverse

from core.mail import service
from core.models import Mailbox
from core.notifications.service import notify

from .base import CoreTestCase, make_user


class MailCompanyTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.ann = make_user("ann@example.com", self.company_a)
        self.ann.profile.companies.add(self.company_b)
        self.bob = make_user("bob@example.com", self.company_a)
        self.bob.profile.companies.add(self.company_b)

    def switch(self, user, company):
        user.profile.active_company = company
        user.profile.save()

    def test_message_is_stamped_with_the_senders_active_company(self):
        self.switch(self.ann, self.company_b)
        m = service.send(self.ann, to=[self.bob], subject="From B")
        self.assertEqual(m.company_id, self.company_b.pk)
        self.assertEqual(service.send_system([self.bob], "Rule", company=self.company_a).company_id, self.company_a.pk)
        self.assertIsNone(service.send_system([self.bob], "No record").company_id)

    def test_inbox_and_thread_show_the_company_in_every_company(self):
        self.switch(self.ann, self.company_b)
        m = service.send(self.ann, to=[self.bob], subject="Hello from B")
        client = self.login(self.bob)                                   # Bob works in company A
        inbox = client.get(reverse("core:mail_inbox")).content.decode()
        self.assertIn("Hello from B", inbox)
        self.assertIn('title="From Company B">B</span>', inbox)
        copy = Mailbox.objects.get(owner=self.bob, message=m)
        self.assertIn('title="From Company B">B</span>', client.get(reverse("core:mail_thread", args=[copy.pk])).content.decode())


class NotificationCompanyTests(CoreTestCase):
    def test_list_and_dropdown_show_the_company_whatever_the_active_one(self):
        user = make_user("n@example.com", self.company_a)
        user.profile.companies.add(self.company_b)
        with self.captureOnCommitCallbacks(execute=True):
            notify([user], "Order approved", company=self.company_b)
            notify([user], "System notice")
        client = self.login(user)
        for name in ("core:notification_list", "core:notification_dropdown"):
            body = client.get(reverse(name)).content.decode()
            self.assertIn("Order approved", body)
            self.assertIn("System notice", body)
            self.assertEqual(body.count('title="From Company B">B</span>'), 1)
