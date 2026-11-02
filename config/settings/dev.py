"""Developer machine (``DJANGO_DEBUG=1``): error pages, plain HTTP on localhost, e-mail printed to the console, static files
served without ``collectstatic``. Never use it on a server."""
from .base import *  # noqa: F401,F403
from .base import SECRET_KEY, env, env_bool, env_list

DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = SECRET_KEY or "insecure-development-only-key-" + "x" * 40     # never valid in production (check --deploy flags it)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = False

STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
