from __future__ import annotations

import json
from urllib.parse import urlencode

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag(takes_context=True)
def qs_with(context, **overrides):
    """Current query string with some keys replaced (``page=3``) or dropped (value ``None``)."""
    request = context["request"]
    params = request.GET.copy()
    for key, value in overrides.items():
        if value is None:
            params.pop(key, None)
        else:
            params[key] = value
    return params.urlencode()


@register.filter
def dash(value):
    """An em dash for a missing value (None or empty text); 0 and False are real values and stay."""
    return "—" if value is None or value == "" else value


@register.filter
def json_script_data(value):
    """JSON safe for an inline <script type="application/json"> (``<`` escaped)."""
    return mark_safe(json.dumps(value, default=str).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


@register.filter
def json_attr(value):
    """JSON for an HTML *attribute*: returned as plain text so the template's autoescape turns quotes into &quot;."""
    return json.dumps(value, default=str)


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def widget_type(field):
    return field.field.widget.__class__.__name__


@register.simple_tag
def sort_url(request, name, current):
    params = request.GET.copy()
    params["sort"] = f"-{name}" if current == name else name
    params.pop("page", None)
    return params.urlencode()


@register.filter
def is_active_path(entry, path):
    """``path`` is the full path (with the query string). A link with a query string (a saved report: ``...?def=12``) is active
    for exactly that query (extra parameters such as ``&page=2`` allowed); any other link by path prefix, as before."""
    if not entry.url:
        return False
    if "?" in entry.url:
        return path == entry.url or path.startswith(entry.url + "&")
    path = path.split("?", 1)[0]
    return path == entry.url or (entry.url != "/" and path.startswith(entry.url))


@register.filter
def any_active(entry, path):
    return any(is_active_path(c, path) or any_active(c, path) for c in entry.children)


@register.filter
def can_build_search(user) -> bool:
    """Builder (+ Filter, save) controls are shown only to superusers and holders of ``core.build_search``."""
    from core.search.service import can_build

    return can_build(user)


@register.filter
def page_window(page, size: int = 3):
    """Page numbers around the current page for the pager, ``None`` marking a gap: ``[1, 2, 3, None]`` on page 1 of 9,
    ``[None, 4, 5, 6, None]`` on page 5."""
    total, current = page.paginator.num_pages, page.number
    start = max(1, min(current - size // 2, total - size + 1))
    end = min(total, start + size - 1)
    numbers = list(range(start, end + 1))
    return [None] * (start > 1) + numbers + [None] * (end < total)


@register.filter
def cell(value):
    """A report cell: dates and times in the site format, empty for None."""
    import datetime as dt

    from core.formatting import format_value

    if value is None:
        return ""
    if isinstance(value, (dt.date, dt.time)):
        return format_value(value)
    return value


@register.filter
def tree_has_errors(nodes) -> bool:
    """True when any inline formset (or a nested one) has row or formset-level errors."""
    for node in nodes or ():
        fs = node.formset
        if not fs.is_bound:
            continue
        if fs.non_form_errors() or any(f.errors for f in fs.forms):
            return True
        if any(tree_has_errors(getattr(f, "nested", None)) for f in fs.forms):
            return True
    return False


LOGO_INLINE_MAX = 48 * 1024


@register.simple_tag
def company_logo_src(company):
    """``src`` for the top-bar logo. A small logo is embedded as a data: URI so every browser paints it together with the page
    (a separate image request, even from cache, can show up a frame late in Firefox: the logo "blinks" on each navigation).
    Larger logos use the cached ``company_logo`` URL. The encoded logo is cached per company + last save."""
    import base64
    import mimetypes

    from django.core.cache import cache
    from django.urls import reverse

    version = company.updated_at.timestamp() if company.updated_at else 0
    url = f"{reverse('core:company_logo', args=[company.pk])}?v={int(version)}"
    key = f"core:logo-src:{company.pk}:{version}"
    src = cache.get(key)
    if src is None:
        src = url
        ctype = mimetypes.guess_type(company.logo.name)[0] or ""
        if ctype.startswith("image/") and ctype != "image/svg+xml":
            try:
                if company.logo.size <= LOGO_INLINE_MAX:
                    with company.logo.open("rb") as handle:
                        src = f"data:{ctype};base64,{base64.b64encode(handle.read()).decode('ascii')}"
            except (OSError, ValueError):
                pass
        cache.set(key, src, 60 * 60 * 24)
    return src
