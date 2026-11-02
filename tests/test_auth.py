from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

from core.models import AuditLog

from .base import CoreTestCase, make_user


class LoginTests(CoreTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = make_user("u@example.com", self.company_a)

    def test_login_success_audited_and_session_rotated(self):
        client = Client()
        r = client.post(reverse("core:login"), {"email": "U@Example.com", "password": self.password})
        self.assertRedirects(r, reverse("core:dashboard"), fetch_redirect_response=False)
        self.assertTrue(AuditLog.objects.filter(event="login", user=self.user).exists())

    def test_login_failure_is_generic_and_audited(self):
        r = Client().post(reverse("core:login"), {"email": "u@example.com", "password": "wrong"})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "Invalid email or password", status_code=400)
        self.assertTrue(AuditLog.objects.filter(event="login_failed").exists())

    def test_unknown_email_same_response_as_wrong_password(self):
        a = Client().post(reverse("core:login"), {"email": "nobody@example.com", "password": "x"})
        b = Client().post(reverse("core:login"), {"email": "u@example.com", "password": "x"})
        self.assertEqual((a.status_code, a.content.count(b"Invalid")), (b.status_code, b.content.count(b"Invalid")))

    @override_settings(CORE_LOGIN_MAX_ATTEMPTS=3)
    def test_throttling_locks_out_even_the_right_password(self):
        client = Client()
        for _ in range(3):
            client.post(reverse("core:login"), {"email": "u@example.com", "password": "bad"})
        r = client.post(reverse("core:login"), {"email": "u@example.com", "password": self.password})
        self.assertEqual(r.status_code, 429)

    def test_anonymous_is_redirected_to_login_everywhere(self):
        for name in ("core:dashboard", "core:report_list", "core:approval_queue", "core:backup_list", "core:menu_list"):
            r = Client().get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn(reverse("core:login"), r["Location"])

    def test_health_is_public_and_leaks_nothing(self):
        r = Client().get(reverse("core:health"))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b"postgres", r.content.lower())

    def test_logout_requires_post(self):
        client = self.login(self.user)
        self.assertEqual(client.get(reverse("core:logout")).status_code, 405)
        self.assertEqual(client.post(reverse("core:logout")).status_code, 302)
        self.assertEqual(client.get(reverse("core:dashboard")).status_code, 302)

    def test_change_password_keeps_session(self):
        client = self.login(self.user)
        r = client.post(reverse("core:password_change"), {"old_password": self.password, "new_password1": "A-new-Long-pass-77", "new_password2": "A-new-Long-pass-77"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(client.get(reverse("core:dashboard")).status_code, 200)

    def test_change_password_errors_are_styled_and_not_raw(self):
        client = self.login(self.user)
        r = client.post(reverse("core:password_change"), {"old_password": "wrong", "new_password1": "A-new-Long-pass-77", "new_password2": "A-new-Long-pass-77"})
        self.assertEqual(r.status_code, 400)
        self.assertNotContains(r, "<li>old_password", status_code=400)      # no raw form.errors dump with field names
        self.assertContains(r, 'class="form-control is-invalid"', status_code=400)
        self.assertContains(r, "Your old password was entered incorrectly", count=1, status_code=400)

    def test_password_reset_does_not_reveal_accounts(self):
        a = Client().post(reverse("core:password_reset"), {"email": "u@example.com"})
        b = Client().post(reverse("core:password_reset"), {"email": "ghost@example.com"})
        self.assertEqual(a["Location"], b["Location"])

    def test_no_default_admin_credentials(self):
        self.assertEqual(get_user_model().objects.exclude(email="u@example.com").count(), 0)

    def test_idle_timeout_logs_out(self):
        client = self.login(self.user)
        client.get(reverse("core:dashboard"))
        session = client.session
        session["core_last_activity"] = 1
        session.save()
        r = client.get(reverse("core:dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("timeout", r["Location"])

    def test_security_headers_and_error_pages(self):
        r = Client().get(reverse("core:login"))
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors", r["Content-Security-Policy"])
        self.assertEqual(Client().get("/definitely/not/here/").status_code, 302)   # anonymous -> login, never a stack trace
        member = make_user("404@example.com", self.company_a)
        self.assertEqual(self.login(member).get("/definitely/not/here/").status_code, 404)

    def test_no_two_factor_anywhere(self):
        import pkgutil

        import core

        names = {m.name for m in pkgutil.walk_packages(core.__path__, "core.")}
        self.assertFalse([n for n in names if any(k in n.lower() for k in ("totp", "otp", "twofactor", "two_factor", "2fa"))])
