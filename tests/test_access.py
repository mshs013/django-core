"""Row-level access (object access, user access), the tabbed user form and several approvers per step."""
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from core.approvals.service import _step_allows
from core.crud.access import Ctx
from core.models import ApprovalStep, ApprovalType, ApprovalTypeStep, ObjectAccess, UserAccess
from core.permissions import invalidate
from core.reports.query.engine import build, validate
from core.reports.query.registry import get_query_model

from tests.dummyapp.models import Buyer, Order

from .base import CoreTestCase, make_user

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "core-modal-content"}
PERMS = ["dummyapp.view_buyer", "dummyapp.view_order", "dummyapp.add_order", "dummyapp.change_order"]


def grant(user, buyer=None, *, all_buyers=False):
    ObjectAccess.objects.create(user=user, content_type=ContentType.objects.get_for_model(Buyer), object_id=None if all_buyers else buyer.pk)
    invalidate()


class ObjectAccessTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.hm, self.zara = (Buyer.objects.create(company=self.company_a, name=n) for n in ("H&M", "Zara"))
        self.boss = make_user("boss@example.com", self.company_a, superuser=True)
        self.clerk = make_user("clerk@example.com", self.company_a, perms=PERMS)
        UserAccess.objects.create(user=self.clerk, target=None)              # user access is not under test here: see everyone's
        self.o1 = Order.objects.create(company=self.company_a, ref="O-HM", buyer=self.hm, created_by=self.boss)
        self.o2 = Order.objects.create(company=self.company_a, ref="O-ZA", buyer=self.zara, created_by=self.boss)

    def page(self, user, slug):
        return self.login(user).get(reverse("core:resource_list", args=[slug])).content.decode()

    def test_no_assignment_means_no_rows(self):
        self.assertNotIn("O-HM", self.page(self.clerk, "orders"))
        self.assertNotIn("H&amp;M", self.page(self.clerk, "buyers"))

    def test_assigned_buyer_limits_lists_forms_and_the_query_builder(self):
        grant(self.clerk, self.hm)
        orders = self.page(self.clerk, "orders")
        self.assertIn("O-HM", orders)
        self.assertNotIn("O-ZA", orders)
        form = self.login(self.clerk).get(reverse("core:resource_new", args=["orders"]), **MODAL).content.decode()
        self.assertIn(f'value="{self.hm.pk}"', form)
        self.assertNotIn(f'value="{self.zara.pk}"', form)                   # only assigned buyers can be chosen
        r = self.login(self.clerk).post(reverse("core:resource_new", args=["orders"]), {"ref": "X", "buyer": self.zara.pk, "qty": 1}, **MODAL)
        self.assertEqual(r.status_code, 400)
        ctx = Ctx(user=self.clerk, company_ids=frozenset({self.company_a.pk}), company_id=self.company_a.pk,
                  member_ids=frozenset({self.company_a.pk}))
        spec = validate(get_query_model("dummyapp.order"), self.clerk, {"columns": [{"path": "ref"}, {"path": "buyer__name"}]})
        self.assertEqual([r["ref"] for r in build(spec, ctx).rows()], ["O-HM"])
        self.assertEqual(self.login(self.clerk).get(reverse("core:resource_detail", args=["orders", self.o2.pk])).status_code, 404)

    def test_all_switch_and_superusers_are_unlimited(self):
        self.assertIn("O-ZA", self.page(self.boss, "orders"))
        grant(self.clerk, all_buyers=True)
        self.assertIn("O-ZA", self.page(self.clerk, "orders"))


class UserAccessTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        self.buyer = Buyer.objects.create(company=self.company_a, name="H&M")
        self.ann, self.bob, self.cid = (make_user(f"{n}@example.com", self.company_a, perms=PERMS) for n in ("ann", "bob", "cid"))
        for u in (self.ann, self.bob, self.cid):
            grant(u, all_buyers=True)
            Order.objects.create(company=self.company_a, ref=f"O-{u.email[:3].upper()}", buyer=self.buyer, created_by=u)

    def test_own_records_plus_assigned_users(self):
        page = lambda u: self.login(u).get(reverse("core:resource_list", args=["orders"])).content.decode()  # noqa: E731
        self.assertEqual([r for r in ("O-ANN", "O-BOB", "O-CID") if r in page(self.ann)], ["O-ANN"])
        UserAccess.objects.create(user=self.ann, target=self.bob)
        invalidate()
        self.assertEqual([r for r in ("O-ANN", "O-BOB", "O-CID") if r in page(self.ann)], ["O-ANN", "O-BOB"])
        UserAccess.objects.create(user=self.ann, target=None)
        invalidate()
        self.assertEqual([r for r in ("O-ANN", "O-BOB", "O-CID") if r in page(self.ann)], ["O-ANN", "O-BOB", "O-CID"])


class UserFormTabTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        from core.models import Department, Designation

        self.root = make_user("root@example.com", self.company_a, superuser=True)
        self.dept = Department.objects.create(name="Ops")
        self.desig = Designation.objects.create(name="Clerk")
        self.hm, self.zara = (Buyer.objects.create(company=self.company_a, name=n) for n in ("H&M", "Zara"))
        self.peer = make_user("peer@example.com", self.company_a)
        self.atype = atype = ApprovalType.objects.create(code="po", name="PO approval")
        self.step = ApprovalTypeStep.objects.create(approval_type=atype, step=ApprovalStep.objects.create(code="po-ok", name="Approve"),
                                                    position=0, is_final=True)
        self.target = make_user("target@example.com", self.company_a)

    def payload(self, **extra):
        return {"email": self.target.email, "full_name": "Target", "default_company": self.company_a.pk, "companies": [self.company_a.pk],
                "dept": self.dept.pk, "designation": self.desig.pk, "is_active": "on", **extra}

    def test_tabs_render_and_every_tab_saves(self):
        client = self.login(self.root)
        url = reverse("core:resource_edit", args=["users", self.target.pk])
        page = client.get(url, **MODAL).content.decode()
        for tab in ("Profile", "Companies", "Roles &amp; permissions", "Object access", "User access", "Approvals"):
            self.assertIn(tab, page)
        r = client.post(url, self.payload(access_dummyapp_buyer=[self.hm.pk], see_users=[self.peer.pk], approver_steps=[self.step.pk],
                                     step_adder_types=[self.atype.pk]), **MODAL)
        self.assertEqual(r.status_code, 204, r.content.decode()[:800])
        ct = ContentType.objects.get_for_model(Buyer)
        self.assertEqual(list(ObjectAccess.objects.filter(user=self.target, content_type=ct).values_list("object_id", flat=True)), [self.hm.pk])
        self.assertEqual(list(UserAccess.objects.filter(user=self.target).values_list("target_id", flat=True)), [self.peer.pk])
        self.assertEqual(list(self.step.approver_users.all()), [self.target])
        self.assertEqual(list(self.atype.step_adders.all()), [self.target])                 # "May add steps on these approval types"
        self.assertRegex(client.get(url, **MODAL).content.decode(), rf'name="step_adder_types" value="{self.atype.pk}"[^>]* checked')
        r = client.post(url, self.payload(access_all_dummyapp_buyer="on", see_all_users="on"), **MODAL)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(list(ObjectAccess.objects.filter(user=self.target).values_list("object_id", flat=True)), [None])
        self.assertFalse(self.step.approver_users.exists())
        self.assertFalse(self.atype.step_adders.exists())

    def test_a_limited_editor_grants_only_their_own_rows_and_never_all(self):
        editor = make_user("hr@example.com", self.company_a, perms=["core.view_user", "core.change_user"])
        grant(editor, self.hm)
        page = self.login(editor).get(reverse("core:resource_edit", args=["users", self.target.pk]), **MODAL).content.decode()
        self.assertIn(f'name="access_dummyapp_buyer" value="{self.hm.pk}"', page)
        self.assertNotIn(f'name="access_dummyapp_buyer" value="{self.zara.pk}"', page)
        self.assertNotIn("access_all_dummyapp_buyer", page)
        self.assertNotIn("see_all_users", page)
        self.assertNotIn('name="approver_steps"', page)                     # needs core.change_approvaltype
        self.assertNotIn('name="step_adder_types"', page)


class SeveralApproversTests(CoreTestCase):
    def test_each_listed_user_may_decide_and_old_requests_still_work(self):
        a, b, c = (make_user(f"{n}@example.com", self.company_a) for n in ("a", "b", "c"))
        step = {"approver_user_ids": [a.pk, b.pk], "permission": ""}
        self.assertTrue(_step_allows(a, step, None, None))
        self.assertTrue(_step_allows(b, step, None, None))
        self.assertFalse(_step_allows(c, step, None, None))
        self.assertTrue(_step_allows(c, {"approver_user_id": c.pk, "permission": ""}, None, None))   # chain frozen before the change

    def test_approvers_tab_on_the_approval_type(self):
        root = make_user("root@example.com", self.company_a, superuser=True)
        a, b = (make_user(f"{n}@example.com", self.company_a) for n in ("a", "b"))
        atype = ApprovalType.objects.create(code="t", name="T")
        step = ApprovalTypeStep.objects.create(approval_type=atype, step=ApprovalStep.objects.create(code="s", name="Sign"), position=0, is_final=True)
        url = reverse("core:resource_edit", args=["approval-types", atype.pk])
        page = self.login(root).get(url, **MODAL).content.decode()
        self.assertIn("Approvers", page)
        self.assertIn(f'name="approvers_{step.pk}"', page)
        data = {"name": "T", "code": "t", "reject_mode": "return_to_submitter", "notify_on": "", "is_active": "on",
                f"approvers_{step.pk}": [a.pk, b.pk],
                "steps-TOTAL_FORMS": "1", "steps-INITIAL_FORMS": "1", "steps-MIN_NUM_FORMS": "0", "steps-MAX_NUM_FORMS": "1000",
                "steps-0-id": step.pk, "steps-0-approval_type": atype.pk, "steps-0-step": step.step_id, "steps-0-position": "0",
                "steps-0-is_final": "on", "steps-0-is_enabled": "on", "steps-0-condition": "{}"}
        r = self.login(root).post(url, data, **MODAL)
        self.assertEqual(r.status_code, 204, r.content.decode()[:800])
        self.assertEqual({u.pk for u in step.approver_users.all()}, {a.pk, b.pk})
