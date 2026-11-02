from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import redirect, render
from django.views import View

from core.permissions import user_has_perm
from core.views.base import CoreMixin

from . import engine
from .models import SeedRecord
from .registry import seedable_models


def _int(value, default: int, high: int) -> int:
    try:
        return max(0, min(int(value), high))
    except (TypeError, ValueError):
        return default


class Index(CoreMixin, View):
    """Developer tools › Seed data: pick models and amounts, generate, or remove what the seeder made."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or not user_has_perm(request.user, "seeder.use_seeder"):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        seeded = {row["content_type"]: row["n"] for row in SeedRecord.objects.values("content_type").annotate(n=Count("pk"))}
        rows = []
        for model in engine.ordered(seedable_models()):
            ct = ContentType.objects.get_for_model(model, for_concrete_model=False)
            rows.append({"key": engine.label(model), "name": str(model._meta.verbose_name_plural).capitalize(),
                         "app": model._meta.app_config.verbose_name, "rows": model._base_manager.count(), "seeded": seeded.get(ct.pk, 0)})
        return render(request, "seeder/index.html", {"rows": rows, "batches": engine.batches(), "enabled": engine.enabled(),
                                                     "password": engine.PASSWORD})

    def post(self, request):
        if not engine.enabled():
            messages.error(request, "The seeder is disabled on this server (DEBUG is off).")
            return redirect("seeder:index")
        if request.POST.get("action") == "purge":
            deleted, problems = engine.purge(request.POST.get("batch") or None)
            messages.success(request, f"Deleted {deleted} seeded rows.")
            if problems:
                messages.warning(request, f"{len(problems)} rows were kept because other data still uses them: " + "; ".join(problems[:5]))
            return redirect("seeder:index")
        by_key = {engine.label(m): m for m in seedable_models()}
        chosen = [by_key[k] for k in request.POST.getlist("models") if k in by_key]
        if not chosen:
            messages.error(request, "Tick at least one model.")
            return redirect("seeder:index")
        seed = request.POST.get("seed", "").strip()
        try:
            seed = int(seed) if seed else None
        except ValueError:
            messages.error(request, "Random seed must be a whole number (for example 42), or left empty for new random data.")
            return redirect("seeder:index")
        result = engine.run(models_=chosen, count=_int(request.POST.get("count"), 10, 200),
                            companies=_int(request.POST.get("companies"), 0, 10), users=_int(request.POST.get("users"), 5, 50),
                            activity=bool(request.POST.get("activity")), seed=seed, actor=request.user)
        extra = ", ".join(f"{n} {what}" for what, n in result.activity.items() if n)
        messages.success(request, f"Created {result.total} rows (batch {result.batch}){'; ' + extra if extra else ''}.")
        for name, why in result.skipped.items():
            messages.warning(request, f"Skipped {name}: {why}")
        return redirect("seeder:index")
