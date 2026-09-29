from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
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


# --- Webhooks & Integrations REST APIs (M08) ---

from apps.integrations.authentication import enforce_scope_and_role
from apps.integrations.models import (
    WebhookEndpoint,
    WebhookDelivery,
    WebhookAttempt,
    BackgroundJob,
    PERMITTED_WEBHOOK_EVENT_TYPES,
)
from apps.integrations.crypto import generate_webhook_secret, encrypt_webhook_secret
from apps.integrations.ssrf import validate_webhook_url
from apps.integrations.delivery import replay_webhook_delivery
from apps.integrations.jobs import submit_background_job
from apps.audit.models import AuditEvent


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def api_event_webhooks(request, slug):
    """
    GET: Lists webhook endpoints configured for this event.
    POST: Creates a new webhook endpoint. Returns the high-entropy shared secret ONCE.
    """
    event = get_object_or_404(Event, slug=slug)
    enforce_scope_and_role(request, event, required_scope="integrations:manage", required_role=EventMembership.Role.ORGANISER)

    if request.method == "GET":
        endpoints = WebhookEndpoint.objects.filter(event=event).order_by("-created_at")
        data = [
            {
                "id": str(ep.id),
                "url": ep.url,
                "allowed_event_types": ep.allowed_event_types,
                "secret_key_version": ep.secret_key_version,
                "status": ep.status,
                "version": ep.version,
                "created_at": ep.created_at.isoformat(),
                "updated_at": ep.updated_at.isoformat(),
            }
            for ep in endpoints
        ]
        return Response({"webhooks": data})

    elif request.method == "POST":
        url = request.data.get("url", "").strip()
        allowed_types = request.data.get("allowed_event_types", ["*"])

        if not url:
            return Response({"detail": "Field 'url' is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Validate URL / SSRF
        is_safe, error_msg, _ = validate_webhook_url(url)
        if not is_safe:
            return Response({"detail": f"Invalid webhook URL: {error_msg}"}, status=status.HTTP_400_BAD_REQUEST)

        # Validate event types
        if not isinstance(allowed_types, list):
            return Response({"detail": "allowed_event_types must be a list of strings."}, status=status.HTTP_400_BAD_REQUEST)

        for et in allowed_types:
            if et != "*" and et not in PERMITTED_WEBHOOK_EVENT_TYPES:
                return Response(
                    {"detail": f"Invalid event type '{et}'. Must be '*' or one of {PERMITTED_WEBHOOK_EVENT_TYPES}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        secret = generate_webhook_secret()
        secret_enc = encrypt_webhook_secret(secret)

        endpoint = WebhookEndpoint.objects.create(
            event=event,
            url=url,
            allowed_event_types=allowed_types,
            secret_encrypted=secret_enc,
            secret_key_version="v1",
            status=WebhookEndpoint.Status.ACTIVE,
            created_by=request.user,
        )

        AuditEvent.objects.create(
            event=event,
            actor_user=request.user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="WEBHOOK_ENDPOINT_CREATED",
            entity_type="WebhookEndpoint",
            entity_id=endpoint.id,
            after_json={"url": url, "allowed_event_types": allowed_types},
            reason="Organiser created webhook endpoint",
        )

        return Response(
            {
                "id": str(endpoint.id),
                "url": endpoint.url,
                "allowed_event_types": endpoint.allowed_event_types,
                "status": endpoint.status,
                "secret": secret,  # Returned only once!
                "notice": "Store this webhook secret securely. It is encrypted in the database and cannot be retrieved again.",
                "created_at": endpoint.created_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def api_webhook_endpoint_detail(request, slug, endpoint_id):
    """
    GET: Retrieves a single webhook endpoint.
    PATCH: Updates endpoint URL, event subscriptions, or status (ACTIVE/PAUSED).
    DELETE: Removes endpoint and cascades its deliveries.
    """
    event = get_object_or_404(Event, slug=slug)
    enforce_scope_and_role(request, event, required_scope="integrations:manage", required_role=EventMembership.Role.ORGANISER)
    endpoint = get_object_or_404(WebhookEndpoint, id=endpoint_id, event=event)

    if request.method == "GET":
        return Response({
            "id": str(endpoint.id),
            "url": endpoint.url,
            "allowed_event_types": endpoint.allowed_event_types,
            "secret_key_version": endpoint.secret_key_version,
            "status": endpoint.status,
            "version": endpoint.version,
            "created_at": endpoint.created_at.isoformat(),
            "updated_at": endpoint.updated_at.isoformat(),
        })

    elif request.method == "PATCH":
        if "url" in request.data:
            url = request.data["url"].strip()
            is_safe, error_msg, _ = validate_webhook_url(url)
            if not is_safe:
                return Response({"detail": f"Invalid webhook URL: {error_msg}"}, status=status.HTTP_400_BAD_REQUEST)
            endpoint.url = url

        if "allowed_event_types" in request.data:
            types = request.data["allowed_event_types"]
            for et in types:
                if et != "*" and et not in PERMITTED_WEBHOOK_EVENT_TYPES:
                    return Response({"detail": f"Invalid event type '{et}'."}, status=status.HTTP_400_BAD_REQUEST)
            endpoint.allowed_event_types = types

        if "status" in request.data:
            new_status = request.data["status"].upper()
            if new_status not in [WebhookEndpoint.Status.ACTIVE, WebhookEndpoint.Status.PAUSED]:
                return Response({"detail": "Status must be ACTIVE or PAUSED."}, status=status.HTTP_400_BAD_REQUEST)
            endpoint.status = new_status

        endpoint.version += 1
        endpoint.save()

        AuditEvent.objects.create(
            event=event,
            actor_user=request.user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="WEBHOOK_ENDPOINT_UPDATED",
            entity_type="WebhookEndpoint",
            entity_id=endpoint.id,
            after_json={"version": endpoint.version, "status": endpoint.status},
            reason="Organiser updated webhook endpoint",
        )

        return Response({
            "id": str(endpoint.id),
            "url": endpoint.url,
            "allowed_event_types": endpoint.allowed_event_types,
            "status": endpoint.status,
            "version": endpoint.version,
            "updated_at": endpoint.updated_at.isoformat(),
        })

    elif request.method == "DELETE":
        ep_id = endpoint.id
        endpoint.delete()

        AuditEvent.objects.create(
            event=event,
            actor_user=request.user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="WEBHOOK_ENDPOINT_DELETED",
            entity_type="WebhookEndpoint",
            entity_id=ep_id,
            after_json={"endpoint_id": str(ep_id)},
            reason="Organiser deleted webhook endpoint",
        )
        return Response({"status": "DELETED", "id": ep_id})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_webhook_deliveries(request, slug, endpoint_id):
    """
    GET: Lists deliveries for an endpoint with pagination.
    """
    event = get_object_or_404(Event, slug=slug)
    enforce_scope_and_role(request, event, required_scope="integrations:manage", required_role=EventMembership.Role.ORGANISER)
    endpoint = get_object_or_404(WebhookEndpoint, id=endpoint_id, event=event)

    limit = min(int(request.query_params.get("limit", 25)), 100)
    deliveries = (
        WebhookDelivery.objects.filter(endpoint=endpoint)
        .select_related("domain_event")
        .order_by("-created_at")[:limit]
    )

    data = [
        {
            "id": str(d.id),
            "domain_event_id": str(d.domain_event_id),
            "event_type": d.domain_event.type,
            "state": d.state,
            "lifetime_attempt_count": d.lifetime_attempt_count,
            "replay_generation": d.replay_generation,
            "attempts_in_generation": d.attempts_in_generation,
            "next_attempt_at": d.next_attempt_at.isoformat() if d.next_attempt_at else None,
            "last_status": d.last_status,
            "last_error_class": d.last_error_class,
            "created_at": d.created_at.isoformat(),
        }
        for d in deliveries
    ]
    return Response({"deliveries": data})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_webhook_delivery_detail(request, slug, delivery_id):
    """
    GET: Retrieves a single webhook delivery with its complete attempt history.
    """
    event = get_object_or_404(Event, slug=slug)
    enforce_scope_and_role(request, event, required_scope="integrations:manage", required_role=EventMembership.Role.ORGANISER)
    delivery = get_object_or_404(WebhookDelivery, id=delivery_id, endpoint__event=event)

    attempts = delivery.attempts.order_by("attempt_number")
    attempts_data = [
        {
            "attempt_number": a.attempt_number,
            "started_at": a.started_at.isoformat(),
            "finished_at": a.finished_at.isoformat(),
            "response_status": a.response_status,
            "response_headers": a.response_headers_json,
            "response_body_redacted": a.response_body_redacted,
            "error_class": a.error_class,
        }
        for a in attempts
    ]

    return Response({
        "id": str(delivery.id),
        "endpoint_id": str(delivery.endpoint_id),
        "endpoint_url": delivery.endpoint.url,
        "domain_event": {
            "id": str(delivery.domain_event.id),
            "type": delivery.domain_event.type,
            "entity_id": delivery.domain_event.entity_id,
            "created_at": delivery.domain_event.created_at.isoformat(),
        },
        "state": delivery.state,
        "lifetime_attempt_count": delivery.lifetime_attempt_count,
        "replay_generation": delivery.replay_generation,
        "attempts_in_generation": delivery.attempts_in_generation,
        "next_attempt_at": delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else None,
        "last_status": delivery.last_status,
        "last_error_class": delivery.last_error_class,
        "created_at": delivery.created_at.isoformat(),
        "attempts": attempts_data,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_webhook_delivery_replay(request, slug, delivery_id):
    """
    POST: Replays a failed or dead webhook delivery.
    Increments replay generation and resets generation retry budget without mutating lifetime history.
    """
    event = get_object_or_404(Event, slug=slug)
    enforce_scope_and_role(request, event, required_scope="integrations:manage", required_role=EventMembership.Role.ORGANISER)
    delivery = get_object_or_404(WebhookDelivery, id=delivery_id, endpoint__event=event)

    try:
        replayed = replay_webhook_delivery(str(delivery.id), request.user)
        return Response({
            "status": "REPLAY_SCHEDULED",
            "id": str(replayed.id),
            "state": replayed.state,
            "replay_generation": replayed.replay_generation,
            "next_attempt_at": replayed.next_attempt_at.isoformat(),
        })
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


# --- Background Jobs REST APIs (M08) ---


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def api_background_jobs(request, slug):
    """
    GET: Lists background jobs for this event.
    POST: Enqueues a new background job. Returns 202 Accepted.
    """
    event = get_object_or_404(Event, slug=slug)

    if request.method == "GET":
        is_organiser = EventMembership.objects.filter(
            event=event, user=request.user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
        ).exists() or request.user.is_staff

        if is_organiser:
            jobs = BackgroundJob.objects.filter(event=event).order_by("-created_at")[:100]
        else:
            jobs = BackgroundJob.objects.filter(event=event, requested_by=request.user).order_by("-created_at")[:100]

        data = [
            {
                "id": str(j.id),
                "kind": j.kind,
                "state": j.state,
                "requested_by": j.requested_by.display_name,
                "progress_current": j.progress_current,
                "progress_total": j.progress_total,
                "result_storage_key": j.result_storage_key,
                "error_code": j.error_code,
                "created_at": j.created_at.isoformat(),
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            }
            for j in jobs
        ]
        return Response({"jobs": data})

    elif request.method == "POST":
        kind = request.data.get("kind", "").strip()
        parameters = request.data.get("parameters", {})

        # Authority check based on job kind
        required_scope = "integrations:manage"
        if kind == "EXPORT_EVENT":
            required_scope = "data:export"
        elif kind == "VALIDATE_IMPORT" or kind == "APPLY_IMPORT":
            required_scope = "data:import"
        elif kind == "GENERATE_CERTIFICATES":
            required_scope = "credentials:issue"

        enforce_scope_and_role(request, event, required_scope=required_scope, required_role=EventMembership.Role.ORGANISER)

        try:
            job = submit_background_job(event, request.user, kind=kind, parameters=parameters)
            return Response(
                {
                    "id": str(job.id),
                    "kind": job.kind,
                    "state": job.state,
                    "created_at": job.created_at.isoformat(),
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_background_job_detail(request, slug, job_id):
    """
    GET: Retrieves the status and progress of a background job.
    """
    event = get_object_or_404(Event, slug=slug)
    job = get_object_or_404(BackgroundJob, id=job_id, event=event)

    is_organiser = EventMembership.objects.filter(
        event=event, user=request.user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
    ).exists() or request.user.is_staff

    if job.requested_by != request.user and not is_organiser:
        raise PermissionDenied("You do not have permission to inspect this job.")

    return Response({
        "id": str(job.id),
        "kind": job.kind,
        "state": job.state,
        "attempts": job.attempts,
        "progress_current": job.progress_current,
        "progress_total": job.progress_total,
        "result_storage_key": job.result_storage_key,
        "result_sha256": job.result_sha256,
        "error_code": job.error_code,
        "error_details": job.error_details,
        "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    })


def organiser_webhooks_page(request, slug):
    """
    Organiser UI for inspecting and managing webhook endpoints and deliveries.
    """
    if not request.user.is_authenticated:
        return redirect(f"/login/?next=/events/{slug}/manage/webhooks/")

    event = get_object_or_404(Event, slug=slug)
    membership = EventMembership.objects.filter(
        event=event, user=request.user, status=EventMembership.Status.ACTIVE
    ).first()

    if not membership or (membership.role != EventMembership.Role.ORGANISER and not request.user.is_staff):
        return render(request, "portal/forbidden.html", {"message": "Organiser privileges required."}, status=403)

    endpoints = WebhookEndpoint.objects.filter(event=event).order_by("-created_at")
    recent_deliveries = (
        WebhookDelivery.objects.filter(endpoint__event=event)
        .select_related("endpoint", "domain_event")
        .order_by("-created_at")[:50]
    )

    context = {
        "event": event,
        "endpoints": endpoints,
        "recent_deliveries": recent_deliveries,
        "permitted_event_types": PERMITTED_WEBHOOK_EVENT_TYPES,
    }
    return render(request, "portal/organiser_webhooks.html", context)


def api_openapi_schema(request):
    """Machine-readable OpenAPI 3.1 schema."""
    return JsonResponse(get_openapi_schema())


def api_docs_ui(request):
    """Local offline interactive documentation UI."""
    return HttpResponse(render_local_docs_html(), content_type="text/html")
