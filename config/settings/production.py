"""Production settings: everything in ``base`` (secure cookies, HTTPS redirect, HSTS, hashed static files, SMTP e-mail) and a
real secret key. Used by ``manage.py`` / ``wsgi.py`` / ``asgi.py`` whenever ``DJANGO_DEBUG`` is off."""
from .base import *  # noqa: F401,F403
from .base import SECRET_KEY

if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set (generate one with `python -c 'import secrets;print(secrets.token_urlsafe(64))'`).")
