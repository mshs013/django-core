from __future__ import annotations

from core import conf
from core.menu.service import menu_for


def core(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    from core.models import Company
    from core.mail.service import unread_count as mail_unread
    from core.notifications.service import unread_count
    from core.permissions import PAGE_PERMS, active_company_id, user_company_ids, user_has_perm

    member_ids = user_company_ids(user)
    active = active_company_id(user)
    path = request.get_full_path()                     # menu links of saved reports carry ?def=<id>
    tree = menu_for(user, active)
    can_mail, can_notify = user_has_perm(user, PAGE_PERMS["mail"]), user_has_perm(user, PAGE_PERMS["notifications"])
    companies = list(Company.objects.filter(pk__in=member_ids).order_by("name")) if member_ids else []
    return {"core_menu": tree, "core_companies": companies, "core_active_company_id": active,
            "core_active_company": next((c for c in companies if c.pk == active), None), "core_unread": unread_count(user) if can_notify else 0, "core_mail_unread": mail_unread(user) if can_mail else 0,
            "core_can_mail": can_mail, "core_can_notify": can_notify, "core_theme": getattr(getattr(user, "profile", None), "theme", "system"),
            "core_path": path, "core_idle_seconds": conf.get("CORE_IDLE_TIMEOUT_SECONDS")}
