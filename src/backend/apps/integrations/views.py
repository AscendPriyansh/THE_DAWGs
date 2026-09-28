from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.events.models import Event, EventMembership
from apps.integrations.models import ApiCredential
from apps.integrations.openapi import get_openapi_schema, render_local_docs_html
from apps.integrations.services import issue_api_credential, revoke_api_credential


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def api_event_api_keys(request, slug):
    """
    GET: Lists active API keys created by the requesting user for this event.
    POST: Issues a new event-scoped API key. Returns raw token once.
    """
    event = get_object_or_404(Event, slug=slug)

    if request.method == "GET":
        # Organisers can see all event keys or own keys
        is_organiser = EventMembership.objects.filter(
            event=event, user=request.user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
        ).exists() or request.user.is_staff

        if is_organiser:
            creds = ApiCredential.objects.filter(event=event).select_related("owner_user").order_by("-created_at")
        else:
            creds = ApiCredential.objects.filter(event=event, owner_user=request.user).order_by("-created_at")

        data = [
            {
                "id": str(c.id),
                "label": c.label,
                "public_prefix": c.public_prefix,
                "scopes": c.scopes_json,
                "owner_name": c.owner_user.display_name,
                "owner_id": str(c.owner_user_id),
                "is_active": c.is_active,
                "expires_at": c.expires_at.isoformat(),
                "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
                "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
                "created_at": c.created_at.isoformat(),
            }
            for c in creds
        ]
        return Response({"api_keys": data})

    elif request.method == "POST":
        label = request.data.get("label", "")
        scopes = request.data.get("scopes", [])
        expires_in_days = request.data.get("expires_in_days", 30)

        try:
            token, cred = issue_api_credential(
                event=event,
                owner_user=request.user,
                label=label,
                scopes=scopes,
                expires_in_days=int(expires_in_days),
            )
            return Response(
                {
                    "token": token,  # High entropy secret shown once
                    "id": str(cred.id),
                    "label": cred.label,
                    "public_prefix": cred.public_prefix,
                    "scopes": cred.scopes_json,
                    "expires_at": cred.expires_at.isoformat(),
                    "notice": "Store this token securely. It will not be shown again.",
                },
                status=status.HTTP_201_CREATED,
            )
        except PermissionDenied as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_revoke_api_key(request, slug, key_id):
    """
    Revokes an event API key. Key owner or event organiser can revoke.
    """
    event = get_object_or_404(Event, slug=slug)
    credential = get_object_or_404(ApiCredential, id=key_id, event=event)

    reason = request.data.get("reason", "Revoked via API")
    try:
        revoked = revoke_api_credential(credential, request.user, reason=reason)
        return Response({
            "status": "REVOKED",
            "id": str(revoked.id),
            "revoked_at": revoked.revoked_at.isoformat(),
        })
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)


def api_openapi_schema(request):
    """Machine-readable OpenAPI 3.1 schema."""
    return JsonResponse(get_openapi_schema())


def api_docs_ui(request):
    """Local offline interactive documentation UI."""
    return HttpResponse(render_local_docs_html(), content_type="text/html")
