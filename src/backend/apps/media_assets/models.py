import uuid
from django.conf import settings
from django.db import models
from apps.events.models import Event
from apps.submissions.models import Project


class Asset(models.Model):
    class Purpose(models.TextChoices):
        EVENT_IMAGE = "EVENT_IMAGE", "Event Image"
        PROJECT_IMAGE = "PROJECT_IMAGE", "Project Image"
        PROJECT_FILE = "PROJECT_FILE", "Project File"

    class State(models.TextChoices):
        STAGED = "STAGED", "Staged"
        READY = "READY", "Ready"
        REJECTED = "REJECTED", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="assets")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_assets"
    )
    project = models.ForeignKey(
        Project, on_delete=models.SET_NULL, null=True, blank=True, related_name="assets"
    )
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    storage_key = models.CharField(max_length=255, unique=True)
    original_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    byte_size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    state = models.CharField(max_length=20, choices=State.choices, default=State.STAGED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assets"
        indexes = [
            models.Index(fields=["state", "created_at"], name="idx_asset_staged"),
        ]

    def __str__(self):
        return f"{self.original_name} [{self.state}]"
