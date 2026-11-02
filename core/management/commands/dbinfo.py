"""Report each configured database: engine, where it is, which backup tools were found. ``--live`` also connects and prints the
server version (Django itself refuses servers older than it supports: PostgreSQL 14, MySQL 8.0.11, MariaDB 10.5). Never prints a password."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.backup.adapters.base import UnsupportedEngineError
from core.backup.adapters.registry import get_adapter
from core.backup.adapters.tools import ToolNotFound

TOOLS = {"postgresql": ("pg_dump", "pg_restore"), "mysql": ("mysqldump", "mysql"), "sqlite": ()}


class Command(BaseCommand):
    help = "Show the configured databases, their engine and backup tools (--live: connect and show the server version)."

    def add_arguments(self, parser):
        parser.add_argument("--database", action="append", dest="aliases", help="Alias to report (repeatable; default: all).")
        parser.add_argument("--live", action="store_true", help="Connect and show the server version.")

    def handle(self, *args, aliases=None, live=False, **opts):
        failed = 0
        for alias in aliases or list(settings.DATABASES):
            if alias not in settings.DATABASES:
                raise CommandError(f"Unknown database alias {alias!r}")
            failed += not self._report(alias, live)
        if failed:
            raise CommandError(f"{failed} database(s) with problems")

    def _report(self, alias: str, live: bool) -> bool:
        self.stdout.write(f"[{alias}] {settings.DATABASES[alias].get('ENGINE', '(no ENGINE)')}")
        try:
            adapter = get_adapter(alias)
        except UnsupportedEngineError as exc:
            self.stdout.write(self.style.ERROR(f"  {exc}"))
            return False
        conn = adapter.connection_settings()
        where = conn.name if adapter.engine == "sqlite" else f"{conn.user or '(default user)'}@{conn.host or 'localhost'}:{conn.port or '(default port)'}/{conn.name}"
        self.stdout.write(f"  engine   {adapter.engine}\n  database {where}")
        ok = True
        for kind in TOOLS[adapter.engine]:
            try:
                self.stdout.write(f"  tool     {kind}: {adapter.tool_name(kind)}")
            except ToolNotFound as exc:
                self.stdout.write(self.style.WARNING(f"  tool     {kind}: {exc}"))
        if live:
            try:
                adapter.connection.ensure_connection()
                self.stdout.write(f"  server   {adapter.server_version()}")
            except Exception as exc:  # noqa: BLE001 - report any connection problem without a traceback
                self.stdout.write(self.style.ERROR(f"  server   cannot connect: {conn.redact(exc)}"))
                ok = False
        return ok
