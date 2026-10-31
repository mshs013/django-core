from django.contrib.contenttypes.models import ContentType
from django.db import models


class SeedRecord(models.Model):
    """One row the seeder created, so "Remove seeded data" deletes exactly those rows and nothing a person typed in."""
    batch = models.CharField(max_length=40, db_index=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]
        permissions = [("use_seeder", "Can generate and remove demo data")]
        default_permissions = ()

    def __str__(self):
        return f"{self.content_type.app_label}.{self.content_type.model} #{self.object_id}"
