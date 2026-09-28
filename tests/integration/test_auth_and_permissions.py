import os
import pytest
from django.conf import settings
from django.test import Client
from apps.accounts.models import User
from apps.events.models import Event, EventMembership
from apps.imports.importer import FixtureImporter


@pytest.fixture(autouse=True)
def setup_fixture_and_users(db):
    fixture_path = os.path.join(settings.BASE_DIR.parent.parent, "fixtures", "fixtures.json")
    importer = FixtureImporter(fixture_path)
    importer.import_fixture(force=False)

    event = Event.objects.get(slug="sample-hack-2026")

    # Ensure users and roles exist
    org = User.objects.filter(email="organizer@example.org").first()
    if not org:
        org = User.objects.create_user("organizer@example.org", "Organiser", "pass1234", is_staff=True)
    EventMembership.objects.get_or_create(event=event, user=org, defaults={"role": EventMembership.Role.ORGANISER})

    judge_a = User.objects.filter(email="tomas.varga@example.org").first()
    if judge_a:
        judge_a.set_password("pass1234")
        judge_a.save()

    judge_b = User.objects.filter(email="mariana.costa@example.org").first()
    if judge_b:
        judge_b.set_password("pass1234")
        judge_b.save()

    participant = User.objects.filter(email="priya1@example.org").first()
    if participant:
        participant.set_password("pass1234")
        participant.save()


@pytest.mark.django_db
def test_gallery_is_public():
    client = Client()
    resp = client.get("/projects")
    assert resp.status_code == 200

    # Ensure fixture project titles appear in initial HTML
    content = resp.content.decode("utf-8").lower()
    assert "glass signal" in content
    assert "dry harbour" in content


@pytest.mark.django_db
def test_closed_event_refuses_submission():
    client = Client()
    # Log in as participant
    client.login(username="priya1@example.org", password="pass1234")

    # Attempt to post a late submission to the closed event
    resp = client.post(
        "/projects/new",
        {"title": "Late Project", "summary": "Probe"},
        content_type="application/json",
    )
    # Must be refused with 4xx
    assert 400 <= resp.status_code < 500


@pytest.mark.django_db
def test_judge_sees_own_scores():
    client = Client()
    client.login(username="tomas.varga@example.org", password="pass1234")

    resp = client.get("/api/judge/scores")
    assert resp.status_code == 200
    data = resp.json()
    assert data["judge"] == "Tomas Varga"
    assert "scores" in data


@pytest.mark.django_db
def test_judge_cannot_see_peer_scores():
    client = Client()
    client.login(username="mariana.costa@example.org", password="pass1234")

    # Attempt peer scores probe
    resp = client.get("/api/judge/scores?judge=judge_a")
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_participant_blocked_from_judge_scores():
    client = Client()
    client.login(username="priya1@example.org", password="pass1234")

    resp = client.get("/api/judge/scores")
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_csv_export_permissions():
    client = Client()

    # Participant forbidden
    client.login(username="priya1@example.org", password="pass1234")
    resp = client.get("/api/export.csv")
    assert resp.status_code == 403

    # Organiser permitted
    client.login(username="organizer@example.org", password="pass1234")
    resp = client.get("/api/export.csv")
    assert resp.status_code == 200
    csv_text = resp.content.decode("utf-8")
    assert "project_id,title,track,team,state,submitted_at,review_count" in csv_text
