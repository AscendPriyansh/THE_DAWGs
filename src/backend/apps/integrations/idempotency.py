import functools
import hashlib
from datetime import timedelta
from django.db import transaction, IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from apps.events.models import Event
from apps.integrations.models import IdempotencyRecord


def execute_with_idempotency(request, event: Event, route_key: str, idempotency_key: str, action_func):
    """
    Executes a mutation with idempotency guarantee.
    - If key does not exist: creates PENDING record, executes action_func, commits result.
    - If key exists with same payload in COMMITTED state: replays cached response.
    - If key exists with different payload: returns 409 Conflict.
    - If key exists in PENDING state: returns 409 Conflict (retryable).
    """
    raw_body = request.body if hasattr(request, "body") else b""
    request_sha256 = hashlib.sha256(raw_body).hexdigest()

    actor_key = (
        f"key:{request.api_credential.id}"
        if getattr(request, "api_credential", None)
        else f"user:{request.user.id}"
    )

    now = timezone.now()
    expires_at = now + timedelta(hours=24)

    # 1. Check existing record
    existing = IdempotencyRecord.objects.filter(
        credential_or_actor_key=actor_key,
        event=event,
        route_key=route_key,
        idempotency_key=idempotency_key,
    ).first()

    if existing:
        if existing.state == IdempotencyRecord.State.COMMITTED:
            if existing.request_sha256 != request_sha256:
                return Response(
                    {"detail": "Idempotency key reused with different request payload."},
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(existing.response_json, status=existing.response_status)
        elif existing.state == IdempotencyRecord.State.PENDING:
            return Response(
                {"detail": "An operation with this idempotency key is already in progress. Please retry shortly."},
                status=status.HTTP_409_CONFLICT,
            )

    # 2. Insert PENDING record
    record = None
    try:
        with transaction.atomic():
            record = IdempotencyRecord.objects.create(
                credential_or_actor_key=actor_key,
                event=event,
                route_key=route_key,
                idempotency_key=idempotency_key,
                request_sha256=request_sha256,
                state=IdempotencyRecord.State.PENDING,
                expires_at=expires_at,
            )
    except IntegrityError:
        # Race condition: concurrent insert
        return Response(
            {"detail": "A concurrent operation with this idempotency key is already in progress."},
            status=status.HTTP_409_CONFLICT,
        )

    # 3. Execute mutation
    try:
        response = action_func()
        # Save response on 2xx or 3xx
        if 200 <= response.status_code < 400:
            record.state = IdempotencyRecord.State.COMMITTED
            record.response_status = response.status_code
            record.response_json = getattr(response, "data", None)
            record.save(update_fields=["state", "response_status", "response_json"])
        else:
            # Failure: delete or mark failed so client can retry
            record.state = IdempotencyRecord.State.FAILED
            record.response_status = response.status_code
            record.save(update_fields=["state", "response_status"])
        return response
    except Exception as e:
        record.state = IdempotencyRecord.State.FAILED
        record.save(update_fields=["state"])
        raise e


def idempotent_view(route_key: str):
    """
    Decorator for DRF API views that checks for 'Idempotency-Key' header.
    """
    def decorator(view_func):
        @functools.wraps(view_func)
        def wrapper(request, *args, **kwargs):
            idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
            if not idempotency_key:
                return view_func(request, *args, **kwargs)

            # Resolve event from slug in kwargs
            slug = kwargs.get("slug")
            if not slug:
                return view_func(request, *args, **kwargs)

            event = get_object_or_404(Event, slug=slug)

            def action():
                return view_func(request, *args, **kwargs)

            return execute_with_idempotency(request, event, route_key, idempotency_key, action)

        return wrapper
    return decorator
