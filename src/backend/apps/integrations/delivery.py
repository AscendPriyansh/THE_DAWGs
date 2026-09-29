import json
import logging
import random
import time
import urllib.request
import urllib.error
from datetime import timedelta
from typing import Optional, Tuple, Dict, Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.integrations.models import WebhookDelivery, WebhookAttempt, WebhookEndpoint
from apps.integrations.crypto import decrypt_webhook_secret, sign_webhook_payload
from apps.integrations.ssrf import validate_webhook_url
from apps.audit.models import AuditEvent

logger = logging.getLogger(__name__)

# Retry backoff in seconds for up to 7 retries (8 total attempts per generation)
RETRY_INTERVALS = [5, 30, 120, 600, 3600, 21600, 86400]
MAX_ATTEMPTS_PER_GENERATION = 8


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def claim_next_webhook_delivery(
    worker_id: str,
    lease_duration_seconds: int = 60,
) -> Optional[WebhookDelivery]:
    """
    Claims the next due webhook delivery using SELECT ... FOR UPDATE SKIP LOCKED.
    Assigns lease to worker_id and returns the delivery, or None if no due deliveries exist.
    """
    now = timezone.now()
    with transaction.atomic():
        delivery = (
            WebhookDelivery.objects.select_for_update(of=("self",), skip_locked=True)
            .filter(
                (
                    Q(state__in=[WebhookDelivery.State.PENDING, WebhookDelivery.State.RETRY])
                    & Q(next_attempt_at__lte=now)
                    & (Q(lease_until__isnull=True) | Q(lease_until__lte=now))
                )
                | (
                    Q(state=WebhookDelivery.State.LEASED)
                    & Q(lease_until__lte=now)
                )
            )
            .order_by("next_attempt_at")
            .first()
        )
        if not delivery:
            return None

        delivery.state = WebhookDelivery.State.LEASED
        delivery.lease_owner = worker_id
        delivery.lease_until = now + timedelta(seconds=lease_duration_seconds)
        delivery.save(update_fields=["state", "lease_owner", "lease_until"])
        return delivery


def execute_webhook_delivery(
    delivery_id: str,
    worker_id: str,
    mock_sender: Optional[Any] = None,
) -> bool:
    """
    Performs outbound HTTP POST outside database transactions.
    Records delivery attempt and updates state machine upon completion if lease is still owned.
    """
    try:
        delivery = WebhookDelivery.objects.select_related(
            "domain_event", "endpoint", "endpoint__event"
        ).get(id=delivery_id)
    except WebhookDelivery.DoesNotExist:
        return False

    endpoint = delivery.endpoint

    # If endpoint paused or cancelled
    if endpoint.status != WebhookEndpoint.Status.ACTIVE:
        with transaction.atomic():
            d = WebhookDelivery.objects.select_for_update().filter(id=delivery_id).first()
            if d and d.lease_owner == worker_id:
                d.state = WebhookDelivery.State.CANCELLED
                d.lease_owner = None
                d.lease_until = None
                d.save(update_fields=["state", "lease_owner", "lease_until"])
        return False

    # SSRF Check
    is_safe, ssrf_err, resolved_ips = validate_webhook_url(endpoint.url)
    if not is_safe:
        now = timezone.now()
        with transaction.atomic():
            d = WebhookDelivery.objects.select_for_update().filter(id=delivery_id).first()
            if d and d.lease_owner == worker_id:
                attempt_num = d.lifetime_attempt_count + 1
                WebhookAttempt.objects.create(
                    delivery=d,
                    attempt_number=attempt_num,
                    started_at=now,
                    finished_at=now,
                    response_status=None,
                    response_headers_json={},
                    response_body_redacted=f"SSRF validation blocked delivery: {ssrf_err}"[:500],
                    error_class="SSRF_BLOCKED",
                    request_timestamp=now,
                )
                d.state = WebhookDelivery.State.DEAD
                d.lifetime_attempt_count = attempt_num
                d.attempts_in_generation += 1
                d.last_status = None
                d.last_error_class = "SSRF_BLOCKED"
                d.lease_owner = None
                d.lease_until = None
                d.save()
        return False

    # Prepare Payload and Sign
    now = timezone.now()
    timestamp_str = str(int(now.timestamp()))
    payload_body = {
        "id": str(delivery.domain_event.id),
        "event_id": str(delivery.domain_event.event_id),
        "type": delivery.domain_event.type,
        "schema_version": delivery.domain_event.schema_version,
        "entity_id": str(delivery.domain_event.entity_id),
        "timestamp": delivery.domain_event.created_at.isoformat(),
        "delivery_id": str(delivery.id),
        "payload": delivery.domain_event.payload,
    }
    raw_body_bytes = json.dumps(payload_body, separators=(",", ":"), sort_keys=True).encode("utf-8")

    secret = decrypt_webhook_secret(endpoint.secret_encrypted)
    signature_header = sign_webhook_payload(secret, timestamp_str, raw_body_bytes)

    req_headers = {
        "Content-Type": "application/json",
        "User-Agent": "Dogfood-Webhook-Delivery/1.0",
        "Dogfood-Event-Id": str(delivery.domain_event.id),
        "Dogfood-Event-Type": delivery.domain_event.type,
        "Dogfood-Delivery-Id": str(delivery.id),
        "Dogfood-Timestamp": timestamp_str,
        "Dogfood-Signature": signature_header,
    }

    started_at = timezone.now()
    response_status: Optional[int] = None
    response_headers: Dict[str, str] = {}
    response_body = ""
    error_class: Optional[str] = None
    retry_after_seconds: Optional[int] = None

    if mock_sender is not None:
        # For unit testing without socket I/O
        response_status, response_headers, response_body, error_class = mock_sender(
            endpoint.url, req_headers, raw_body_bytes
        )
    else:
        # Standard HTTP POST with connect timeout 5s, read timeout 10s, max 64KB response body
        connect_timeout = getattr(settings, "WEBHOOK_TIMEOUT_CONNECT", 5)
        read_timeout = getattr(settings, "WEBHOOK_TIMEOUT_READ", 10)
        max_bytes = getattr(settings, "WEBHOOK_MAX_RESPONSE_BYTES", 65536)

        opener = urllib.request.build_opener(NoRedirectHandler)
        req = urllib.request.Request(
            endpoint.url,
            data=raw_body_bytes,
            headers=req_headers,
            method="POST",
        )
        try:
            with opener.open(req, timeout=connect_timeout + read_timeout) as resp:
                response_status = resp.status
                response_headers = {k: v for k, v in resp.headers.items()}
                raw_resp = resp.read(max_bytes + 1)
                if len(raw_resp) > max_bytes:
                    raw_resp = raw_resp[:max_bytes] + b" [TRUNCATED]"
                response_body = raw_resp.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            response_status = e.code
            response_headers = {k: v for k, v in e.headers.items()}
            try:
                raw_resp = e.read(max_bytes + 1)
                if len(raw_resp) > max_bytes:
                    raw_resp = raw_resp[:max_bytes] + b" [TRUNCATED]"
                response_body = raw_resp.decode("utf-8", errors="replace")
            except Exception:
                response_body = str(e)
            error_class = f"HTTP_{e.code}"
        except urllib.error.URLError as e:
            response_status = None
            response_body = f"Network connection error: {e.reason}"
            error_class = "NETWORK_ERROR"
        except TimeoutError:
            response_status = 408
            response_body = "Request timed out"
            error_class = "TIMEOUT"
        except Exception as e:
            response_status = None
            response_body = f"Unexpected delivery error: {e}"
            error_class = "CLIENT_ERROR"

    finished_at = timezone.now()

    # Parse Retry-After if 429 or 503
    if response_status in (429, 503) and "Retry-After" in response_headers:
        try:
            val = int(response_headers["Retry-After"])
            if 0 < val <= 86400:
                retry_after_seconds = val
        except (ValueError, TypeError):
            pass

    # Evaluate State Machine Transitions
    # Success: 2xx
    is_success = response_status is not None and (200 <= response_status < 300)
    # Retryable: Network error, 408, 429, 5xx
    is_retryable = (
        not is_success
        and (
            response_status is None
            or response_status == 408
            or response_status == 429
            or (500 <= response_status < 600)
        )
    )

    with transaction.atomic():
        d = WebhookDelivery.objects.select_for_update().filter(id=delivery_id).first()
        if not d:
            return False

        # Verify lease ownership
        if d.lease_owner != worker_id or (d.lease_until and timezone.now() > d.lease_until):
            logger.warning(
                "Lease lost or expired for delivery %s (worker: %s, owner: %s)",
                delivery_id,
                worker_id,
                d.lease_owner,
            )
            return False

        # Record attempt
        next_attempt_number = d.lifetime_attempt_count + 1
        WebhookAttempt.objects.create(
            delivery=d,
            attempt_number=next_attempt_number,
            started_at=started_at,
            finished_at=finished_at,
            response_status=response_status,
            response_headers_json=response_headers,
            response_body_redacted=response_body[:5000],  # Bound stored attempt size
            error_class=error_class,
            request_timestamp=started_at,
        )

        d.lifetime_attempt_count = next_attempt_number
        d.attempts_in_generation += 1
        d.last_status = response_status
        d.last_error_class = error_class

        if is_success:
            d.state = WebhookDelivery.State.SUCCEEDED
            d.lease_owner = None
            d.lease_until = None
            d.save()
            return True

        if is_retryable:
            if d.attempts_in_generation < MAX_ATTEMPTS_PER_GENERATION:
                # Calculate backoff delay
                idx = min(d.attempts_in_generation - 1, len(RETRY_INTERVALS) - 1)
                base_interval = RETRY_INTERVALS[idx]
                # Apply 10% jitter
                jittered = base_interval * random.uniform(0.9, 1.1)
                if retry_after_seconds:
                    backoff_delay = max(jittered, retry_after_seconds)
                else:
                    backoff_delay = jittered

                d.state = WebhookDelivery.State.RETRY
                d.next_attempt_at = timezone.now() + timedelta(seconds=backoff_delay)
                d.lease_owner = None
                d.lease_until = None
                d.save()
                return False
            else:
                # Exhausted retries
                d.state = WebhookDelivery.State.DEAD
                d.lease_owner = None
                d.lease_until = None
                d.save()
                return False
        else:
            # Terminal non-retryable error (e.g. 400, 401, 403, 404, 422)
            d.state = WebhookDelivery.State.DEAD
            d.lease_owner = None
            d.lease_until = None
            d.save()
            return False


def replay_webhook_delivery(delivery_id: str, actor_user) -> WebhookDelivery:
    """
    Resets retry budget and generation for a delivery.
    Preserves lifetime attempt history. Rejects replay if delivery is actively leased or endpoint paused.
    """
    with transaction.atomic():
        delivery = (
            WebhookDelivery.objects.select_for_update()
            .select_related("endpoint", "endpoint__event")
            .get(id=delivery_id)
        )

        if delivery.endpoint.status != WebhookEndpoint.Status.ACTIVE:
            raise ValidationError("Cannot replay delivery for a paused or inactive endpoint.")

        # Prevent concurrent active lease replay
        if (
            delivery.state == WebhookDelivery.State.LEASED
            and delivery.lease_until
            and delivery.lease_until > timezone.now()
        ):
            raise ValidationError("Cannot replay delivery currently leased by an active worker.")

        delivery.replay_generation += 1
        delivery.attempts_in_generation = 0
        delivery.state = WebhookDelivery.State.PENDING
        delivery.next_attempt_at = timezone.now()
        delivery.lease_owner = None
        delivery.lease_until = None
        delivery.last_error_class = None
        delivery.save()

        # Audit replay
        AuditEvent.objects.create(
            event=delivery.endpoint.event,
            actor_user=actor_user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="WEBHOOK_DELIVERY_REPLAYED",
            entity_type="WebhookDelivery",
            entity_id=delivery.id,
            after_json={
                "delivery_id": str(delivery.id),
                "generation": delivery.replay_generation,
                "lifetime_attempts": delivery.lifetime_attempt_count,
            },
            reason="Organiser triggered delivery replay",
        )

        return delivery
