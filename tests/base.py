"""Shared fixtures for the Django-dependent tests."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase

from core.models import Company
from core.permissions import cache


def make_company(code="A", name=None):
    return Company.objects.create(abbreviation=code, name=name or f"Company {code}", address="Test address")


def make_user(email, company=None, *, superuser=False, perms=(), password="pw-for-tests-123", page_perms=True):
    User = get_user_model()
    user = User.objects.create_superuser(email, password) if superuser else User.objects.create_user(email, password)
    if company is not None:
        profile = user.profile
        profile.companies.add(company)
        profile.default_company = profile.active_company = company
        profile.save()
    if page_perms and not superuser:              # everyday pages (Mail, Notifications, Approvals, Reports), as the upgrade grants them
        from core.permissions import PAGE_PERMS

        perms = (*PAGE_PERMS.values(), *perms)
    for perm in perms:
        app, code = perm.split(".")
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
    cache.bump()
    return user


class CoreTestCase(TestCase):
    password = "pw-for-tests-123"

    def setUp(self):
        self.company_a, self.company_b = make_company("A"), make_company("B")

    def login(self, user):
        client = Client()
        assert client.login(email=user.email, password=self.password)
        return client
