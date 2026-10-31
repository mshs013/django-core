"""Email-login User and Profile. No Django admin dependency; no second factor."""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, Group, PermissionsMixin
from django.db import models
from django.utils import timezone

from core.files.storage import private_storage

from .base import AuditTrackedModel, CreatedInfoModel, DeletedManager, GlobalManager, SoftDeleteManager, SoftDeleteModel, UpdatedInfoModel


# Which companies a report / export covers (see core.reports.scope).
REPORT_SCOPES = [("active", "Active company"), ("all", "All my companies"), ("chosen", "Chosen companies")]


class UserManager(BaseUserManager, SoftDeleteManager):
    """Alive users only (soft-deleted accounts cannot log in and drop out of every list)."""

    use_in_migrations = True

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not password:
            raise ValueError("A superuser needs a password.")
        return self._create(email, password, **extra)

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)


class User(AbstractBaseUser, PermissionsMixin, AuditTrackedModel, CreatedInfoModel, UpdatedInfoModel, SoftDeleteModel):
    email = models.EmailField("email address", unique=True)
    full_name = models.CharField("full name", max_length=150, blank=True)
    is_active = models.BooleanField("active", default=True)
    is_staff = models.BooleanField("staff", default=False, help_text="Reserved for operators; grants no permissions by itself.")
    date_joined = models.DateTimeField("date joined", default=timezone.now)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []
    audit_exclude_fields = ("password", "last_login")
    objects = UserManager()                  # FIRST => default manager => never exposes deleted accounts
    deleted_objects = DeletedManager()
    global_objects = GlobalManager()

    class Meta:
        swappable = "AUTH_USER_MODEL"
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ("email",)

    def __str__(self) -> str:
        return self.full_name or self.email

    def get_full_name(self) -> str:
        return self.full_name or self.email

    def get_short_name(self) -> str:
        return (self.full_name or self.email).split(" ")[0]

    def clean(self):
        super().clean()
        self.email = (self.email or "").strip().lower()

    # read-only conveniences for lists / detail pages (the profile row always exists, see core.services.org.ensure_profile)
    def _profile_attr(self, name: str, default=""):
        profile = getattr(self, "profile", None) if self.pk else None
        return getattr(profile, name, default) if profile is not None else default

    @property
    def department_name(self) -> str:
        return str(self._profile_attr("dept") or "")

    @property
    def designation_name(self) -> str:
        return str(self._profile_attr("designation") or "")

    @property
    def default_company_name(self) -> str:
        return str(self._profile_attr("default_company") or "")

    @property
    def official_id(self) -> str:
        return self._profile_attr("official_id")

    @property
    def contact_no(self) -> str:
        return self._profile_attr("contact_no")

    @property
    def joining_date(self):
        return self._profile_attr("joining_date", None)

    def _profile_thumb(self, name: str):
        """Thumbnail of the profile photo / signature for list pages (served by the private ``core:user_file`` view)."""
        from django.urls import reverse
        from django.utils.html import format_html

        if not self._profile_attr(name, None):
            return ""
        url = reverse("core:user_file", args=[self.pk, name])
        return format_html('<a href="{}" target="_blank" rel="noopener"><img src="{}" alt="" class="core-thumb" loading="lazy"></a>', url, url)

    @property
    def photo(self):
        return self._profile_thumb("user_img")

    photo.fget.short_description = "Photo"

    @property
    def signature(self):
        return self._profile_thumb("user_sign")

    signature.fget.short_description = "Signature"


class Profile(CreatedInfoModel, AuditTrackedModel):
    """Per-user settings: org placement (department, designation, default company), company membership, contact details, images.
    Audited; the active company and theme are left out (they change on every company switch / theme toggle)."""

    audit_exclude_fields = ("active_company", "theme")

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    dept = models.ForeignKey("core.Department", on_delete=models.PROTECT, related_name="profiles", verbose_name="department")
    designation = models.ForeignKey("core.Designation", on_delete=models.PROTECT, related_name="profiles")
    default_company = models.ForeignKey("core.Company", on_delete=models.PROTECT, related_name="default_profiles")
    active_company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    companies = models.ManyToManyField("core.Company", blank=True, related_name="member_profiles", verbose_name="member of")
    official_id = models.CharField("official ID", max_length=50, blank=True, default="")
    joining_date = models.DateField(null=True, blank=True)
    contact_no = models.CharField("contact no.", max_length=50, blank=True, default="")
    user_img = models.ImageField("photo", upload_to="users/img/", storage=private_storage, null=True, blank=True)
    user_sign = models.ImageField("signature", upload_to="users/sign/", storage=private_storage, null=True, blank=True)
    theme = models.CharField(max_length=6, choices=[("system", "System"), ("light", "Light"), ("dark", "Dark")], default="system")
    report_all_companies = models.BooleanField("report shows all companies", default=False,
                                               help_text="Off: reports and exports use the active company only.")
    report_company_scope = models.CharField("report companies", max_length=10, choices=REPORT_SCOPES, default="active",
                                            help_text="Which companies reports and exports cover by default (each run can change it).")
    report_companies = models.ManyToManyField("core.Company", blank=True, related_name="+", verbose_name="chosen report companies",
                                              help_text="Used when the report companies are “Chosen companies”.")

    class Meta:
        verbose_name = "profile"

    def __str__(self) -> str:
        return f"Profile of {self.user}"

    def member_company_ids(self) -> set:
        return set(self.companies.values_list("pk", flat=True))


class Role(Group, AuditTrackedModel):
    """A Django auth group under the name users see ("role"): a named bundle of permissions given to its members. Proxy model,
    so it is the same table as ``auth.Group`` and works everywhere groups do (approver group, sharing, notifications)."""

    class Meta:
        proxy = True
        verbose_name = "role"
        verbose_name_plural = "roles"
        ordering = ("name",)

    @property
    def member_count(self) -> int:
        return self.user_set.count()

    member_count.fget.short_description = "Members"

    @property
    def permission_count(self) -> int:
        return self.permissions.count()

    permission_count.fget.short_description = "Permissions"
