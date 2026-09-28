import hashlib
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from rest_framework import authentication
from rest_framework.exceptions import AuthenticationFailed

from apps.events.models import EventMembership
from apps.integrations.models import ApiCredential


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """
    Bearer token authentication for scoped event API keys (ApiCredential).
    Enforces secret digest matching, active state, expiration, revocation,
    event scoping, and ambiguous mixed-identity rejection.
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:].strip()
        if not token:
            return None

        # Reject ambiguous mixed identities: check if a conflicting session cookie is present
        session_key = request.COOKIES.get("session") or request.COOKIES.get("sessionid")
        if session_key and request.user.is_authenticated:
            # We will verify that if a session user is authenticated, it matches the key owner
            pass

        secret_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()

        try:
            credential = (
                ApiCredential.objects.select_related("owner_user", "event")
                .filter(secret_digest=secret_digest)
                .first()
            )
        except Exception:
            return None

        if not credential:
            raise AuthenticationFailed("Invalid API key.")

        if credential.revoked_at:
            raise AuthenticationFailed("API key has been revoked.")

        now = timezone.now()
        if now >= credential.expires_at:
            raise AuthenticationFailed("API key has expired.")

        if not credential.owner_user.is_active:
            raise AuthenticationFailed("API key owner user account is deactivated.")

        # Check owner's event membership
        membership = EventMembership.objects.filter(
            event=credential.event, user=credential.owner_user
        ).first()

        if not membership or membership.status != EventMembership.Status.ACTIVE:
            raise AuthenticationFailed("API key owner has no active membership in this event.")

        # If a session was also present, reject if different user
        if session_key and request.user.is_authenticated and request.user.id != credential.owner_user_id:
            raise AuthenticationFailed(
                "Ambiguous mixed identities detected: session identity does not match Bearer token owner."
            )

        # Update last_used_at timestamp
        credential.last_used_at = now
        credential.save(update_fields=["last_used_at"])

        # Attach credential and membership to request
        request.api_credential = credential
        request.event_membership = membership

        return (credential.owner_user, credential)

    def authenticate_header(self, request):
        return 'Bearer realm="api"'


def enforce_scope_and_role(request, event, required_scope: str, required_role: str = None):
    """
    Enforces that the request has both the necessary API scope (if using an ApiCredential)
    and the actual role in EventMembership.
    Effective authority is ALWAYS the intersection:
    A scope cannot elevate a participant into an organiser.
    """
    if not request.user or not request.user.is_authenticated:
        raise PermissionDenied("Authentication required.")

    # 1. Check API Key Scope if request was authenticated via ApiCredential
    if hasattr(request, "api_credential") and request.api_credential:
        cred = request.api_credential
        if cred.event_id != event.id:
            raise PermissionDenied(
                f"API key is scoped to event {cred.event.slug}, cannot access {event.slug}."
            )
        if required_scope not in cred.scopes_json:
            raise PermissionDenied(
                f"API key lacks required scope '{required_scope}'."
            )

    # 2. Check Event Membership & Role
    membership = getattr(request, "event_membership", None)
    if not membership:
        membership = EventMembership.objects.filter(
            event=event, user=request.user, status=EventMembership.Status.ACTIVE
        ).first()

    if not membership and not request.user.is_staff:
        raise PermissionDenied("You do not have active membership in this event.")

    if required_role:
        if required_role == EventMembership.Role.ORGANISER:
            if (not membership or membership.role != EventMembership.Role.ORGANISER) and not request.user.is_staff:
                raise PermissionDenied("Organiser permissions required.")
        elif required_role == EventMembership.Role.JUDGE:
            if (not membership or membership.role not in [EventMembership.Role.JUDGE, EventMembership.Role.ORGANISER]) and not request.user.is_staff:
                raise PermissionDenied("Judge permissions required.")
        elif required_role == EventMembership.Role.PARTICIPANT:
            if (not membership or membership.role not in [EventMembership.Role.PARTICIPANT, EventMembership.Role.ORGANISER]) and not request.user.is_staff:
                raise PermissionDenied("Participant permissions required.")

    return membership
