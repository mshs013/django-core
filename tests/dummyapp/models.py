from django.db import models

from core.models import AuditedModel, CompanyScopedModel, CompanyScopedSoftDeleteModel, CreatedInfoModel


class Fabric(CompanyScopedSoftDeleteModel):
    name = models.CharField(max_length=80)

    def __str__(self):
        return self.name


class Garment(CompanyScopedSoftDeleteModel, AuditedModel):
    approval_target = "garment"
    STATUS = [("draft", "Draft"), ("ready", "Ready")]
    name = models.CharField(max_length=80)
    style_no = models.CharField(max_length=30)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS, default="draft")
    ordered_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    fabric = models.ForeignKey(Fabric, null=True, blank=True, on_delete=models.PROTECT, related_name="garments")
    secret_note = models.CharField(max_length=80, blank=True)

    class Meta:
        permissions = [("approve_garment", "Can approve garments")]

    def __str__(self):
        return self.name


class GarmentLine(models.Model):
    garment = models.ForeignKey(Garment, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=80)
    qty = models.PositiveIntegerField(default=1)
    position = models.PositiveIntegerField(default=0)


class LineOption(models.Model):
    line = models.ForeignKey(GarmentLine, on_delete=models.CASCADE, related_name="options")
    label = models.CharField(max_length=40)
    position = models.PositiveIntegerField(default=0)


class OldRecord(models.Model):
    """Archive target (allow-listed in test settings): has a creation timestamp and no inbound references."""
    created_at = models.DateTimeField()
    payload = models.CharField(max_length=40, blank=True)


class Buyer(CompanyScopedSoftDeleteModel):
    """Object-access source in the tests and the demo: users are assigned the buyers they work for."""
    name = models.CharField(max_length=80)

    def __str__(self):
        return self.name


class Order(CreatedInfoModel, CompanyScopedSoftDeleteModel):
    """Follows its buyer (object access) and its creator (user access, ``owner_field="created_by"``)."""
    ref = models.CharField(max_length=30)
    buyer = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="orders")
    qty = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.ref


class StockMove(CompanyScopedModel):
    """A receipt (qty_in) or issue (qty_out) of a fabric: the stock ledger example (``register_stock_ledger`` in registrations)."""
    fabric = models.ForeignKey(Fabric, on_delete=models.PROTECT, related_name="moves")
    moved_on = models.DateField()
    ref = models.CharField("challan no.", max_length=30, blank=True)
    qty_in = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    qty_out = models.DecimalField(max_digits=12, decimal_places=2, default=0)
