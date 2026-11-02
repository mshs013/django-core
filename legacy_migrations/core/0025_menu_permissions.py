"""Every menu link except Dashboard gets a permission, and Approvals / Reports / Mail / Notifications now check theirs on the page too.

* Fills the permission of the default menu rows that still have none (rows an administrator already gave a permission are left alone).
* So nobody loses those four pages on upgrade, their permissions are granted to every existing role and to every user without a role."""
from django.db import migrations

ROW_PERMS = {
    "core:approval_queue": "core.view_approvalrequest",
    "core:report_list": "core.view_reportdefinition",
    "core:mail_inbox": "core.view_mailmessage",
    "core:notification_list": "core.view_notification",
    "core:companies": "core.view_company",
    "core:departments": "core.view_department",
    "core:designations": "core.view_designation",
    "core:approval_types": "core.view_approvaltype",
    "core:approval_steps": "core.view_approvalstep",
    "core:approval_requests": "core.view_approvalrequest",
    "core:notification_rules": "core.view_notificationrule",
    "core:report_definitions": "core.view_reportdefinition",
    "core:search_builder": "core.build_search",
    "core:menu_list": "core.view_menuitem",
    "core:backup_list": "core.view_backuprecord",
    "core:archive_list": "core.view_archivejob",
    "core:export_jobs": "core.view_exportjob",
}
PAGE_CODENAMES = ("view_approvalrequest", "view_reportdefinition", "view_mailmessage", "view_notification")


def forwards(apps, schema_editor):
    from django.contrib.auth.management import create_permissions

    MenuItem = apps.get_model("core", "MenuItem")
    for url_name, perm in ROW_PERMS.items():
        MenuItem.objects.filter(url_name=url_name, permission="").update(permission=perm)

    config = apps.get_app_config("core")
    config.models_module = getattr(config, "models_module", None) or True         # historical app configs skip create_permissions otherwise
    create_permissions(config, apps=apps, verbosity=0)
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("core", "User")
    perms = list(Permission.objects.filter(content_type__app_label="core", codename__in=PAGE_CODENAMES))
    for group in Group.objects.all():
        group.permissions.add(*perms)
    for user in User.objects.filter(is_superuser=False, groups__isnull=True):
        user.user_permissions.add(*perms)


def backwards(apps, schema_editor):
    MenuItem = apps.get_model("core", "MenuItem")
    for url_name, perm in ROW_PERMS.items():
        MenuItem.objects.filter(url_name=url_name, permission=perm).update(permission="")


class Migration(migrations.Migration):
    dependencies = [("core", "0024_user_audit_fields"), ("auth", "0012_alter_user_first_name_max_length"),
                    ("contenttypes", "0002_remove_content_type_name")]
    operations = [migrations.RunPython(forwards, backwards)]
