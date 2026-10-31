"""Contract: ``pyproject.toml`` (pip-installable ``core``) and ``requirements*.txt`` (this project's installs) pin the same versions,
and every template folder inside ``core`` is shipped with the package."""
import re
import tomllib
from pathlib import Path

from django.test import SimpleTestCase

import core

ROOT = Path(core.__file__).parent.parent
CORE_DIR = ROOT / "core"


def _name(spec: str) -> str:
    return re.split(r"[\[=<>;! ]", spec.strip(), maxsplit=1)[0].lower()


def _requirements(filename: str) -> set[str]:
    lines = (line.split("#", 1)[0].strip() for line in (ROOT / filename).read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line and not line.startswith("-r")}


def _bare(spec: str) -> str:
    return spec.split(";", 1)[0].strip()                     # environment markers are pyproject-only


class PackagingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    def test_runtime_pins_match_requirements_txt(self):
        extras = self.project["optional-dependencies"]
        packaged = {_bare(s) for s in self.project["dependencies"] + extras["server"]}
        self.assertEqual(packaged, _requirements("requirements.txt"))

    def test_postgres_pin_matches_requirements_postgresql_txt(self):
        self.assertEqual(set(self.project["optional-dependencies"]["postgres"]), _requirements("requirements-postgresql.txt"))

    def test_mysql_pin_matches_requirements_mysql_txt(self):
        self.assertEqual(set(self.project["optional-dependencies"]["mysql"]), _requirements("requirements-mysql.txt"))

    def test_dev_pins_match_requirements_dev_txt(self):
        self.assertEqual({_bare(s) for s in self.project["optional-dependencies"]["dev"]}, _requirements("requirements-dev.txt"))

    def test_core_dependencies_are_what_core_imports(self):
        names = {_name(s) for s in self.project["dependencies"]}
        self.assertNotIn("psycopg", names)                   # database drivers are extras
        self.assertNotIn("mysqlclient", names)
        self.assertNotIn("gunicorn", names)
        self.assertIn("django", names)

    def test_every_template_folder_is_packaged(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]
        for folder in CORE_DIR.rglob("templates"):
            package = ".".join(folder.parent.relative_to(ROOT).parts)
            self.assertIn("templates/**/*", data.get(package, []), f"{folder.relative_to(ROOT)} is not in package-data")
