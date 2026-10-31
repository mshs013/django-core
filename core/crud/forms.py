"""Form generation for registered resources: every Django field type, company-scoped relations, validated uploads."""
from __future__ import annotations

import uuid
import os

from django import forms
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models

from core.files.validators import validate_upload

_CUSTOM_FIELD_MAP: dict[type, object] = {}


def register_field_mapping(field_class: type[models.Field], mapping) -> None:
    """Map a custom model field class to a form field class / ``callable(model_field, **kwargs) -> form field`` / widget class."""
    _CUSTOM_FIELD_MAP[field_class] = mapping


class BinaryUploadField(forms.FileField):
    """Editable BinaryField: uploaded file bytes are stored in the column (size-limited by the upload validator)."""

    def to_python(self, data):
        upload = super().to_python(data)
        if upload is None:
            return None
        validate_upload(upload)
        return upload.read()


class PrivateFileInput(forms.ClearableFileInput):
    """Clearable file input for private storage: shows the stored file's name instead of a link, because private files have no URL
    (``ClearableFileInput`` reads ``value.url`` and would crash the form as soon as a file exists)."""

    template_name = "core/widgets/private_file_input.html"
    preview_url = None          # set by the form view / custom forms: a stored image is shown as a thumbnail from this URL

    def is_initial(self, value):
        return bool(value and getattr(value, "name", None))

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        if self.is_initial(value):
            context["widget"]["file_name"] = os.path.basename(value.name)
            context["widget"]["preview_url"] = self.preview_url
        return context


class ValidatedFileField(forms.FileField):
    images_only = False
    widget = PrivateFileInput

    def clean(self, data, initial=None):
        upload = super().clean(data, initial)
        if upload and hasattr(upload, "read") and hasattr(upload, "size") and upload is not initial:
            validate_upload(upload, images_only=self.images_only)
            ext = os.path.splitext(upload.name)[1].lower()
            upload.original_name = upload.name
            upload.name = f"{uuid.uuid4().hex}{ext}"                   # randomised even if the model's upload_to keeps the name
        return upload


class ValidatedImageField(ValidatedFileField, forms.ImageField):
    images_only = True


def formfield_for(db_field: models.Field, **kwargs):
    for klass, mapping in _CUSTOM_FIELD_MAP.items():
        if isinstance(db_field, klass):
            if isinstance(mapping, type) and issubclass(mapping, forms.Field):
                return mapping(**{"required": not db_field.blank, "label": db_field.verbose_name.capitalize(),
                                  "help_text": db_field.help_text})
            if isinstance(mapping, type) and issubclass(mapping, forms.Widget):
                kwargs["widget"] = mapping
                return db_field.formfield(**kwargs)
            return mapping(db_field, **kwargs)
    if isinstance(db_field, models.BinaryField):
        return BinaryUploadField(required=not db_field.blank, label=db_field.verbose_name.capitalize(), help_text=db_field.help_text)
    if isinstance(db_field, models.ImageField):
        kwargs["form_class"] = ValidatedImageField
    elif isinstance(db_field, models.FileField):
        kwargs["form_class"] = ValidatedFileField
    elif isinstance(db_field, models.DateTimeField):
        kwargs.setdefault("widget", forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))
    elif isinstance(db_field, models.DateField):
        kwargs.setdefault("widget", forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    elif isinstance(db_field, models.TimeField):
        kwargs.setdefault("widget", forms.TimeInput(attrs={"type": "time"}))
    elif isinstance(db_field, models.JSONField):
        kwargs.setdefault("widget", forms.Textarea(attrs={"rows": 5, "class": "font-monospace"}))
    return db_field.formfield(**kwargs)


def editable_field_names(model: type[models.Model], exclude: tuple = ()) -> list[str]:
    skip = {"deleted_at", "deleted_by", "company"} | set(exclude)
    names = []
    for f in [*model._meta.concrete_fields, *model._meta.many_to_many]:
        if f.primary_key or f.name in skip or f.auto_created:
            continue
        if isinstance(f, (models.DateField, models.DateTimeField)) and (f.auto_now or f.auto_now_add):
            continue                                                    # read-only in forms
        if not f.editable and not isinstance(f, models.BinaryField):
            continue
        if isinstance(f, models.BinaryField) and not f.editable:
            continue
        if f.one_to_one and f.remote_field.parent_link:
            continue                                                    # multi-table parent_ptr
        names.append(f.name)
    return names


def scope_form(form: forms.BaseForm, company_ids, user=None) -> None:
    """Limit every relation choice to the user's companies (fail closed: no companies -> no choices) and, given ``user``, to the
    rows their object access / user access allows (e.g. only the buyers assigned to them)."""
    from core.permissions.access import restrict

    for name, field in form.fields.items():
        queryset = getattr(field, "queryset", None)
        if queryset is None:
            continue
        if hasattr(queryset, "for_company_ids") and getattr(queryset.model, "company_lookup", None):
            queryset = field.queryset = queryset.for_company_ids(company_ids)
        if user is not None:
            limited = restrict(queryset, user)
            if limited is not queryset:
                field.queryset = limited


def style_form(form: forms.BaseForm) -> None:
    """Tabler/Bootstrap classes; errors get ``is-invalid``."""
    for name, field in form.fields.items():
        widget = field.widget
        if isinstance(widget, (forms.CheckboxInput,)):
            css = "form-check-input"
        elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
            css = "form-select"
        elif isinstance(widget, (forms.RadioSelect, forms.CheckboxSelectMultiple)):
            css = ""
        elif isinstance(widget, forms.FileInput):
            css = "form-control"
        elif isinstance(widget, forms.HiddenInput):
            css = ""
        else:
            css = "form-control"
        if form.is_bound and name in form.errors:
            css += " is-invalid"
        existing = widget.attrs.get("class", "")
        widget.attrs["class"] = f"{existing} {css}".strip()


class ScopedModelForm(forms.ModelForm):
    """Base for generated forms. ``ctx`` carries the acting user and company ids (set by the view / service)."""

    ctx = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        scope_form(self, getattr(self.ctx, "company_ids", ()), getattr(self.ctx, "user", None))
        style_form(self)

    def _get_validation_exclusions(self):
        """Include ``company`` / ``deleted_at`` when they are already set on the instance, so company-scoped (partial) unique
        constraints are validated in the form (a friendly error) instead of failing later as an IntegrityError."""
        exclude = super()._get_validation_exclusions()
        if getattr(self.instance, "company_id", None):
            exclude.discard("company")
        if hasattr(self.instance, "deleted_at"):
            exclude.discard("deleted_at")
        return exclude

    def full_clean(self):
        super().full_clean()
        style_form(self)


class GenericFKMixin:
    """Adds ``<name>__type`` (allowlisted content types) + ``<name>__id`` for GenericForeignKey fields; the target must exist,
    be in the user's company and be viewable."""

    generic_fks: tuple[str, ...] = ()
    generic_allowed = staticmethod(lambda: [])      # replaced by the form factory: callable returning allowed models

    def _init_generic(self):
        allowed = [m for m in self.generic_allowed()]
        cts = ContentType.objects.get_for_models(*allowed).values() if allowed else []
        for name in self.generic_fks:
            self.fields[f"{name}__type"] = forms.ModelChoiceField(queryset=ContentType.objects.filter(pk__in=[c.pk for c in cts]),
                                                                  required=False, label=name.replace("_", " ").capitalize() + " type")
            self.fields[f"{name}__id"] = forms.CharField(required=False, max_length=64, label=name.replace("_", " ").capitalize() + " id")
            current = getattr(self.instance, name, None) if self.instance.pk else None
            if current is not None:
                self.initial[f"{name}__type"] = ContentType.objects.get_for_model(current).pk
                self.initial[f"{name}__id"] = current.pk
        style_form(self)

    def _clean_generic(self):
        from core.permissions import user_has_perm

        for name in self.generic_fks:
            ct, ident = self.cleaned_data.get(f"{name}__type"), self.cleaned_data.get(f"{name}__id")
            if not ct and not ident:
                continue
            if not (ct and ident):
                raise ValidationError({f"{name}__id": "Choose both the type and the id."})
            model = ct.model_class()
            if model is None or model not in self.generic_allowed():
                raise ValidationError({f"{name}__type": "That type is not allowed."})
            qs = model._default_manager.all()
            if hasattr(qs, "for_company_ids") and getattr(model, "company_lookup", None):
                qs = qs.for_company_ids(getattr(self.ctx, "company_ids", ()))
            try:
                target = qs.get(pk=ident)
            except (model.DoesNotExist, ValueError, ValidationError):
                raise ValidationError({f"{name}__id": "No such record."}) from None
            if not user_has_perm(self.ctx.user, f"{model._meta.app_label}.view_{model._meta.model_name}", target):
                raise ValidationError({f"{name}__id": "You may not link to that record."})
            self.cleaned_data[name] = target

    def _apply_generic(self, instance):
        for name in self.generic_fks:
            if name in self.cleaned_data:
                setattr(instance, name, self.cleaned_data[name])


def build_form_class(cfg, ctx, *, fields: list[str] | None = None, model=None, widgets=None, generic_fks=()):
    """A ModelForm class for ``model`` (default: the resource model) with scoping bound to ``ctx``."""
    from django.forms import modelform_factory

    from core.crud.registry import resource_for_model

    model = model or cfg.model
    names = list(fields if fields is not None else (cfg.form_fields or editable_field_names(model, cfg.readonly_fields)))
    for ro in cfg.readonly_fields if model is cfg.model else ():
        if ro in names:
            names.remove(ro)
    base: tuple = (GenericFKMixin, ScopedModelForm) if generic_fks else (ScopedModelForm,)
    attrs = {"ctx": ctx}
    if generic_fks:
        attrs["generic_fks"] = tuple(generic_fks)

        def generic_allowed():
            from core.crud.registry import all_resources

            return [c.model for c in all_resources()]

        attrs["generic_allowed"] = staticmethod(generic_allowed)
    form_base = type("ResourceFormBase", base, attrs)
    if generic_fks:
        def __init__(self, *a, **k):
            ScopedModelForm.__init__(self, *a, **k)
            self._init_generic()

        def clean(self):
            data = super(form_base, self).clean()
            self._clean_generic()
            return self.cleaned_data

        def _post_clean(self):
            super(form_base, self)._post_clean()
            self._apply_generic(self.instance)

        form_base.__init__, form_base.clean, form_base._post_clean = __init__, clean, _post_clean
    return modelform_factory(model, form=form_base, fields=names, formfield_callback=lambda f, **kw: formfield_for(f, **kw),
                             widgets={**m2m_widgets_for(model, names, cfg.m2m_widgets if model is cfg.model else {}),
                                      **(cfg.widgets if model is cfg.model else {}), **(widgets or {})})


def m2m_widgets_for(model, names, chosen: dict) -> dict:
    """Widget per many-to-many field in ``names``: the resource's choice, else ``CORE_M2M_WIDGET`` ("select" | "checkbox")."""
    from core import conf
    from core.crud.widgets import m2m_widget

    default = conf.get("CORE_M2M_WIDGET")
    out = {}
    for name in names:
        try:
            db_field = model._meta.get_field(name)
        except Exception:
            continue
        if getattr(db_field, "many_to_many", False):
            out[name] = m2m_widget(chosen.get(name, default))
    return out


def build_tabs(form, nodes, cfg) -> dict:
    """Split a form into tabs: ``form.tabs`` (custom forms) or ``cfg.tabs``, each ``("Label", ("field", "@inline_slug", ...))``.
    Fields and inlines a tab list does not name go on the first tab. Returns ``{"tabs": [{"label", "fields", "nodes", "errors"}],
    "active": index}``; the first tab with an error opens first. Without tabs it is one unlabelled tab."""
    spec = tuple(getattr(form, "tabs", None) or getattr(cfg, "tabs", ()) or ())
    visible = {bf.name: bf for bf in form.visible_fields()}
    by_slug = {node.inline.slug: node for node in nodes}
    tabs, used_fields, used_nodes = [], set(), set()
    for label, names in spec or (("", ()),):
        fields = [visible[n] for n in names if n in visible]
        tab_nodes = [by_slug[n[1:]] for n in names if n.startswith("@") and n[1:] in by_slug]
        used_fields.update(bf.name for bf in fields)
        used_nodes.update(node.inline.slug for node in tab_nodes)
        tabs.append({"label": label, "fields": fields, "nodes": tab_nodes})
    rest_fields = [bf for name, bf in visible.items() if name not in used_fields]
    rest_nodes = [node for node in nodes if node.inline.slug not in used_nodes]
    tabs[0]["fields"] = rest_fields + tabs[0]["fields"] if spec else rest_fields
    tabs[0]["nodes"] = tabs[0]["nodes"] + rest_nodes
    from core.templatetags.core_tags import tree_has_errors

    for tab in tabs:
        tab["errors"] = any(bf.errors for bf in tab["fields"]) or tree_has_errors(tab["nodes"])
    tabs = [t for t in tabs if t["fields"] or t["nodes"] or not spec]
    active = next((i for i, t in enumerate(tabs) if t["errors"]), 0)
    return {"tabs": tabs, "active": active}
