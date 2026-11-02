"""Default navigation, stored as DATA (``MenuItem`` rows) -- seeded into an empty menu after ``migrate`` (``post_migrate``,
``core.signals_handlers.seed_defaults_after_migrate``) and by ``manage.py seed_default_menu`` / ``bootstrap``.
Pure data, no Django imports. After seeding, the rows belong to the administrator: edit, reorder, hide or delete them in the Menu
builder. The seeder only ever *adds* rows that are missing (matched by title + parent); it never edits or deletes.
Every link has a permission except Dashboard; superuser-only rows carry one too, so they can be delegated by unticking the flag.

(title, icon, url_name, path, permission, superuser_only, children)"""

DEFAULT_MENU = [
    ("Dashboard", "home", "core:dashboard", "", "", False, []),
    ("Administration", "settings", "", "", "core.is_staff", False, [         # staff + superusers; flagged entries superusers only
        ("Common", "building", "", "", "core.is_staff", False, [
            ("Companies", "building", "core:companies", "", "core.view_company", True, []),
            ("Departments", "sitemap", "core:departments", "", "core.view_department", False, []),
            ("Designations", "id-badge", "core:designations", "", "core.view_designation", False, []),
        ]),
        ("Users", "users", "core:users", "", "core.view_user", False, []),     # staff manage normal users
        ("Menu Builder", "menu-2", "core:menu_list", "", "core.view_menuitem", True, []),
        ("Approval Builder", "route", "", "", "", True, [
            ("Approval Types", "route", "core:approval_types", "", "core.view_approvaltype", True, []),
            ("Approval Steps", "list-check", "core:approval_steps", "", "core.view_approvalstep", True, []),
            ("Approval Requests", "checklist", "core:approval_requests", "", "core.view_approvalrequest", True, []),
        ]),
        ("Auth", "shield-lock", "", "", "core.is_staff", False, [
            ("Permissions", "key", "core:permissions", "", "core.is_staff", False, []),
            ("Roles", "shield-lock", "core:roles", "", "core.is_staff", False, []),
        ]),
        ("Notification Rules", "bell-cog", "core:notification_rules", "", "core.view_notificationrule", False, []),
        ("Saved Reports", "report", "core:report_definitions", "", "core.view_reportdefinition", True, []),
        ("Search builder", "zoom-code", "core:search_builder", "", "core.build_search", True, []),
        ("Backups", "database-export", "core:backup_list", "", "core.view_backuprecord", False, []),
        ("Archive", "archive", "core:archive_list", "", "core.view_archivejob", False, []),
        ("Export jobs", "file-export", "core:export_jobs", "", "core.view_exportjob", False, []),
        ("Background jobs", "player-play", "core:job_list", "", "core.view_jobrecord", False, []),
        ("Audit log", "history", "core:audit_log", "", "core.is_staff", False, []),     # superusers and staff (pseudo-permission)
    ]),
    ("Approvals", "checklist", "core:approval_queue", "", "core.view_approvalrequest", False, []),
    ("Reports", "report", "core:report_list", "", "core.view_reportdefinition", False, []),
    ("Mail", "mail", "core:mail_inbox", "", "core.view_mailmessage", False, []),
    ("Notifications", "bell", "core:notification_list", "", "core.view_notification", False, []),
]
# "Developer tools › Seed data" is registered in code by the optional seeder app (core/seeder/apps.py) and is drawn after these rows.


def seed(menu_model, tree=DEFAULT_MENU, using=None) -> int:
    """Add every missing default row (works with real or historical models). Returns the number of rows created."""
    created = 0
    rows = menu_model.objects.db_manager(using) if using else menu_model.objects

    def walk(items, parent):
        nonlocal created
        for position, (title, icon, url_name, path, permission, superuser_only, children) in enumerate(items):
            row = rows.filter(title=title, parent=parent).first()
            if row is None:
                row = rows.create(title=title, icon=icon, url_name=url_name, path=path, permission=permission,
                                                superuser_only=superuser_only, parent=parent, position=(position + 1) * 10)
                created += 1
            walk(children, row)

    walk(tree, None)
    return created
