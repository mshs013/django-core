"""Constraints that differ per database engine."""
from django.db import models


class CheckConstraintSkippedOnMySQL(models.CheckConstraint):
    """A CHECK constraint that is not created on MySQL / MariaDB, which refuse CHECKs on AUTO_INCREMENT columns (e.g.
    ``parent != id``). PostgreSQL and SQLite get the normal constraint; on MySQL the model's ``clean()`` / ``validate_constraints``
    still enforce the rule (``CheckConstraint.validate`` is evaluated by Django, not by the CHECK)."""

    @staticmethod
    def _skipped(schema_editor) -> bool:
        return schema_editor.connection.vendor == "mysql"

    def constraint_sql(self, model, schema_editor):
        return None if self._skipped(schema_editor) else super().constraint_sql(model, schema_editor)

    def create_sql(self, model, schema_editor):
        return None if self._skipped(schema_editor) else super().create_sql(model, schema_editor)

    def remove_sql(self, model, schema_editor):
        return None if self._skipped(schema_editor) else super().remove_sql(model, schema_editor)
