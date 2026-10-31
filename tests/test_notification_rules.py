from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core import approvals
from core.models import Notification, NotificationRule
from core.services import context

from tests.dummyapp.models import Fabric, Garment

from .base import CoreTestCase, make_user

VIEW = ["dummyapp.view_garment"]


class NotificationRuleTests(CoreTestCase):
    """Rules a superuser builds in the app send notices to the top-bar bell."""

    def setUp(self):
        super().setUp()
        self.ct = ContentType.objects.get_for_model(Garment, for_concrete_model=False)
        self.maker = make_user("maker@example.com", self.company_a, perms=[*VIEW, "dummyapp.add_garment", "dummyapp.change_garment"])
        self.boss = make_user("boss@example.com", self.company_a, perms=VIEW)
        self.blind = make_user("blind@example.com", self.company_a)                          # cannot view garments
        self.other_co = make_user("other@example.com", self.company_b, perms=VIEW)
        self.group = Group.objects.create(name="planning")
        self.planner = make_user("planner@example.com", self.company_a, perms=VIEW)
        self.planner.groups.add(self.group)

    def rule(self, **kw):
        users = kw.pop("users", [self.boss])
        groups = kw.pop("groups", [])
        rule = NotificationRule(name=kw.pop("name", "Rule"), content_type=self.ct, **kw)
        rule.full_clean()
        rule.save()
        rule.notify_users.set(users)
        rule.notify_groups.set(groups)
        return rule

    def act(self, func, user=None):
        """Run ``func`` as ``user`` (the request context the middleware sets) and deliver on-commit notices."""
        with self.captureOnCommitCallbacks(execute=True), context.acting_as(user or self.maker, "10.0.0.1", {self.company_a.pk}, self.company_a):
            return func()

    def inbox(self, user):
        return list(Notification.objects.filter(recipient=user).values_list("title", flat=True))

    def test_created_rule_notifies_users_and_groups_but_not_the_actor(self):
        self.rule(event="created", title="New {model}: {record} ({status})", users=[self.boss, self.maker], groups=[self.group])
        self.act(lambda: Garment.objects.create(company=self.company_a, name="Tee", style_no="T1"))
        self.assertEqual(self.inbox(self.boss), ["New Garment: Tee (Draft)"])
        self.assertEqual(self.inbox(self.planner), ["New Garment: Tee (Draft)"])
        self.assertEqual(self.inbox(self.maker), [])                                           # the person who did it

    def test_actor_can_opt_in(self):
        self.rule(event="created", users=[self.maker], notify_actor=True)
        self.act(lambda: Garment.objects.create(company=self.company_a, name="Tee", style_no="T1"))
        self.assertEqual(len(self.inbox(self.maker)), 1)

    def test_watched_fields_and_real_changes_only(self):
        g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1", price=5)
        self.rule(event="updated", watch_fields="price, status", title="{record}: price {price}")

        def change(**values):
            obj = Garment.objects.get(pk=g.pk)
            for k, v in values.items():
                setattr(obj, k, v)
            self.act(obj.save)

        change(name="Tee 2")                       # not watched
        change(price=5)                            # same value
        change(price=9)
        self.assertEqual(self.inbox(self.boss), ["Tee 2: price 9.00"])

    def test_condition_limits_the_records(self):
        self.rule(event="created", condition={"field": "status", "op": "eq", "value": "ready"})
        self.act(lambda: Garment.objects.create(company=self.company_a, name="Draft one", style_no="D", status="draft"))
        self.act(lambda: Garment.objects.create(company=self.company_a, name="Ready one", style_no="R", status="ready"))
        self.assertEqual(len(self.inbox(self.boss)), 1)

    def test_recipients_must_see_the_record(self):
        self.rule(event="created", users=[self.boss, self.blind, self.other_co])
        self.act(lambda: Garment.objects.create(company=self.company_a, name="Tee", style_no="T1"))
        self.assertEqual(len(self.inbox(self.boss)), 1)
        self.assertEqual(self.inbox(self.blind), [])                                           # no view permission
        self.assertEqual(self.inbox(self.other_co), [])                                        # other company

    def test_company_specific_rule(self):
        self.rule(event="created", company=self.company_b)
        self.act(lambda: Garment.objects.create(company=self.company_a, name="Tee", style_no="T1"))
        self.assertEqual(self.inbox(self.boss), [])

    def test_soft_delete_links_to_the_list(self):
        g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1")
        self.rule(event="deleted", title="{actor} deleted {record}")
        self.act(g.delete)
        note = Notification.objects.get(recipient=self.boss)
        self.assertEqual(note.title, "maker@example.com deleted Tee")
        self.assertEqual(note.url, reverse("core:resource_list", args=["garments"]))

    def test_approval_events_reach_submitter_and_creator(self):
        approvals.sync_all_chains()
        merch = make_user("merch@example.com", self.company_a, perms=[*VIEW, "dummyapp.change_garment"])
        manager = make_user("mgr@example.com", self.company_a, perms=[*VIEW, "dummyapp.approve_garment"])
        creator = make_user("creator@example.com", self.company_a, perms=VIEW)
        g = self.act(lambda: Garment.objects.create(company=self.company_a, name="Tee", style_no="T1"), user=creator)
        self.rule(event="approval_approved", users=[], notify_creator=True, notify_submitter=True, title="Approved: {record}",
                  send_email=True)
        self.rule(name="sent", event="approval_submitted", users=[self.boss], title="Waiting: {record}")
        req = self.act(lambda: approvals.submit_for_approval(self.maker, g), user=self.maker)
        self.act(lambda: approvals.submit_approval_step(merch, req.pk, "approve"), user=merch)
        self.act(lambda: approvals.submit_approval_step(manager, req.pk, "approve"), user=manager)
        self.assertIn("Waiting: Tee", self.inbox(self.boss))
        self.assertIn("Approved: Tee", [t for t in self.inbox(creator) if t.startswith("Approved: Tee")])
        self.assertEqual(Notification.objects.filter(title="Approved: Tee", kind__startswith="rule:").count(), 2)  # creator + submitter
        self.assertTrue(any(m.subject == "Approved: Tee" for m in mail.outbox))

    def test_models_without_rules_do_no_rule_work(self):
        self.rule(event="created")
        Fabric.objects.create(company=self.company_a, name="warm-up")                         # fills the cache
        with CaptureQueriesContext(connection) as queries:
            self.act(lambda: Fabric.objects.create(company=self.company_a, name="Cotton"))
        self.assertFalse([q for q in queries.captured_queries if "notificationrule" in q["sql"].lower()])

    def test_rule_changes_take_effect_immediately(self):
        rule = self.rule(event="created")
        rule.is_active = False
        rule.save()
        self.act(lambda: Garment.objects.create(company=self.company_a, name="Tee", style_no="T1"))
        self.assertEqual(self.inbox(self.boss), [])

    def test_a_broken_rule_never_breaks_the_save(self):
        rule = self.rule(event="created")
        NotificationRule.objects.filter(pk=rule.pk).update(condition={"op": "AND", "items": [{"field": "gone", "op": "eq", "value": 1}]})
        g = self.act(lambda: Garment.objects.create(company=self.company_a, name="Tee", style_no="T1"))
        self.assertTrue(Garment.objects.filter(pk=g.pk).exists())

    def test_validation(self):
        bad = [({"event": "created", "watch_fields": "price"}, "watch_fields"),
               ({"event": "updated", "watch_fields": "nope"}, "watch_fields"),
               ({"event": "created", "user_field": "name"}, "user_field"),
               ({"event": "created", "notify_submitter": True}, "notify_submitter"),
               ({"event": "created", "condition": {"field": "secret_note", "op": "iexact", "value": "x"}}, "condition"),
               ({"event": "created", "title": "{record} {password} {__class__}"}, "title")]
        for values, field in bad:
            with self.assertRaises(ValidationError, msg=str(values)) as cm:
                NotificationRule(name="x", content_type=self.ct, **values).full_clean()
            self.assertIn(field, cm.exception.message_dict, values)

    def test_placeholders_never_reach_attributes(self):
        from core.notifications.rules import render

        self.assertEqual(render("{record.__class__} {record}", {"record": "Tee"}), "{record.__class__} Tee")

    def test_superuser_builds_a_rule_in_the_app(self):
        admin = make_user("root@example.com", self.company_a, superuser=True)
        client = self.login(admin)
        data = {"name": "Ready garments", "content_type": str(self.ct.pk), "event": "updated", "watch_fields": "status",
                "condition": '{"field": "status", "op": "eq", "value": "ready"}', "notify_users": [str(self.boss.pk)],
                "title": "{record} is {status}", "level": "info", "send_in_app": "on", "is_active": "on"}
        r = client.post(reverse("core:resource_new", args=["notification-rules"]), data)
        self.assertEqual(r.status_code, 302, r.content[:400])
        r = client.post(reverse("core:resource_new", args=["notification-rules"]), {**data, "name": "Bad", "watch_fields": "nope"})
        self.assertEqual(r.status_code, 400)
        body = client.get(reverse("core:resource_new", args=["notification-rules"])).content.decode()
        self.assertIn("Garments", body)
        g = Garment.objects.create(company=self.company_a, name="Tee", style_no="T1")
        g.status = "ready"
        self.act(g.save)
        self.assertEqual(self.inbox(self.boss), ["Tee is Ready"])


class BellTests(CoreTestCase):
    def test_badge_and_dropdown_show_new_notices_and_mark_all_read(self):
        user = make_user("bell@example.com", self.company_a)
        client = self.login(user)
        self.assertNotIn("badge-notification", client.get(reverse("core:notification_badge")).content.decode())
        Notification.objects.create(recipient=user, title="Hello there")
        self.assertIn(">1<", client.get(reverse("core:notification_badge")).content.decode())
        self.assertIn("Hello there", client.get(reverse("core:notification_dropdown")).content.decode())
        client.post(reverse("core:notification_list"), {"action": "mark_all"})
        self.assertFalse(Notification.objects.filter(recipient=user, is_read=False).exists())
        page = client.get(reverse("core:dashboard")).content.decode()
        self.assertIn('hx-trigger="every 60s"', page)


class RuleFormChoiceTests(CoreTestCase):
    def test_applies_to_starts_empty_and_is_sorted_by_name(self):
        from core.crud.approvalform import notification_rule_form_class
        from core.crud.registry import get_resource
        from core.crud.access import Ctx

        root = make_user("root@example.com", self.company_a, superuser=True)
        ids = frozenset({self.company_a.pk})
        ctx = Ctx(user=root, company_ids=ids, company_id=self.company_a.pk, member_ids=ids)
        form = notification_rule_form_class(get_resource("notification-rules"), ctx)()
        names = [label for value, label in form.fields["content_type"].choices]
        self.assertEqual(names[0], "—")
        self.assertEqual(names[1:], sorted(names[1:], key=str.lower))
        self.assertIn("Garments", names)
