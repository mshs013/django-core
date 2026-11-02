"""Modal-first UI helpers. Create / edit / view / delete open in the global Bootstrap modal (``#core-modal``): HTMX loads a partial into
``#core-modal-content``; a successful POST answers ``204`` + an ``HX-Trigger`` event that closes the modal, shows a toast and refreshes the
table; validation errors re-render the partial (HTTP 400) so field errors appear inside the modal. The classic full-page views stay
available (non-HTMX requests, tests, API-style use) but no list page links to them."""
from __future__ import annotations

import json

from django.http import HttpResponse

MODAL_TARGET = "core-modal-content"


def is_modal(request) -> bool:
    return request.headers.get("HX-Request") == "true" and request.headers.get("HX-Target") == MODAL_TARGET


def modal_done(message: str = "", level: str = "success", *, refresh: bool = True) -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"core:modal-done": {"message": message, "level": level, "refresh": refresh}})
    return response
