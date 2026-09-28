import os
import pytest
from decimal import Decimal
from datetime import timedelta
from django.conf import settings
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.events.models import Event, EventMembership, Track
from apps.imports.importer import FixtureImporter
from apps.judging.models import (
    Criterion,
    JudgeAssignment,
    JudgeTrackPermission,
    Review,
    ReviewScore,
    Rubric,
)
from apps.judging.services import (
    assign_judge_to_project,
    freeze_rubric,
    grant_judge_track_permission,
    submit_review,
)
from apps.results.models import Publication, ResultRow, ResultRun
from apps.results.services import calculate_result_run, publish_results
from apps.results.views import sanitize_csv_cell
from apps.submissions.models import Project, ProjectRevision
from apps.teams.models import Team, TeamMember


@pytest.fixture(autouse=True)
def setup_results_test_env(db):
    fixture_path = os.path.join(settings.BASE_DIR.parent.parent, "fixtures", "fixtures.json")
    importer = FixtureImporter(fixture_path)
    importer.import_fixture(force=False)

    now = timezone.now()
    event, _ = Event.objects.get_or_create(
        slug="results-test-hack",
        defaults={
            "name": "Results Test Hackathon",
            "lifecycle": Event.Lifecycle.PUBLISHED,
            "registration_opens_at": now - timedelta(days=6),
            "registration_closes_at": now - timedelta(days=3),
            "submissions_opens_at": now - timedelta(days=5),
            "submissions_closes_at": now - timedelta(days=2),
            "judging_opens_at": now - timedelta(days=2),
            "judging_closes_at": now - timedelta(hours=1),  # Closed judging window
            "min_team_size": 1,
            "max_team_size": 4,
            "required_reviews": 1,
        }
    )

    track, _ = Track.objects.get_or_create(
        event=event,
        name="General Track",
        defaults={"slug": "general-track", "description_md": "General track", "display_order": 1}
    )

    # Organiser, Judge, Participant
    org, _ = User.objects.get_or_create(email="results_org@example.org", defaults={"display_name": "Results Organiser", "is_staff": True})
    org.set_password("pass1234")
    org.save()

    judge, _ = User.objects.get_or_create(email="results_judge@example.org", defaults={"display_name": "Results Judge"})
    judge.set_password("pass1234")
    judge.save()

    pete, _ = User.objects.get_or_create(email="results_pete@example.org", defaults={"display_name": "Pete Participant"})
    pete.set_password("pass1234")
    pete.save()

    m_o, _ = EventMembership.objects.get_or_create(event=event, user=org, defaults={"role": EventMembership.Role.ORGANISER})
    m_j, _ = EventMembership.objects.get_or_create(event=event, user=judge, defaults={"role": EventMembership.Role.JUDGE})
    m_p, _ = EventMembership.objects.get_or_create(event=event, user=pete, defaults={"role": EventMembership.Role.PARTICIPANT})

    rubric, _ = Rubric.objects.get_or_create(
        event=event,
        version_number=1,
        defaults={"name": "Results Rubric", "created_by": org}
    )
    c1, _ = Criterion.objects.get_or_create(rubric=rubric, key="functionality", defaults={"label": "Functionality", "weight": Decimal("1.0")})
    c2, _ = Criterion.objects.get_or_create(rubric=rubric, key="quality", defaults={"label": "Quality", "weight": Decimal("1.0")})
    freeze_rubric(org, rubric)
    grant_judge_track_permission(org, m_j, track)

    # Create 2 submitted projects
    t1 = Team.objects.create(event=event, name="Alpha Team", captain_user=pete)
    p1 = Project.objects.create(event=event, team=t1, state=Project.State.SUBMITTED)
    r1 = ProjectRevision.objects.create(project=p1, number=1, track=track, title="Alpha Project", summary="First", created_by=pete)
    p1.submitted_revision = r1
    p1.save()

    t2 = Team.objects.create(event=event, name="Beta Team", captain_user=pete)
    p2 = Project.objects.create(event=event, team=t2, state=Project.State.SUBMITTED)
    r2 = ProjectRevision.objects.create(project=p2, number=1, track=track, title="Beta Project", summary="Second", created_by=pete)
    p2.submitted_revision = r2
    p2.save()

    # Assign and score
    a1 = assign_judge_to_project(org, event, m_j, p1, rubric)
    a2 = assign_judge_to_project(org, event, m_j, p2, rubric)

    # Direct review creation since event judging is now in past
    rev1 = Review.objects.create(assignment=a1, status=Review.Status.SUBMITTED, submitted_at=now - timedelta(hours=2))
    ReviewScore.objects.create(review=rev1, criterion=c1, value=5)
    ReviewScore.objects.create(review=rev1, criterion=c2, value=5)

    rev2 = Review.objects.create(assignment=a2, status=Review.Status.SUBMITTED, submitted_at=now - timedelta(hours=2))
    ReviewScore.objects.create(review=rev2, criterion=c1, value=3)
    ReviewScore.objects.create(review=rev2, criterion=c2, value=4)


@pytest.mark.django_db
def test_calculate_result_run_and_preview_api():
    client = Client()
    event = Event.objects.get(slug="results-test-hack")

    # Participant cannot preview -> 403
    client.login(username="results_pete@example.org", password="pass1234")
    resp_part = client.post(f"/api/v1/events/{event.slug}/results/preview/")
    assert resp_part.status_code == 403

    # Organiser can preview -> 200
    client.login(username="results_org@example.org", password="pass1234")
    resp_org = client.post(
        f"/api/v1/events/{event.slug}/results/preview/",
        data={"algorithm": "RAW_WEIGHTED_V1"},
        content_type="application/json",
    )
    assert resp_org.status_code == 200
    data = resp_org.json()

    assert data["algorithm"] == "RAW_WEIGHTED_V1"
    assert len(data["rows"]) == 2
    assert data["rows"][0]["rank"] == 1
    assert data["rows"][0]["title"] == "Alpha Project"
    assert data["rows"][1]["rank"] == 2
    assert data["rows"][1]["title"] == "Beta Project"


@pytest.mark.django_db
def test_no_leak_before_publication():
    client = Client()
    event = Event.objects.get(slug="results-test-hack")

    # Public endpoint returns 404 before publication
    resp = client.get(f"/api/v1/events/{event.slug}/results/")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_publish_results_and_public_leaderboard():
    client = Client()
    event = Event.objects.get(slug="results-test-hack")
    org = User.objects.get(email="results_org@example.org")

    run = calculate_result_run(org, event, algorithm="RIDGE_JUDGE_OFFSET_V1")
    assert run is not None
    assert run.input_sha256 != ""
    assert run.output_sha256 != ""

    # Publish results
    pub = publish_results(
        actor_user=org,
        event=event,
        result_run=run,
        public_note_md="Official winners announcement!",
    )
    assert pub.number == 1
    event.refresh_from_db()
    assert event.active_publication_id == pub.id
    assert event.judging_frozen_at is not None

    # Now public endpoint returns 200 with ranked results
    resp_pub = client.get(f"/api/v1/events/{event.slug}/results/")
    assert resp_pub.status_code == 200
    data = resp_pub.json()
    assert data["publication_number"] == 1
    assert len(data["results"]) == 2
    assert data["results"][0]["rank"] == 1
    assert data["results"][0]["project_title"] == "Alpha Project"

    # Public web HTML page returns 200 with winner
    resp_html = client.get(f"/events/{event.slug}/results/")
    assert resp_html.status_code == 200
    content = resp_html.content.decode("utf-8")
    assert "Alpha Project" in content
    assert "Official winners announcement!" in content


@pytest.mark.django_db
def test_csv_export_permissions_and_formula_sanitization():
    client = Client()
    event = Event.objects.get(slug="results-test-hack")
    org = User.objects.get(email="results_org@example.org")

    # Calculate run so CSV has data
    calculate_result_run(org, event)

    # 1. Anonymous or Participant cannot export CSV -> 401/403
    resp_anon = client.get(f"/api/v1/events/{event.slug}/exports/results.csv")
    assert resp_anon.status_code in (401, 403)

    client.login(username="results_pete@example.org", password="pass1234")
    resp_part = client.get(f"/api/v1/events/{event.slug}/exports/results.csv")
    assert resp_part.status_code == 403

    # 2. Organiser can export CSV
    client.login(username="results_org@example.org", password="pass1234")
    resp_org = client.get(f"/api/v1/events/{event.slug}/exports/results.csv")
    assert resp_org.status_code == 200
    assert resp_org["Content-Type"].startswith("text/csv")
    csv_text = resp_org.content.decode("utf-8")
    assert "Alpha Project" in csv_text
    assert "Beta Project" in csv_text

    # 3. Unit check on CSV formula injection sanitization
    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("+cmd|' /C calc'!A0") == "'+cmd|' /C calc'!A0"
    assert sanitize_csv_cell("-123") == "'-123"
    assert sanitize_csv_cell("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert sanitize_csv_cell("Safe Normal Title") == "Safe Normal Title"
