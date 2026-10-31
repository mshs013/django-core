"""Saved-report visibility and ownership rules, shared by the report pages and the resource list pages.

* Everyone may build reports and keep them private, or share them with selected users/groups or everybody. Saved reports belong
  to no company: they are offered in every company and always run on the active company's rows.
* Superusers may also mark a report *standard* (``is_system``): it is offered to every user who can use the source, in every
  company. Only superusers change or delete standard reports; other users run them or save their own copy.
* A report never grants access: the source's permission and company scope still apply to every row it returns.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q

from core.models import ReportDefinition
from core.models.reports import PUBLIC, REPORT_VISIBILITY, USERS

VISIBILITY_VALUES = {value for value, _label in REPORT_VISIBILITY}


def visible_definitions(user, member_ids, model=ReportDefinition):
    """Reports ``user`` may open: own, shared with everybody, shared with them or one of their groups, plus standard reports.
    Not company bound (``member_ids`` is kept for callers). ``model``: ``ReportDefinition`` or ``QueryReport``."""
    if not getattr(user, "is_authenticated", False):
        return model.objects.none()
    shared_with_me = Q(visibility=USERS) & (Q(shared_users=user) | Q(shared_groups__in=user.groups.all()))
    ids = model.objects.filter(Q(owner=user) | Q(visibility=PUBLIC) | shared_with_me | Q(is_system=True)).values("pk")
    return model.objects.filter(pk__in=ids)                       # subquery: the M2M joins never duplicate rows


def can_edit(user, definition: ReportDefinition) -> bool:
    if definition.is_system:
        return bool(getattr(user, "is_superuser", False))
    return definition.owner_id == getattr(user, "pk", None)


def share_targets(user):
    """Users (active people sharing a company with ``user``; anyone active for a superuser) and groups a report can be shared with."""
    from core.permissions import user_company_ids

    users = get_user_model().objects.filter(is_active=True)
    if not getattr(user, "is_superuser", False):
        users = users.filter(profile__companies__in=user_company_ids(user)).distinct()
    return users.order_by("email"), Group.objects.order_by("name")


def apply_sharing(user, definition: ReportDefinition, *, visibility: str, user_ids=(), group_ids=(),
                  is_system: bool = False) -> None:
    """Set visibility/sharing on a saved definition (call after ``save()``). Unknown values fall back to private; selected users
    are limited to ``share_targets(user)``; only superusers can make a report standard."""
    definition.visibility = visibility if visibility in VISIBILITY_VALUES else "private"
    definition.is_system = bool(is_system and getattr(user, "is_superuser", False))
    definition.save(update_fields=["visibility", "is_system", "updated_at"])
    if definition.visibility == USERS:
        users, groups = share_targets(user)
        definition.shared_users.set(users.filter(pk__in=[i for i in user_ids if str(i).isdigit()]).exclude(pk=definition.owner_id))
        definition.shared_groups.set(groups.filter(pk__in=[i for i in group_ids if str(i).isdigit()]))
    else:
        definition.shared_users.clear()
        definition.shared_groups.clear()


def definitions_for_model(user, member_ids, model) -> tuple[list, list, list]:
    """(standard reports, my & shared reports, sources) for a model, limited to sources the user may use -- for list pages."""
    from core.crud.access import allowed
    from core.reports.registry import report_sources_for_model

    sources = [s for s in report_sources_for_model(model) if all(allowed(user, p) for p in s.permission)]
    keys = [s.key for s in sources]
    if not keys:
        return [], [], []
    defs = list(visible_definitions(user, member_ids).filter(source_key__in=keys).select_related("owner"))
    return [d for d in defs if d.is_system], [d for d in defs if not d.is_system], sources
