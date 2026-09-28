import uuid
from django.conf import settings
from django.db import models


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        VALIDATED = "VALIDATED", "Validated"
        APPLIED = "APPLIED", "Applied"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    namespace = models.CharField(max_length=80)
    format_version = models.CharField(max_length=20, default="1.0")
    file_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=Status.choices)
    report_json = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "import_batches"
        constraints = [
            models.UniqueConstraint(fields=["namespace", "file_sha256"], name="unique_import_batch_file")
        ]

    def __str__(self):
        return f"ImportBatch {self.namespace} [{self.status}]"


class ExternalRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    namespace = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=40)
    external_id = models.CharField(max_length=100)
    internal_id = models.UUIDField()
    original_payload_json = models.JSONField()
    import_batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="records")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "external_records"
        constraints = [
            models.UniqueConstraint(
                fields=["namespace", "entity_type", "external_id"],
                name="unique_external_record"
            )
        ]

    def __str__(self):
        return f"{self.namespace}:{self.entity_type}:{self.external_id} -> {self.internal_id}"
