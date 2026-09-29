import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from apps.events.models import Event, Prize
from apps.results.models import Publication
from apps.submissions.models import Project


class CertificateTemplate(models.Model):
    """Immutable versioned template for certificate generation."""

    class Kind(models.TextChoices):
        PARTICIPANT = "PARTICIPANT", "Participant"
        JUDGE = "JUDGE", "Judge"
        WINNER = "WINNER", "Winner"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="certificate_templates")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    version = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=200)
    subtitle = models.CharField(max_length=300, blank=True)
    body_text = models.TextField(max_length=2000, blank=True)
    footer_text = models.CharField(max_length=300, blank=True)
    logo_asset_key = models.CharField(max_length=512, null=True, blank=True)
    layout_version = models.CharField(max_length=20, default="v1")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_certificate_templates"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "certificate_templates"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "kind", "version"],
                name="unique_cert_template_event_kind_version"
            ),
        ]
        ordering = ["kind", "-version"]

    def __str__(self):
        return f"{self.title} ({self.kind} v{self.version})"


class SigningKey(models.Model):
    """Ed25519 signing key for credential issuance."""

    class State(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RETIRED = "RETIRED", "Retired"
        COMPROMISED = "COMPROMISED", "Compromised"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key_id = models.CharField(max_length=64, unique=True)
    issuer_id = models.CharField(max_length=128)
    algorithm = models.CharField(max_length=20, default="ED25519")
    public_key_bytes = models.BinaryField()
    private_key_ref = models.CharField(
        max_length=512, null=True, blank=True,
        help_text="Reference to private key in secure store. Null for imported public-only keys."
    )
    state = models.CharField(max_length=20, choices=State.choices, default=State.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "signing_keys"
        indexes = [
            models.Index(fields=["state", "created_at"], name="idx_signing_key_state"),
        ]

    def clean(self):
        super().clean()
        if self.state == self.State.RETIRED and not self.retired_at:
            raise ValidationError("retired_at is required when state is RETIRED.")

    @property
    def can_issue(self) -> bool:
        """Can this key be used to issue new credentials?"""
        return self.state == self.State.ACTIVE and self.private_key_ref is not None

    def __str__(self):
        return f"SigningKey {self.key_id} ({self.state})"


class AwardDecision(models.Model):
    """Explicit organiser prize decision tied to a publication."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publication = models.ForeignKey(
        Publication, on_delete=models.PROTECT, related_name="award_decisions"
    )
    prize = models.ForeignKey(Prize, on_delete=models.PROTECT, related_name="award_decisions")
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="award_decisions")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="award_decisions"
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "award_decisions"
        constraints = [
            models.UniqueConstraint(
                fields=["publication", "prize", "project"],
                name="unique_award_pub_prize_project"
            ),
        ]

    def __str__(self):
        return f"Award {self.prize.name} -> {self.project} (pub #{self.publication.number})"


class IssuedCredential(models.Model):
    """Immutable signed credential record with optional PDF."""

    class Kind(models.TextChoices):
        PARTICIPANT = "PARTICIPANT", "Participant"
        JUDGE = "JUDGE", "Judge"
        WINNER = "WINNER", "Winner"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="issued_credentials")
    subject_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="issued_credentials"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    template = models.ForeignKey(
        CertificateTemplate, on_delete=models.PROTECT, related_name="issued_credentials",
        null=True, blank=True
    )
    template_version = models.PositiveIntegerField(null=True, blank=True)
    publication = models.ForeignKey(
        Publication, on_delete=models.PROTECT, related_name="issued_credentials",
        null=True, blank=True
    )
    award_decision = models.ForeignKey(
        AwardDecision, on_delete=models.PROTECT, related_name="issued_credentials",
        null=True, blank=True
    )
    eligibility_snapshot = models.JSONField(default=dict)
    display_name_snapshot = models.CharField(max_length=200)
    payload_bytes = models.BinaryField(
        help_text="Exact canonical JSON bytes that were signed."
    )
    signature_bytes = models.BinaryField()
    signing_key = models.ForeignKey(
        SigningKey, on_delete=models.PROTECT, related_name="issued_credentials"
    )
    pdf_storage_key = models.CharField(max_length=512, null=True, blank=True)
    pdf_sha256 = models.CharField(max_length=64, null=True, blank=True)
    issued_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "issued_credentials"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "subject_user", "kind", "template_version", "publication"],
                name="unique_credential_issuance_key"
            ),
        ]
        indexes = [
            models.Index(
                fields=["event", "subject_user", "kind"],
                name="idx_cred_event_subject_kind"
            ),
        ]

    def __str__(self):
        return f"Credential {self.kind} for {self.display_name_snapshot} ({self.event})"


class CredentialStatusEvent(models.Model):
    """Append-only status changes for issued credentials."""

    class State(models.TextChoices):
        REVOKED = "REVOKED", "Revoked"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    credential = models.ForeignKey(
        IssuedCredential, on_delete=models.CASCADE, related_name="status_events"
    )
    state = models.CharField(max_length=20, choices=State.choices)
    reason = models.TextField(blank=True)
    replacement = models.ForeignKey(
        IssuedCredential, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="replaces_events"
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="credential_status_events"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "credential_status_events"
        indexes = [
            models.Index(fields=["credential", "created_at"], name="idx_cred_status_cred_time"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Status {self.state} for credential {self.credential_id}"


class PublicRecordConsent(models.Model):
    """Tracks user consent for public display of credential records."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="public_consents")
    subject_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="public_consents"
    )
    allowed_public_fields = models.JSONField(
        default=list,
        help_text="List of fields allowed for public display, e.g. ['display_name', 'contribution_summary']"
    )
    granted_at = models.DateTimeField(default=timezone.now)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "public_record_consents"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "subject_user"],
                name="unique_public_consent_event_user"
            ),
        ]

    @property
    def is_active(self) -> bool:
        return self.withdrawn_at is None

    def __str__(self):
        status = "ACTIVE" if self.is_active else "WITHDRAWN"
        return f"Consent {status} for {self.subject_user} in {self.event}"
