import hashlib
import secrets
from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.events.models import Event, EventMembership
from apps.integrations.models import ApiCredential, PERMITTED_API_SCOPES


@transaction.atomic
def issue_api_credential(
    event: Event,
    owner_user,
    label: str,
    scopes: list,
    expires_in_days: int = 30,
) -> tuple[str, ApiCredential]:
    """
    Issues a new high-entropy event-scoped API key.
    Stores only the SHA-256 digest; returns the plain-text token once.
    """
    if not owner_user or not owner_user.is_authenticated:
        raise PermissionDenied("Authentication required to generate API keys.")

    # Owner must have active membership in this event
    membership = EventMembership.objects.filter(
        event=event, user=owner_user, status=EventMembership.Status.ACTIVE
    ).first()
    if not membership and not owner_user.is_staff:
        raise PermissionDenied("You must have active membership in this event to create an API key.")

    if not label or not label.strip():
        raise ValidationError("API key label is required.")

    # Validate scopes
    if not scopes:
        raise ValidationError("At least one scope must be selected.")

    for s in scopes:
        if s not in PERMITTED_API_SCOPES:
            raise ValidationError(f"Invalid scope '{s}'. Permitted scopes are: {', '.join(PERMITTED_API_SCOPES)}")

    # Generate high-entropy bearer token
    token = f"dg_live_{secrets.token_urlsafe(32)}"
    secret_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    public_prefix = token[:12]

    now = timezone.now()
    expires_at = now + timedelta(days=expires_in_days)

    cred = ApiCredential.objects.create(
        owner_user=owner_user,
        event=event,
        label=label.strip(),
        public_prefix=public_prefix,
        secret_digest=secret_digest,
        scopes_json=scopes,
        expires_at=expires_at,
    )

    AuditEvent.objects.create(
        event=event,
        actor_user=owner_user,
        actor_kind=AuditEvent.ActorKind.USER,
        action="API_KEY_ISSUED",
        entity_type="ApiCredential",
        entity_id=cred.id,
        after_json={
            "label": cred.label,
            "public_prefix": cred.public_prefix,
            "scopes": cred.scopes_json,
            "expires_at": cred.expires_at.isoformat(),
        },
    )

    return token, cred


@transaction.atomic
def revoke_api_credential(
    credential: ApiCredential,
    actor_user,
    reason: str = "",
) -> ApiCredential:
    """
    Revokes an API credential. Owner or event organiser can revoke.
    """
    is_owner = credential.owner_user_id == actor_user.id
    is_organiser = EventMembership.objects.filter(
        event=credential.event, user=actor_user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
    ).exists() or actor_user.is_staff

    if not is_owner and not is_organiser:
        raise PermissionDenied("Only the key owner or an organiser can revoke this API key.")

    if credential.revoked_at:
        return credential  # Already revoked

    now = timezone.now()
    credential.revoked_at = now
    credential.save(update_fields=["revoked_at"])

    AuditEvent.objects.create(
        event=credential.event,
        actor_user=actor_user,
        actor_kind=AuditEvent.ActorKind.USER,
        action="API_KEY_REVOKED",
        entity_type="ApiCredential",
        entity_id=credential.id,
        reason=reason.strip() if reason else "Revoked by user",
        after_json={"revoked_at": now.isoformat()},
    )

    return credential
