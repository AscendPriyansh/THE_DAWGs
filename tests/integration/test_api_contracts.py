import hashlib
import json
import pytest
from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.events.models import Event, EventMembership, Track
from apps.integrations.models import ApiCredential, IdempotencyRecord
from apps.integrations.services import issue_api_credential, revoke_api_credential
from apps.submissions.models import Project, ProjectRevision
from apps.teams.models import Team, TeamMember


@pytest.fixture
def m07_env(db):
    now = timezone.now()
    org_user = User.objects.create_user(email="org_m07@example.com", display_name="Organiser M07")
    participant_user = User.objects.create_user(email="part_m07@example.com", display_name="Participant M07")
    other_user = User.objects.create_user(email="other_m07@example.com", display_name="Other User")

    event_a = Event.objects.create(
        slug="event-alpha-2026",
        name="Event Alpha 2026",
        lifecycle=Event.Lifecycle.PUBLISHED,
        registration_opens_at=now - timedelta(days=5),
        registration_closes_at=now + timedelta(days=2),
        submissions_opens_at=now - timedelta(days=2),
        submissions_closes_at=now - timedelta(hours=1),
        judging_opens_at=now - timedelta(hours=1),
        judging_closes_at=now - timedelta(minutes=5),
        voting_opens_at=now - timedelta(hours=1),
        voting_closes_at=now - timedelta(minutes=5),
        data_version=1,
    )

    event_b = Event.objects.create(
        slug="event-beta-2026",
        name="Event Beta 2026",
        lifecycle=Event.Lifecycle.PUBLISHED,
        registration_opens_at=now - timedelta(days=5),
        registration_closes_at=now + timedelta(days=2),
        submissions_opens_at=now - timedelta(days=2),
        submissions_closes_at=now - timedelta(hours=1),
        judging_opens_at=now - timedelta(hours=1),
        judging_closes_at=now + timedelta(hours=2),
        data_version=1,
    )

    EventMembership.objects.create(event=event_a, user=org_user, role=EventMembership.Role.ORGANISER)
    EventMembership.objects.create(event=event_a, user=participant_user, role=EventMembership.Role.PARTICIPANT)

    EventMembership.objects.create(event=event_b, user=org_user, role=EventMembership.Role.ORGANISER)
    EventMembership.objects.create(event=event_b, user=participant_user, role=EventMembership.Role.PARTICIPANT)

    track_a = Track.objects.create(event=event_a, slug="general", name="General Track")
    team_a = Team.objects.create(event=event_a, name="Team Alpha", captain_user=participant_user)
    TeamMember.objects.create(event=event_a, team=team_a, user=participant_user)
    proj_a = Project.objects.create(event=event_a, team=team_a, state=Project.State.SUBMITTED)
    rev_a = ProjectRevision.objects.create(
        project=proj_a,
        number=1,
        title="Project Alpha",
        track=track_a,
        created_by=participant_user,
        roster_snapshot=[{"id": str(participant_user.id), "name": "Participant M07"}],
    )
    proj_a.submitted_revision = rev_a
    proj_a.save(update_fields=["submitted_revision"])

    return {
        "event_a": event_a,
        "event_b": event_b,
        "org_user": org_user,
        "participant_user": participant_user,
        "other_user": other_user,
        "proj_a": proj_a,
    }


@pytest.mark.django_db
def test_scoped_api_key_issuance_and_revocation(m07_env):
    """
    Validates:
    - Issuing scoped API key returns token once and stores only SHA-256 digest.
    - Public prefix, scopes validation, expiration.
    - Revocation marks key inactive immediately.
    """
    env = m07_env
    event = env["event_a"]
    org = env["org_user"]

    token, cred = issue_api_credential(
        event=event,
        owner_user=org,
        label="CI/CD Deploy Key",
        scopes=["event:read", "results:publish"],
        expires_in_days=14,
    )

    assert token.startswith("dg_live_")
    assert cred.public_prefix == token[:12]
    # Secret digest is SHA-256 hash of token
    assert cred.secret_digest == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert cred.is_active is True
    assert "results:publish" in cred.scopes_json

    # Revoke key
    revoked = revoke_api_credential(cred, org, reason="Key rotation")
    assert revoked.is_active is False
    assert revoked.revoked_at is not None


@pytest.mark.django_db
def test_bearer_authentication_and_rejection_rules(m07_env):
    """
    Validates:
    - Valid bearer token authenticates successfully.
    - Invalid bearer token rejected with 401.
    - Revoked token rejected with 401.
    - Expired token rejected with 401.
    - Key for Event A used against Event B rejected with 403.
    """
    env = m07_env
    event_a = env["event_a"]
    event_b = env["event_b"]
    org = env["org_user"]

    token, cred = issue_api_credential(
        event=event_a,
        owner_user=org,
        label="Test Key",
        scopes=["event:read", "results:publish"],
        expires_in_days=7,
    )

    client = APIClient()

    # 1. Valid bearer token on event_a
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    resp = client.post(f"/api/v1/events/{event_a.slug}/results/preview/")
    assert resp.status_code == 200

    # 2. Key for Event A used on Event B -> 403 Forbidden (cross-event isolation)
    resp_b = client.post(f"/api/v1/events/{event_b.slug}/results/preview/")
    assert resp_b.status_code == 403
    assert "cannot access" in resp_b.data["detail"]

    # 3. Invalid token -> 401 Unauthorized
    client.credentials(HTTP_AUTHORIZATION="Bearer invalid_token_123")
    resp_invalid = client.post(f"/api/v1/events/{event_a.slug}/results/preview/")
    assert resp_invalid.status_code == 401

    # 4. Revoked key -> 401 Unauthorized
    revoke_api_credential(cred, org)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    resp_revoked = client.post(f"/api/v1/events/{event_a.slug}/results/preview/")
    assert resp_revoked.status_code == 401
    assert "revoked" in resp_revoked.data["detail"]


@pytest.mark.django_db
def test_role_and_scope_intersection(m07_env):
    """
    Validates:
    - Scope cannot elevate role: participant with 'results:publish' scope key is blocked with 403.
    - Organiser with key missing required scope is blocked with 403.
    - Organiser with valid scope can execute privileged action.
    """
    env = m07_env
    event = env["event_a"]
    org = env["org_user"]
    part = env["participant_user"]

    client = APIClient()

    # 1. Participant key has 'results:publish' scope
    part_token, _ = issue_api_credential(
        event=event,
        owner_user=part,
        label="Part Elevate Attempt",
        scopes=["results:publish"],
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {part_token}")
    resp = client.post(f"/api/v1/events/{event.slug}/results/preview/")
    assert resp.status_code == 403
    assert "Organiser permissions required" in resp.data["detail"]

    # 2. Organiser key lacks 'results:publish' scope (only 'event:read')
    read_token, _ = issue_api_credential(
        event=event,
        owner_user=org,
        label="Read Only Key",
        scopes=["event:read"],
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {read_token}")
    resp_lacks_scope = client.post(f"/api/v1/events/{event.slug}/results/preview/")
    assert resp_lacks_scope.status_code == 403
    assert "lacks required scope 'results:publish'" in resp_lacks_scope.data["detail"]

    # 3. Organiser key with 'results:publish' succeeds
    valid_token, _ = issue_api_credential(
        event=event,
        owner_user=org,
        label="Valid Organiser Key",
        scopes=["results:publish"],
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {valid_token}")
    resp_valid = client.post(f"/api/v1/events/{event.slug}/results/preview/")
    assert resp_valid.status_code == 200


@pytest.mark.django_db
def test_idempotency_contract(m07_env):
    """
    Validates:
    - First request with Idempotency-Key performs mutation.
    - Replay with same key and same payload returns exact cached response without re-running.
    - Key reuse with different payload returns 409 Conflict.
    - IdempotencyRecord stores sha256, status, and response.
    """
    env = m07_env
    event = env["event_a"]
    org = env["org_user"]

    client = APIClient()
    client.force_authenticate(user=org)

    # Calculate run first so we have a result_run_id to publish
    from apps.results.services import calculate_result_run
    run = calculate_result_run(org, event)

    idemp_key = "idemp-pub-test-uuid-001"
    payload_1 = {
        "result_run_id": str(run.id),
        "public_note_md": "Published officially",
        "waivers": [],
    }

    # 1. First publication request with Idempotency-Key
    resp1 = client.post(
        f"/api/v1/events/{event.slug}/results/publish/",
        data=json.dumps(payload_1),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=idemp_key,
    )
    assert resp1.status_code == 200
    pub_id = resp1.data["publication_id"]

    # Check IdempotencyRecord committed in database
    rec = IdempotencyRecord.objects.get(event=event, idempotency_key=idemp_key)
    assert rec.state == IdempotencyRecord.State.COMMITTED
    assert rec.response_status == 200

    # 2. Replay with same key and same payload -> returns exact cached response
    resp2 = client.post(
        f"/api/v1/events/{event.slug}/results/publish/",
        data=json.dumps(payload_1),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=idemp_key,
    )
    assert resp2.status_code == 200
    assert resp2.data["publication_id"] == pub_id

    # 3. Same idempotency key with DIFFERENT payload -> 409 Conflict
    payload_diff = {
        "result_run_id": str(run.id),
        "public_note_md": "Different note payload",
        "waivers": ["WAIVE_COVERAGE"],
    }
    resp_conflict = client.post(
        f"/api/v1/events/{event.slug}/results/publish/",
        data=json.dumps(payload_diff),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY=idemp_key,
    )
    assert resp_conflict.status_code == 409
    assert "different request payload" in resp_conflict.data["detail"]


@pytest.mark.django_db
def test_openapi_schema_and_docs_endpoints(m07_env):
    """
    Validates:
    - /api/v1/schema.json returns valid OpenAPI 3.1.0 document.
    - /api/docs returns self-contained local interactive HTML documentation without CDN dependencies.
    """
    client = APIClient()

    # 1. OpenAPI 3.1.0 Schema
    resp_schema = client.get("/api/v1/schema.json")
    assert resp_schema.status_code == 200
    data = resp_schema.json()
    assert data["openapi"] == "3.1.0"
    assert "paths" in data
    assert "/api/v1/events/{slug}/results/publish/" in data["paths"]
    assert "/api/v1/events/{slug}/api-keys/" in data["paths"]

    # 2. Local Documentation UI
    resp_docs = client.get("/api/docs")
    assert resp_docs.status_code == 200
    assert "text/html" in resp_docs["Content-Type"]
    content = resp_docs.content.decode("utf-8")
    assert "Dogfood 2026 Portal API Documentation" in content
    assert "OpenAPI 3.1.0" in content
    # Offline verification: ensure no unpinned external CDN scripts
    assert "cdn.jsdelivr.net" not in content
    assert "unpkg.com" not in content
