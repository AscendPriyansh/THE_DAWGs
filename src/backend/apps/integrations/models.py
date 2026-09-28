import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from apps.events.models import Event


PERMITTED_API_SCOPES = [
    "event:read",
    "event:manage",
    "team:manage",
    "submission:write",
    "judging:write",
    "results:publish",
    "community:moderate",
    "integrations:manage",
    "credentials:issue",
    "data:export",
    "data:import",
]


class ApiCredential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_credentials"
    )
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="api_credentials")
    label = models.CharField(max_length=120)
    public_prefix = models.CharField(max_length=16, db_index=True)
    secret_digest = models.CharField(max_length=64, unique=True)
    scopes_json = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "api_credentials"
        indexes = [
            models.Index(fields=["event", "owner_user", "created_at"], name="idx_api_cred_owner"),
        ]

    def clean(self):
        super().clean()
        if not self.event_id:
            raise ValidationError("event_id is required: no global wildcard keys are permitted.")
        for scope in self.scopes_json:
            if scope not in PERMITTED_API_SCOPES:
                raise ValidationError(f"Invalid API scope '{scope}'. Must be one of {PERMITTED_API_SCOPES}")

    @property
    def is_active(self) -> bool:
        if self.revoked_at:
            return False
        if timezone.now() >= self.expires_at:
            return False
        return True

    def __str__(self):
        status_label = "REVOKED" if self.revoked_at else ("EXPIRED" if not self.is_active else "ACTIVE")
        return f"ApiKey {self.public_prefix}... ({self.label}) [{status_label}]"


class IdempotencyRecord(models.Model):
    class State(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMMITTED = "COMMITTED", "Committed"
        FAILED = "FAILED", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    credential_or_actor_key = models.CharField(max_length=128)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="idempotency_records")
    route_key = models.CharField(max_length=128)
    idempotency_key = models.CharField(max_length=128)
    request_sha256 = models.CharField(max_length=64)
    response_status = models.PositiveIntegerField(null=True, blank=True)
    response_json = models.JSONField(null=True, blank=True)
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "idempotency_records"
        constraints = [
            models.UniqueConstraint(
                fields=["credential_or_actor_key", "event", "route_key", "idempotency_key"],
                name="unique_idempotency_record",
            )
        ]
        indexes = [
            models.Index(fields=["expires_at"], name="idx_idemp_expires"),
        ]

    def __str__(self):
        return f"Idempotency {self.route_key}:{self.idempotency_key} ({self.state})"
