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


class PortableImportPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_id = models.UUIDField(null=True, blank=True)
    archive_sha256 = models.CharField(max_length=64)
    source_instance_id = models.CharField(max_length=120)
    format_version = models.CharField(max_length=20, default="1")
    preview_json = models.JSONField(default=dict)
    expires_at = models.DateTimeField()
    applied_at = models.DateTimeField(null=True, blank=True)
    target_event_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "portable_import_plans"
        constraints = [
            models.UniqueConstraint(
                fields=["source_instance_id", "archive_sha256"],
                condition=models.Q(applied_at__isnull=False),
                name="unique_applied_portable_import_plan",
            )
        ]
        indexes = [
            models.Index(fields=["expires_at", "applied_at"], name="idx_import_plan_cleanup"),
            models.Index(fields=["source_instance_id", "archive_sha256"], name="idx_import_plan_source_hash"),
        ]

    def __str__(self):
        status = "APPLIED" if self.applied_at else "PREVIEW"
        return f"PortableImportPlan {self.source_instance_id}:{self.archive_sha256[:8]} [{status}]"
