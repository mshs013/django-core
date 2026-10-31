"""Internal mail pages: folders, labels, search, conversation view, compose / reply / forward, labels and filters management."""
from __future__ import annotations

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from core.mail import service
from core.mail.service import MailError
from core.models import MailFilter, MailLabel, Mailbox, StoredFile

from core.permissions import PAGE_PERMS

from .base import CoreMixin, safe_next

FOLDER_TITLES = {"inbox": "Inbox", "starred": "Starred", "sent": "Sent", "all": "All mail", "trash": "Trash"}


class MailMixin(CoreMixin):
    required_perm = PAGE_PERMS["mail"]

    def sidebar(self, **extra) -> dict:
        user = self.request.user
        counts = service.label_counts(user)
        labels = list(MailLabel.objects.filter(owner=user))
        for label in labels:
            label.unread = counts.get(label.pk, 0)
        return {"mail_folders": FOLDER_TITLES, "mail_labels": labels, "mail_unread": service.unread_count(user), **extra}

    def copy_or_404(self, pk) -> Mailbox:
        return get_object_or_404(Mailbox.objects.select_related("message", "message__sender"), pk=pk, owner=self.request.user,
                                 is_deleted=False)


# --------------------------------------------------------------------------------------------------------------- folders
class MailFolder(MailMixin, View):
    def get(self, request, name="inbox", label_id=None):
        label = get_object_or_404(MailLabel, pk=label_id, owner=request.user) if label_id else None
        if label is None and name not in FOLDER_TITLES:
            raise Http404
        q = request.GET.get("q", "")
        page = Paginator(service.folder(request.user, name, label=label, q=q), 50).get_page(request.GET.get("page"))
        return render(request, "core/mail/folder.html", self.sidebar(
            page=page, q=q, folder=None if label else name, label=label, title=label.name if label else FOLDER_TITLES[name]))

    def post(self, request, name="inbox", label_id=None):
        label = None
        if request.POST.get("label"):
            label = MailLabel.objects.filter(pk=request.POST["label"], owner=request.user).first()
        try:
            n = service.bulk(request.user, request.POST.getlist("ids"), request.POST.get("action", ""), label)
            if n:
                messages.success(request, f"{n} conversation{'s' if n != 1 else ''} updated.")
        except MailError as exc:
            messages.error(request, str(exc))
        return redirect(safe_next(request, request.path))


# --------------------------------------------------------------------------------------------------------------- reading
class MailThread(MailMixin, View):
    def get(self, request, pk):
        copy = self.copy_or_404(pk)
        copies = list(service.thread_copies(request.user, copy))
        Mailbox.objects.filter(pk__in=[c.pk for c in copies], is_read=False).update(is_read=True)
        for c in copies:
            c.people = service.participants(c)
        return render(request, "core/mail/thread.html", self.sidebar(
            copy=copy, copies=copies, last=copies[-1] if copies else copy, reply_form=ReplyForm(),
            forward_form=ForwardForm(user=request.user), title=copy.message.subject or "(no subject)"))

    def post(self, request, pk):
        copy = self.copy_or_404(pk)
        action = request.POST.get("action", "")
        try:
            if action in ("reply", "reply_all"):
                form = ReplyForm(request.POST)
                if not form.is_valid():
                    raise MailError("Write a message.")
                message = service.reply(request.user, copy, form.cleaned_data["body"], everyone=action == "reply_all",
                                        files=request.FILES.getlist("files"))
                messages.success(request, "Reply sent.")
                return redirect("core:mail_thread", _my_copy(request.user, message, copy).pk)
            if action == "forward":
                form = ForwardForm(request.POST, user=request.user)
                if not form.is_valid():
                    raise MailError("Choose who to forward it to.")
                service.forward(request.user, copy, [u.pk for u in form.cleaned_data["to"]], form.cleaned_data["body"],
                                files=request.FILES.getlist("files"))
                messages.success(request, "Forwarded.")
                return redirect("core:mail_thread", copy.pk)
            if action in ("label", "unlabel"):
                label = MailLabel.objects.filter(pk=request.POST.get("label"), owner=request.user).first()
                service.bulk(request.user, [copy.pk], action, label)
                return redirect("core:mail_thread", copy.pk)
            service.bulk(request.user, [copy.pk], action)
        except MailError as exc:
            messages.error(request, str(exc))
            return redirect("core:mail_thread", copy.pk)
        if action in ("trash", "archive", "delete"):
            return redirect("core:mail_inbox")
        return redirect("core:mail_thread", copy.pk)


def _my_copy(user, message, fallback):
    return Mailbox.objects.filter(owner=user, message=message).order_by("pk").first() or fallback


class MailAttachmentDownload(MailMixin, View):
    def get(self, request, file_id):
        from core.files.service import PathEscape, safe_path

        stored = StoredFile.objects.filter(pk=file_id).first()
        if stored is None or not service.can_download(request.user, stored):
            raise Http404("Not found")
        try:
            handle = open(safe_path(stored.storage_name), "rb")
        except (PathEscape, FileNotFoundError):
            raise Http404("Not found") from None
        from django.http import FileResponse

        response = FileResponse(handle, as_attachment=True, filename=stored.original_name)
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response


# --------------------------------------------------------------------------------------------------------------- writing
class PeopleField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.full_name} <{obj.email}>" if getattr(obj, "full_name", "") else obj.email


class ComposeForm(forms.Form):
    to = PeopleField(queryset=None)
    cc = PeopleField(queryset=None, required=False)
    bcc = PeopleField(queryset=None, required=False)
    subject = forms.CharField(max_length=200, required=False)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 12}), required=False, max_length=service.MAX_BODY)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        people = service.reachable_users(user)
        for name in ("to", "cc", "bcc"):
            self.fields[name].queryset = people
            self.fields[name].widget.attrs.update({"class": "form-select", "size": 6})
        for name in ("subject", "body"):
            self.fields[name].widget.attrs.update({"class": "form-control"})


class ReplyForm(forms.Form):
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 5, "class": "form-control", "placeholder": "Write a reply…"}),
                           max_length=service.MAX_BODY)


class ForwardForm(forms.Form):
    to = PeopleField(queryset=None)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "Add a note (optional)"}),
                           required=False, max_length=service.MAX_BODY)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to"].queryset = service.reachable_users(user)
        self.fields["to"].widget.attrs.update({"class": "form-select", "size": 5})


class MailCompose(MailMixin, View):
    def get(self, request):
        initial = {"to": [i for i in request.GET.getlist("to") if i.isdigit()], "subject": request.GET.get("subject", "")[:200]}
        return render(request, "core/mail/compose.html", self.sidebar(form=ComposeForm(initial=initial, user=request.user), title="New message"))

    def post(self, request):
        form = ComposeForm(request.POST, user=request.user)
        if form.is_valid():
            data = form.cleaned_data
            try:
                message = service.send(request.user, to=list(data["to"]), cc=list(data["cc"]), bcc=list(data["bcc"]),
                                       subject=data["subject"], body=data["body"], files=request.FILES.getlist("files"))
                messages.success(request, "Message sent.")
                return redirect("core:mail_thread", _my_copy(request.user, message, None).pk)
            except MailError as exc:
                form.add_error(None, str(exc))
        return render(request, "core/mail/compose.html", self.sidebar(form=form, title="New message"), status=400)


# --------------------------------------------------------------------------------------------------------------- labels & filters
class MailLabels(MailMixin, View):
    def get(self, request):
        return render(request, "core/mail/labels.html", self.sidebar(colors=MailLabel.COLORS, title="Labels"))

    def post(self, request):
        action, name = request.POST.get("action", "create"), (request.POST.get("name") or "").strip()[:60]
        color = request.POST.get("color") if request.POST.get("color") in dict(MailLabel.COLORS) else "blue"
        try:
            if action == "create":
                if not name:
                    raise MailError("Give the label a name.")
                MailLabel.objects.create(owner=request.user, name=name, color=color)
            else:
                label = get_object_or_404(MailLabel, pk=request.POST.get("id"), owner=request.user)
                if action == "delete":
                    label.delete()
                elif action == "rename" and name:
                    label.name, label.color = name, color
                    label.save()
        except IntegrityError:
            messages.error(request, "You already have a label with that name.")
        except MailError as exc:
            messages.error(request, str(exc))
        return redirect("core:mail_labels")


class FilterForm(forms.ModelForm):
    class Meta:
        model = MailFilter
        fields = ("name", "from_users", "from_system", "subject_contains", "body_contains", "label", "mark_read", "star", "skip_inbox",
                  "is_active")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["from_users"] = PeopleField(queryset=service.reachable_users(user), required=False, label="From")
        self.fields["label"].queryset = MailLabel.objects.filter(owner=user)
        for name, field in self.fields.items():
            css = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else (
                "form-select" if isinstance(field.widget, (forms.Select, forms.SelectMultiple)) else "form-control")
            field.widget.attrs["class"] = css

    def clean(self):
        data = super().clean()
        if not (data.get("from_users") or data.get("from_system") or data.get("subject_contains") or data.get("body_contains")):
            raise forms.ValidationError("Fill in at least one “matches” criterion.")
        if not (data.get("label") or data.get("mark_read") or data.get("star") or data.get("skip_inbox")):
            raise forms.ValidationError("Choose at least one action.")
        return data


class MailFilters(MailMixin, View):
    def get(self, request, pk=None):
        instance = get_object_or_404(MailFilter, pk=pk, owner=request.user) if pk else None
        return self._render(request, FilterForm(instance=instance, user=request.user), instance)

    def _render(self, request, form, instance, status=200):
        return render(request, "core/mail/filters.html", self.sidebar(
            form=form, editing=instance, filters=MailFilter.objects.filter(owner=request.user).select_related("label"), title="Filters"),
            status=status)

    def post(self, request, pk=None):
        instance = get_object_or_404(MailFilter, pk=pk, owner=request.user) if pk else None
        action = request.POST.get("action", "save")
        if instance is not None and action == "delete":
            instance.delete()
            messages.success(request, "Filter deleted.")
            return redirect("core:mail_filters")
        if instance is not None and action == "apply":
            n = service.apply_filter_to_existing(instance)
            messages.success(request, f"Applied to {n} message{'s' if n != 1 else ''}.")
            return redirect("core:mail_filters")
        form = FilterForm(request.POST, instance=instance, user=request.user)
        if not form.is_valid():
            return self._render(request, form, instance, status=400)
        flt = form.save(commit=False)
        flt.owner = request.user
        flt.save()
        form.save_m2m()
        messages.success(request, "Filter saved. It runs on new mail; use “Apply now” for mail you already have.")
        return redirect("core:mail_filters")


class MailBadge(CoreMixin, View):
    required_perm = PAGE_PERMS["mail"]

    def get(self, request):
        return render(request, "core/mail/_badge.html", {"core_mail_unread": service.unread_count(request.user)})
