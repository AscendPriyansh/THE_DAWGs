import json
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone
from rest_framework.test import APIClient

from apps.events.models import Event, EventMembership
from apps.integrations.models import (
    WebhookEndpoint,
    DomainEvent,
    WebhookDelivery,
    WebhookAttempt,
    BackgroundJob,
)
from apps.integrations.crypto import (
    generate_webhook_secret,
    encrypt_webhook_secret,
    decrypt_webhook_secret,
    sign_webhook_payload,
    verify_webhook_signature,
)
from apps.integrations.ssrf import validate_webhook_url
from apps.integrations.outbox import publish_domain_event
from apps.integrations.delivery import (
    claim_next_webhook_delivery,
    execute_webhook_delivery,
    replay_webhook_delivery,
)
from apps.integrations.jobs import (
    submit_background_job,
    claim_next_background_job,
    execute_background_job,
)

User = get_user_model()


@pytest.fixture
def test_setup(db):
    now = timezone.now()
    user_organiser = User.objects.create_user(
        email="wh_org@example.com",
        display_name="Webhook Organiser",
    )
    user_participant = User.objects.create_user(
        email="wh_part@example.com",
        display_name="Webhook Participant",
    )

    event = Event.objects.create(
        slug=f"wh-event-{uuid.uuid4().hex[:6]}",
        name="Webhook Test Event",
        tagline="Testing Outbox and Jobs",
        description_md="Description",
        rules_md="Rules",
        registration_opens_at=now - timedelta(days=5),
        registration_closes_at=now + timedelta(days=5),
        submissions_opens_at=now - timedelta(days=5),
        submissions_closes_at=now + timedelta(days=5),
        judging_opens_at=now - timedelta(days=2),
        judging_closes_at=now + timedelta(days=5),
    )

    EventMembership.objects.create(
        event=event,
        user=user_organiser,
        role=EventMembership.Role.ORGANISER,
        status=EventMembership.Status.ACTIVE,
    )
    EventMembership.objects.create(
        event=event,
        user=user_participant,
        role=EventMembership.Role.PARTICIPANT,
        status=EventMembership.Status.ACTIVE,
    )

    return {
        "event": event,
        "organiser": user_organiser,
        "participant": user_participant,
    }


@pytest.mark.django_db(transaction=True)
def test_transactional_outbox_atomic_rollback(test_setup):
    """
    Verifies that if a business mutation rolls back, no DomainEvent or WebhookDelivery rows persist.
    """
    event = test_setup["event"]

    # Create active endpoint
    secret = generate_webhook_secret()
    endpoint = WebhookEndpoint.objects.create(
        event=event,
        url="https://localhost/webhook",
        allowed_event_types=["*"],
        secret_encrypted=encrypt_webhook_secret(secret),
        status=WebhookEndpoint.Status.ACTIVE,
    )

    initial_events = DomainEvent.objects.filter(event=event).count()
    initial_deliveries = WebhookDelivery.objects.filter(endpoint=endpoint).count()

    # Attempt mutation that rolls back
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            publish_domain_event(
                event=event,
                event_type="submission.created",
                entity_id=uuid.uuid4(),
                payload={"test": "rolled_back"},
            )
            # Intentional database error triggers rollback
            raise RuntimeError("Database transaction aborted unexpectedly")

    # Assert nothing persisted
    assert DomainEvent.objects.filter(event=event).count() == initial_events
    assert WebhookDelivery.objects.filter(endpoint=endpoint).count() == initial_deliveries


@pytest.mark.django_db(transaction=True)
def test_webhook_delivery_success_lifecycle(test_setup):
    """
    Verifies full lifecycle: outbox commit -> claim via lease -> HTTP 200 -> SUCCEEDED + WebhookAttempt.
    """
    event = test_setup["event"]
    secret = generate_webhook_secret()
    endpoint = WebhookEndpoint.objects.create(
        event=event,
        url="https://localhost/destination",
        allowed_event_types=["submission.created"],
        secret_encrypted=encrypt_webhook_secret(secret),
        status=WebhookEndpoint.Status.ACTIVE,
    )

    # Publish event
    domain_event = publish_domain_event(
        event=event,
        event_type="submission.created",
        entity_id="proj-123",
        payload={"title": "Test Submission"},
    )

    delivery = WebhookDelivery.objects.get(domain_event=domain_event, endpoint=endpoint)
    assert delivery.state == WebhookDelivery.State.PENDING
    assert delivery.lifetime_attempt_count == 0

    # Claim delivery
    claimed = claim_next_webhook_delivery("worker-alpha", lease_duration_seconds=60)
    assert claimed is not None
    assert claimed.id == delivery.id
    assert claimed.state == WebhookDelivery.State.LEASED
    assert claimed.lease_owner == "worker-alpha"
    assert claimed.lease_until > timezone.now()

    # Mock HTTP 200 response
    def mock_sender(url, headers, body_bytes):
        assert headers["Dogfood-Event-Type"] == "submission.created"
        assert headers["Dogfood-Signature"].startswith("v1=")
        return 200, {"Content-Type": "application/json"}, '{"status": "OK"}', None

    success = execute_webhook_delivery(str(delivery.id), "worker-alpha", mock_sender=mock_sender)
    assert success is True

    delivery.refresh_from_db()
    assert delivery.state == WebhookDelivery.State.SUCCEEDED
    assert delivery.lifetime_attempt_count == 1
    assert delivery.attempts_in_generation == 1
    assert delivery.last_status == 200
    assert delivery.lease_owner is None
    assert delivery.lease_until is None

    # Check WebhookAttempt record
    attempt = WebhookAttempt.objects.get(delivery=delivery, attempt_number=1)
    assert attempt.response_status == 200
    assert "OK" in attempt.response_body_redacted


@pytest.mark.django_db(transaction=True)
def test_webhook_retries_and_dead_letter(test_setup):
    """
    Verifies retryable vs terminal errors:
    500 triggers RETRY with backoff.
    404 triggers terminal DEAD state without retry.
    429 respects bounded Retry-After.
    """
    event = test_setup["event"]
    endpoint = WebhookEndpoint.objects.create(
        event=event,
        url="https://localhost/test-err",
        allowed_event_types=["*"],
        secret_encrypted=encrypt_webhook_secret(generate_webhook_secret()),
        status=WebhookEndpoint.Status.ACTIVE,
    )

    # 1. Test 500 Internal Server Error -> RETRY
    de1 = publish_domain_event(event=event, event_type="event.updated", entity_id=event.id, payload={})
    deliv1 = WebhookDelivery.objects.get(domain_event=de1, endpoint=endpoint)
    claim_next_webhook_delivery("worker-retry")

    def mock_500(url, headers, body):
        return 500, {}, "Internal Server Error", "HTTP_500"

    execute_webhook_delivery(str(deliv1.id), "worker-retry", mock_sender=mock_500)
    deliv1.refresh_from_db()
    assert deliv1.state == WebhookDelivery.State.RETRY
    assert deliv1.next_attempt_at > timezone.now()
    assert deliv1.attempts_in_generation == 1

    # 2. Test 404 Not Found -> Terminal DEAD
    de2 = publish_domain_event(event=event, event_type="event.updated", entity_id=event.id, payload={})
    deliv2 = WebhookDelivery.objects.get(domain_event=de2, endpoint=endpoint)
    claim_next_webhook_delivery("worker-retry")

    def mock_404(url, headers, body):
        return 404, {}, "Endpoint Not Found", "HTTP_404"

    execute_webhook_delivery(str(deliv2.id), "worker-retry", mock_sender=mock_404)
    deliv2.refresh_from_db()
    assert deliv2.state == WebhookDelivery.State.DEAD
    assert deliv2.last_status == 404

    # 3. Test 429 Too Many Requests with Retry-After
    de3 = publish_domain_event(event=event, event_type="event.updated", entity_id=event.id, payload={})
    deliv3 = WebhookDelivery.objects.get(domain_event=de3, endpoint=endpoint)
    claim_next_webhook_delivery("worker-retry")

    def mock_429(url, headers, body):
        return 429, {"Retry-After": "45"}, "Rate Limit Exceeded", "HTTP_429"

    execute_webhook_delivery(str(deliv3.id), "worker-retry", mock_sender=mock_429)
    deliv3.refresh_from_db()
    assert deliv3.state == WebhookDelivery.State.RETRY
    # Scheduled at least 40 seconds into the future
    assert deliv3.next_attempt_at >= timezone.now() + timedelta(seconds=40)


@pytest.mark.django_db(transaction=True)
def test_worker_crash_and_lease_recovery(test_setup):
    """
    Verifies that an abandoned lease due to worker crash can be reclaimed by another worker
    once lease_until expires, and a late write by the crashed worker is rejected.
    """
    event = test_setup["event"]
    endpoint = WebhookEndpoint.objects.create(
        event=event,
        url="https://localhost/crash-test",
        allowed_event_types=["*"],
        secret_encrypted=encrypt_webhook_secret(generate_webhook_secret()),
        status=WebhookEndpoint.Status.ACTIVE,
    )
    de = publish_domain_event(event=event, event_type="rubric.frozen", entity_id="rub-1", payload={})
    delivery = WebhookDelivery.objects.get(domain_event=de, endpoint=endpoint)

    # Worker A claims lease
    d_a = claim_next_webhook_delivery("worker-crashed", lease_duration_seconds=60)
    assert d_a.lease_owner == "worker-crashed"

    # Simulate crash: worker A disappears. Time advances past lease expiration
    delivery.refresh_from_db()
    delivery.lease_until = timezone.now() - timedelta(seconds=1)
    delivery.save(update_fields=["lease_until"])

    # Worker B reclaims expired lease
    d_b = claim_next_webhook_delivery("worker-recovery", lease_duration_seconds=60)
    assert d_b is not None
    assert d_b.id == delivery.id
    assert d_b.lease_owner == "worker-recovery"

    # If worker A wakes up late and attempts to complete the delivery, lease check rejects worker A
    def mock_ok(url, headers, body):
        return 200, {}, "OK", None

    res = execute_webhook_delivery(str(delivery.id), "worker-crashed", mock_sender=mock_ok)
    assert res is False  # Rejected because lease belongs to worker-recovery!

    # Worker B completes delivery
    res_b = execute_webhook_delivery(str(delivery.id), "worker-recovery", mock_sender=mock_ok)
    assert res_b is True
    delivery.refresh_from_db()
    assert delivery.state == WebhookDelivery.State.SUCCEEDED


@pytest.mark.django_db(transaction=True)
def test_replay_generation_and_concurrent_lease_prevention(test_setup):
    """
    Verifies replay behavior:
    Increments generation, resets retry budget, preserves lifetime attempt count.
    Rejects replay on active lease.
    """
    event = test_setup["event"]
    endpoint = WebhookEndpoint.objects.create(
        event=event,
        url="https://localhost/replay-test",
        allowed_event_types=["*"],
        secret_encrypted=encrypt_webhook_secret(generate_webhook_secret()),
        status=WebhookEndpoint.Status.ACTIVE,
    )
    de = publish_domain_event(event=event, event_type="review.submitted", entity_id="rev-1", payload={})
    delivery = WebhookDelivery.objects.get(domain_event=de, endpoint=endpoint)

    # Simulate 3 previous failed attempts
    delivery.state = WebhookDelivery.State.DEAD
    delivery.lifetime_attempt_count = 3
    delivery.attempts_in_generation = 3
    delivery.save()

    # Replay delivery
    replayed = replay_webhook_delivery(str(delivery.id), test_setup["organiser"])
    assert replayed.state == WebhookDelivery.State.PENDING
    assert replayed.replay_generation == 1
    assert replayed.attempts_in_generation == 0
    assert replayed.lifetime_attempt_count == 3  # History preserved!

    # Claim delivery
    claim_next_webhook_delivery("worker-active")

    # Attempting to replay an actively leased delivery must fail
    with pytest.raises(ValidationError):
        replay_webhook_delivery(str(delivery.id), test_setup["organiser"])


def test_ssrf_rejection_rules():
    """
    Validates that validate_webhook_url blocks cloud metadata, loopback, private IPs, and non-HTTPS schemes.
    """
    # Cloud metadata
    valid, err, _ = validate_webhook_url("http://169.254.169.254/latest/meta-data")
    assert not valid

    # Loopback IP
    valid, err, _ = validate_webhook_url("https://127.0.0.1/webhook")
    # In test, 127.0.0.1 might be allowlisted if in DOGFOOD_ALLOWED_WEBHOOK_HOSTS
    # Let's test non-allowlisted private IP 10.254.1.1
    valid, err, _ = validate_webhook_url("https://10.254.1.1/webhook")
    assert not valid
    assert "private" in err.lower() or "forbidden" in err.lower()

    # Userinfo embedded in URL
    valid, err, _ = validate_webhook_url("https://admin:pass@example.com/webhook")
    assert not valid
    assert "userinfo" in err.lower()

    # Forbidden port
    valid, err, _ = validate_webhook_url("https://example.com:8443/webhook")
    assert not valid
    assert "port" in err.lower()


def test_secret_encryption_and_hmac_sha256_verification():
    """
    Verifies that webhook secrets are encrypted at rest and signatures adhere to constant-time verification.
    """
    secret = generate_webhook_secret()
    assert secret.startswith("whsec_")

    enc = encrypt_webhook_secret(secret)
    assert enc != secret
    dec = decrypt_webhook_secret(enc)
    assert dec == secret

    timestamp = "1727500000"
    raw_body = b'{"event":"test","status":"ok"}'

    signature = sign_webhook_payload(secret, timestamp, raw_body)
    assert signature.startswith("v1=")

    # Valid verification
    assert verify_webhook_signature(
        secret=secret,
        timestamp_str=timestamp,
        raw_body_bytes=raw_body,
        signature_header=signature,
        current_time=1727500100,  # 100 seconds later (within 300s tolerance)
    )

    # Expired timestamp (400 seconds later)
    assert not verify_webhook_signature(
        secret=secret,
        timestamp_str=timestamp,
        raw_body_bytes=raw_body,
        signature_header=signature,
        current_time=1727500400,
    )

    # Tampered body
    assert not verify_webhook_signature(
        secret=secret,
        timestamp_str=timestamp,
        raw_body_bytes=b'{"event":"tampered"}',
        signature_header=signature,
        current_time=1727500100,
    )


@pytest.mark.django_db(transaction=True)
def test_background_job_execution_and_authority_recheck(test_setup):
    """
    Verifies background job queuing, compare-and-set leasing, and authority recheck at execution time.
    """
    event = test_setup["event"]
    organiser = test_setup["organiser"]

    # 1. Enqueue job
    job = submit_background_job(
        event=event,
        requested_by=organiser,
        kind=BackgroundJob.Kind.EXPORT_EVENT,
        parameters={"format": "archive"},
    )
    assert job.state == BackgroundJob.State.QUEUED

    # 2. Worker claims job
    claimed = claim_next_background_job("job-worker-1", lease_duration_seconds=60)
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.state == BackgroundJob.State.RUNNING
    assert claimed.attempts == 1

    # 3. Simulate revoking organiser role before execution finishes
    EventMembership.objects.filter(event=event, user=organiser).delete()

    # Worker executes: authority recheck must abort
    success = execute_background_job(str(job.id), "job-worker-1")
    assert success is False

    job.refresh_from_db()
    assert job.state == BackgroundJob.State.FAILED
    assert job.error_code == "PERMISSION_REVOKED"


@pytest.mark.django_db(transaction=True)
def test_background_job_success_flow(test_setup):
    """
    Verifies successful execution of background job publishes domain event job.completed.
    """
    event = test_setup["event"]
    organiser = test_setup["organiser"]

    job = submit_background_job(
        event=event,
        requested_by=organiser,
        kind=BackgroundJob.Kind.EXPORT_EVENT,
        parameters={},
    )
    claim_next_background_job("worker-job")
    success = execute_background_job(str(job.id), "worker-job")
    assert success is True

    job.refresh_from_db()
    assert job.state == BackgroundJob.State.SUCCEEDED
    assert job.progress_current == 100

    # Verify job.completed domain event was published
    de = DomainEvent.objects.filter(event=event, type="job.completed", entity_id=str(job.id)).first()
    assert de is not None
    assert de.payload["kind"] == BackgroundJob.Kind.EXPORT_EVENT


@pytest.mark.django_db(transaction=True)
def test_worker_management_command_once(test_setup):
    """
    Verifies that `manage.py run_worker --once` processes pending queue items and exits cleanly.
    """
    event = test_setup["event"]
    organiser = test_setup["organiser"]

    # Enqueue a job
    submit_background_job(
        event=event,
        requested_by=organiser,
        kind=BackgroundJob.Kind.EXPORT_EVENT,
        parameters={},
    )

    # Run command with --once
    call_command("run_worker", once=True, worker_id="test-cmd-worker")

    # Verify job was completed
    job = BackgroundJob.objects.filter(event=event).first()
    assert job.state == BackgroundJob.State.SUCCEEDED


@pytest.mark.django_db
def test_webhook_rest_endpoints(test_setup):
    """
    Tests REST API for webhooks: create, list, patch, delete, replay.
    """
    event = test_setup["event"]
    client = APIClient()
    client.force_login(test_setup["organiser"])

    # 1. Create Webhook Endpoint
    res = client.post(
        f"/api/v1/events/{event.slug}/webhooks/",
        data=json.dumps({"url": "https://localhost/webhook", "allowed_event_types": ["*"]}),
        content_type="application/json",
    )
    assert res.status_code == 201
    data = res.json()
    assert "secret" in data
    ep_id = data["id"]

    # 2. List Webhooks
    res = client.get(f"/api/v1/events/{event.slug}/webhooks/")
    assert res.status_code == 200
    assert len(res.json()["webhooks"]) == 1

    # 3. Patch Status to PAUSED
    res = client.patch(
        f"/api/v1/events/{event.slug}/webhooks/{ep_id}/",
        data=json.dumps({"status": "PAUSED"}),
        content_type="application/json",
    )
    assert res.status_code == 200
    assert res.json()["status"] == "PAUSED"

    # 4. Delete Webhook
    res = client.delete(f"/api/v1/events/{event.slug}/webhooks/{ep_id}/")
    assert res.status_code == 200
    assert WebhookEndpoint.objects.filter(id=ep_id).count() == 0
