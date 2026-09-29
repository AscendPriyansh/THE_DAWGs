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


PERMITTED_WEBHOOK_EVENT_TYPES = [
    "event.updated",
    "submission.created",
    "submission.updated",
    "rubric.frozen",
    "assignment.created",
    "review.submitted",
    "results.published",
    "community.moderated",
    "credential.issued",
    "credential.revoked",
    "job.completed",
]


class WebhookEndpoint(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        PAUSED = "PAUSED", "Paused"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="webhook_endpoints")
    url = models.CharField(max_length=2048)
    allowed_event_types = models.JSONField(default=list)
    secret_encrypted = models.CharField(max_length=512)
    secret_key_version = models.CharField(max_length=32, default="v1")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_webhook_endpoints"
    )
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "webhook_endpoints"
        indexes = [
            models.Index(fields=["event", "status"], name="idx_wh_ep_event_status"),
        ]

    def __str__(self):
        return f"WebhookEndpoint {self.url} ({self.status})"

    def clean(self):
        super().clean()
        for et in self.allowed_event_types:
            if et != "*" and et not in PERMITTED_WEBHOOK_EVENT_TYPES:
                raise ValidationError(f"Invalid event type '{et}'. Must be '*' or one of {PERMITTED_WEBHOOK_EVENT_TYPES}")


class DomainEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="domain_events")
    type = models.CharField(max_length=128)
    schema_version = models.CharField(max_length=16, default="1.0")
    entity_id = models.CharField(max_length=128)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "domain_events"
        indexes = [
            models.Index(fields=["event", "created_at"], name="idx_domain_event_event_time"),
            models.Index(fields=["type", "created_at"], name="idx_domain_event_type_time"),
        ]

    def __str__(self):
        return f"DomainEvent {self.type} [{self.entity_id}] at {self.created_at}"


class WebhookDelivery(models.Model):
    class State(models.TextChoices):
        PENDING = "PENDING", "Pending"
        LEASED = "LEASED", "Leased"
        RETRY = "RETRY", "Retry"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        DEAD = "DEAD", "Dead"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    domain_event = models.ForeignKey(DomainEvent, on_delete=models.CASCADE, related_name="deliveries")
    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries")
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    lifetime_attempt_count = models.PositiveIntegerField(default=0)
    replay_generation = models.PositiveIntegerField(default=0)
    attempts_in_generation = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease_owner = models.CharField(max_length=128, null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    last_status = models.IntegerField(null=True, blank=True)
    last_error_class = models.CharField(max_length=64, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "webhook_deliveries"
        constraints = [
            models.UniqueConstraint(fields=["domain_event", "endpoint"], name="unique_wh_delivery_event_endpoint"),
        ]
        indexes = [
            models.Index(fields=["state", "next_attempt_at", "lease_until"], name="idx_wh_deliv_state_next"),
            models.Index(fields=["endpoint", "created_at"], name="idx_wh_deliv_ep_created"),
        ]

    def __str__(self):
        return f"WebhookDelivery {self.endpoint_id} - {self.domain_event.type} ({self.state})"


class WebhookAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    delivery = models.ForeignKey(WebhookDelivery, on_delete=models.CASCADE, related_name="attempts")
    attempt_number = models.PositiveIntegerField()
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    response_status = models.IntegerField(null=True, blank=True)
    response_headers_json = models.JSONField(default=dict)
    response_body_redacted = models.TextField(blank=True, default="")
    error_class = models.CharField(max_length=64, null=True, blank=True)
    request_timestamp = models.DateTimeField()

    class Meta:
        db_table = "webhook_attempts"
        constraints = [
            models.UniqueConstraint(fields=["delivery", "attempt_number"], name="unique_wh_attempt_deliv_num"),
        ]
        indexes = [
            models.Index(fields=["delivery", "started_at"], name="idx_wh_attempt_deliv_time"),
        ]

    def __str__(self):
        return f"WebhookAttempt #{self.attempt_number} ({self.response_status or self.error_class})"


class BackgroundJob(models.Model):
    class Kind(models.TextChoices):
        GENERATE_CERTIFICATES = "GENERATE_CERTIFICATES", "Generate Certificates"
        EXPORT_EVENT = "EXPORT_EVENT", "Export Event"
        VALIDATE_IMPORT = "VALIDATE_IMPORT", "Validate Import"
        APPLY_IMPORT = "APPLY_IMPORT", "Apply Import"

    class State(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True, related_name="background_jobs")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="requested_jobs")
    kind = models.CharField(max_length=64, choices=Kind.choices)
    parameters_json = models.JSONField(default=dict)
    input_sha256 = models.CharField(max_length=64, null=True, blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    lease_owner = models.CharField(max_length=128, null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    progress_current = models.PositiveIntegerField(default=0)
    progress_total = models.PositiveIntegerField(null=True, blank=True)
    result_storage_key = models.CharField(max_length=512, null=True, blank=True)
    result_sha256 = models.CharField(max_length=64, null=True, blank=True)
    error_code = models.CharField(max_length=64, null=True, blank=True)
    error_details = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "background_jobs"
        indexes = [
            models.Index(fields=["state", "lease_until", "created_at"], name="idx_bg_job_state_lease"),
            models.Index(fields=["event", "requested_by", "created_at"], name="idx_bg_job_event_req"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(attempts__gte=0), name="chk_bg_job_attempts_nonneg"),
            models.CheckConstraint(condition=models.Q(progress_current__gte=0), name="chk_bg_job_prog_nonneg"),
        ]

    def __str__(self):
        return f"BackgroundJob {self.kind} ({self.state}) - attempts: {self.attempts}"
