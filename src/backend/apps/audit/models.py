import uuid
from django.conf import settings
from django.db import models
from apps.events.models import Event


class AuditEvent(models.Model):
    class ActorKind(models.TextChoices):
        USER = "USER", "User"
        SYSTEM = "SYSTEM", "System"
        IMPORT = "IMPORT", "Import"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events")
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_events"
    )
    actor_kind = models.CharField(max_length=20, choices=ActorKind.choices, default=ActorKind.USER)
    action = models.CharField(max_length=80)
    entity_type = models.CharField(max_length=80)
    entity_id = models.UUIDField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True)
    before_json = models.JSONField(null=True, blank=True)
    after_json = models.JSONField(null=True, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "audit_events"
        indexes = [
            models.Index(fields=["event", "created_at", "id"], name="idx_audit_timeline"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.actor_kind}] {self.action} on {self.entity_type} ({self.created_at})"
