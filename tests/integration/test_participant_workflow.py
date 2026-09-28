import os
import pytest
from django.conf import settings
from django.test import Client
from django.utils import timezone
from datetime import timedelta

from apps.accounts.models import User
from apps.events.models import Event, EventMembership, Track
from apps.imports.importer import FixtureImporter
from apps.submissions.models import Project, ProjectRevision
from apps.submissions.services import save_draft_submission, submit_project, StaleSaveConflict
from apps.teams.models import Team, TeamMember, TeamInvitation
from apps.teams.services import create_team, create_team_invitation, accept_team_invitation


@pytest.fixture(autouse=True)
def setup_test_environment(db):
    fixture_path = os.path.join(settings.BASE_DIR.parent.parent, "fixtures", "fixtures.json")
    importer = FixtureImporter(fixture_path)
    importer.import_fixture(force=False)

    # Create an open event for active testing
    now = timezone.now()
    open_event, _ = Event.objects.get_or_create(
        slug="active-hack-2026",
        defaults={
            "name": "Active Hackathon 2026",
            "tagline": "Test Hackathon for M02",
            "lifecycle": Event.Lifecycle.PUBLISHED,
            "registration_opens_at": now - timedelta(days=2),
            "registration_closes_at": now + timedelta(days=2),
            "submissions_opens_at": now - timedelta(hours=12),
            "submissions_closes_at": now + timedelta(days=2),
            "judging_opens_at": now + timedelta(days=2, hours=1),
            "judging_closes_at": now + timedelta(days=3),
            "min_team_size": 1,
            "max_team_size": 4,
        }
    )

    # Create a track for open_event
    Track.objects.get_or_create(
        event=open_event,
        name="General Track",
        defaults={
            "slug": "general-track",
            "description_md": "General track for active testing",
            "display_order": 1,
        }
    )

    # Participant users
    u1, _ = User.objects.get_or_create(email="alice@example.org", defaults={"display_name": "Alice"})
    u1.set_password("pass1234")
    u1.save()

    u2, _ = User.objects.get_or_create(email="bob@example.org", defaults={"display_name": "Bob"})
    u2.set_password("pass1234")
    u2.save()

    org, _ = User.objects.get_or_create(email="organizer@example.org", defaults={"display_name": "Organizer", "is_staff": True})
    org.set_password("pass1234")
    org.save()

    EventMembership.objects.get_or_create(event=open_event, user=u1, defaults={"role": EventMembership.Role.PARTICIPANT})
    EventMembership.objects.get_or_create(event=open_event, user=u2, defaults={"role": EventMembership.Role.PARTICIPANT})
    EventMembership.objects.get_or_create(event=open_event, user=org, defaults={"role": EventMembership.Role.ORGANISER})


@pytest.mark.django_db
def test_team_creation_and_invitation():
    event = Event.objects.get(slug="active-hack-2026")
    alice = User.objects.get(email="alice@example.org")
    bob = User.objects.get(email="bob@example.org")

    # Alice creates team
    team = create_team(alice, event, "Quantum Voyagers")
    assert team.captain_user == alice
    assert TeamMember.objects.filter(team=team, user=alice).exists()

    # Alice invites Bob
    inv, token = create_team_invitation(alice, team, "bob@example.org")
    assert inv.accepted_at is None
    assert inv.token_digest is not None

    # Bob accepts invitation
    member = accept_team_invitation(bob, token)
    assert member.team == team
    assert member.user == bob

    inv.refresh_from_db()
    assert inv.accepted_at is not None
    assert inv.accepted_by == bob


@pytest.mark.django_db
def test_draft_saving_and_optimistic_concurrency_conflict():
    event = Event.objects.get(slug="active-hack-2026")
    alice = User.objects.get(email="alice@example.org")
    team = create_team(alice, event, "Concurrency Test Team")

    # Initial save
    project, rev1 = save_draft_submission(
        user=alice,
        event=event,
        team=team,
        data={
            "title": "Quantum Sensor",
            "summary": "First revision summary",
            "description_md": "Full description of quantum sensor",
        },
        expected_version=None,
    )
    assert project.version == 1
    assert rev1.number == 1
    assert project.draft_revision == rev1

    # Second save with correct version
    project, rev2 = save_draft_submission(
        user=alice,
        event=event,
        team=team,
        data={
            "title": "Quantum Sensor v2",
            "summary": "Second revision summary",
        },
        expected_version=1,
        project_id=str(project.id),
    )
    assert project.version == 2
    assert rev2.number == 2

    # Third save with STALE version 1 -> Must raise StaleSaveConflict
    with pytest.raises(StaleSaveConflict) as excinfo:
        save_draft_submission(
            user=alice,
            event=event,
            team=team,
            data={"title": "Conflicting edit"},
            expected_version=1,  # Stale! Current version is 2
            project_id=str(project.id),
        )
    assert excinfo.value.current_version == 2


@pytest.mark.django_db
def test_captain_submission_and_roster_snapshot():
    event = Event.objects.get(slug="active-hack-2026")
    alice = User.objects.get(email="alice@example.org")
    bob = User.objects.get(email="bob@example.org")

    team = create_team(alice, event, "Apollo Crew")
    inv, token = create_team_invitation(alice, team, "bob@example.org")
    accept_team_invitation(bob, token)

    project, _ = save_draft_submission(
        user=alice,
        event=event,
        team=team,
        data={
            "title": "Lunar Rover",
            "summary": "Autonomous navigation on the moon",
            "description_md": "Details about autonomous lunar rover navigation.",
            "repo_url": "https://github.com/example/lunar-rover",
        },
    )

    # Bob (member, non-captain) attempts to submit -> Must be denied
    with pytest.raises(Exception) as exc:
        submit_project(bob, event, str(project.id))
    assert "captain" in str(exc.value).lower()

    # Alice (captain) submits -> Success
    project, receipt = submit_project(alice, event, str(project.id))
    assert project.state == Project.State.SUBMITTED
    assert receipt["project_id"] == str(project.id)
    assert receipt["submitted_by"] == alice.email
    assert len(receipt["roster"]) == 2

    emails = [m["email"] for m in receipt["roster"]]
    assert "alice@example.org" in emails
    assert "bob@example.org" in emails


@pytest.mark.django_db
def test_late_submission_cutoff():
    # sample-hack-2026 from fixture is closed (submissions_closes_at in past)
    closed_event = Event.objects.get(slug="sample-hack-2026")
    team = Team.objects.filter(event=closed_event).first()
    captain = team.captain_user

    # Attempting to save draft or submit should fail cutoff check
    with pytest.raises(Exception) as exc:
        save_draft_submission(
            user=captain,
            event=closed_event,
            team=team,
            data={"title": "Late Project Edit"},
        )
    assert "closed" in str(exc.value).lower()


@pytest.mark.django_db
def test_api_draft_save_conflict_returns_409():
    client = Client()
    client.login(username="alice@example.org", password="pass1234")
    event = Event.objects.get(slug="active-hack-2026")
    alice = User.objects.get(email="alice@example.org")
    team = create_team(alice, event, "API Conflict Team")

    # Initial save via API
    resp1 = client.post(
        "/api/v1/workspace/save-draft/",
        data={
            "event_id": str(event.id),
            "title": "Initial Title",
            "summary": "Initial Summary",
        },
        content_type="application/json",
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["version"] == 1
    project_id = data1["project_id"]

    # Second save advancing version to 2
    resp2 = client.post(
        "/api/v1/workspace/save-draft/",
        data={
            "event_id": str(event.id),
            "project_id": project_id,
            "version": 1,
            "title": "Second Title",
        },
        content_type="application/json",
    )
    assert resp2.status_code == 200
    assert resp2.json()["version"] == 2

    # Conflicting save sending stale version 1
    resp3 = client.post(
        "/api/v1/workspace/save-draft/",
        data={
            "event_id": str(event.id),
            "project_id": project_id,
            "version": 1,
            "title": "Conflicting Title",
        },
        content_type="application/json",
    )
    assert resp3.status_code == 409
    data3 = resp3.json()
    assert data3["error"] == "STALE_SAVE_CONFLICT"
    assert data3["server_version"] == 2


@pytest.mark.django_db
def test_organiser_event_settings_update():
    client = Client()
    event = Event.objects.get(slug="active-hack-2026")

    # Non-organiser participant cannot update
    client.login(username="alice@example.org", password="pass1234")
    resp_forbidden = client.post(
        f"/api/v1/events/{event.slug}/update-settings/",
        data={"name": "Hacked Name"},
        content_type="application/json",
    )
    assert resp_forbidden.status_code == 403

    # Organiser can update
    client.login(username="organizer@example.org", password="pass1234")
    resp_ok = client.post(
        f"/api/v1/events/{event.slug}/update-settings/",
        data={
            "name": "Updated Hackathon Name",
            "tagline": "New exciting tagline",
            "min_team_size": 2,
            "max_team_size": 5,
        },
        content_type="application/json",
    )
    assert resp_ok.status_code == 200
    assert resp_ok.json()["status"] == "UPDATED"

    event.refresh_from_db()
    assert event.name == "Updated Hackathon Name"
    assert event.tagline == "New exciting tagline"
    assert event.min_team_size == 2
    assert event.max_team_size == 5
    assert event.version > 1
