"""Reusable Django ``core`` app: identity, companies, permissions, audit, CRUD/search/report/approval engines, menu, notifications,
files, backup and archive. Business apps depend on ``core``; ``core`` never imports business apps."""
default_app_config = "core.apps.CoreConfig"
