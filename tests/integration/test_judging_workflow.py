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
    get_event_judging_progress,
    grant_judge_track_permission,
    save_draft_review,
    submit_review,
)
from apps.submissions.models import Project, ProjectRevision
from apps.teams.models import Team, TeamMember


@pytest.fixture(autouse=True)
def setup_judging_test_env(db):
    fixture_path = os.path.join(settings.BASE_DIR.parent.parent, "fixtures", "fixtures.json")
    importer = FixtureImporter(fixture_path)
    importer.import_fixture(force=False)

    now = timezone.now()
    # Active open judging event
    event, _ = Event.objects.get_or_create(
        slug="judging-active-hack",
        defaults={
            "name": "Judging Active Hackathon",
            "lifecycle": Event.Lifecycle.PUBLISHED,
            "registration_opens_at": now - timedelta(days=5),
            "registration_closes_at": now - timedelta(days=2),
            "submissions_opens_at": now - timedelta(days=4),
            "submissions_closes_at": now - timedelta(days=1),
            "judging_opens_at": now - timedelta(hours=12),
            "judging_closes_at": now + timedelta(days=2),
            "min_team_size": 1,
            "max_team_size": 4,
            "required_reviews": 3,
        }
    )

    track, _ = Track.objects.get_or_create(
        event=event,
        name="AI Track",
        defaults={"slug": "ai-track", "description_md": "AI applications", "display_order": 1}
    )

    # Users
    judge_a, _ = User.objects.get_or_create(email="judge_a@example.org", defaults={"display_name": "Judge Alice"})
    judge_a.set_password("pass1234")
    judge_a.save()

    judge_b, _ = User.objects.get_or_create(email="judge_b@example.org", defaults={"display_name": "Judge Bob"})
    judge_b.set_password("pass1234")
    judge_b.save()

    participant, _ = User.objects.get_or_create(email="participant@example.org", defaults={"display_name": "Participant Pete"})
    participant.set_password("pass1234")
    participant.save()

    organiser, _ = User.objects.get_or_create(email="organiser@example.org", defaults={"display_name": "Organiser Olivia", "is_staff": True})
    organiser.set_password("pass1234")
    organiser.save()

    # Memberships
    m_ja, _ = EventMembership.objects.get_or_create(event=event, user=judge_a, defaults={"role": EventMembership.Role.JUDGE})
    m_jb, _ = EventMembership.objects.get_or_create(event=event, user=judge_b, defaults={"role": EventMembership.Role.JUDGE})
    m_p, _ = EventMembership.objects.get_or_create(event=event, user=participant, defaults={"role": EventMembership.Role.PARTICIPANT})
    m_o, _ = EventMembership.objects.get_or_create(event=event, user=organiser, defaults={"role": EventMembership.Role.ORGANISER})

    # Rubric & Criteria
    rubric, _ = Rubric.objects.get_or_create(
        event=event,
        version_number=1,
        defaults={"name": "Standard Innovation Rubric", "created_by": organiser}
    )
    Criterion.objects.get_or_create(
        rubric=rubric,
        key="functionality",
        defaults={"label": "Functionality", "weight": Decimal("1.0000"), "display_order": 1}
    )
    Criterion.objects.get_or_create(
        rubric=rubric,
        key="quality",
        defaults={"label": "Code & Architecture Quality", "weight": Decimal("1.0000"), "display_order": 2}
    )
    Criterion.objects.get_or_create(
        rubric=rubric,
        key="innovation",
        defaults={"label": "Innovation & Impact", "weight": Decimal("1.0000"), "display_order": 3}
    )


@pytest.mark.django_db
def test_rubric_freeze_requirement():
    event = Event.objects.get(slug="judging-active-hack")
    org = User.objects.get(email="organiser@example.org")
    judge_a = User.objects.get(email="judge_a@example.org")
    m_ja = EventMembership.objects.get(event=event, user=judge_a)
    track = Track.objects.get(event=event, slug="ai-track")

    rubric = Rubric.objects.get(event=event, version_number=1)
    assert rubric.state == Rubric.State.DRAFT

    # Create submitted project
    team = Team.objects.create(event=event, name="AI Builders", captain_user=judge_a)
    proj = Project.objects.create(event=event, team=team, state=Project.State.SUBMITTED)
    rev = ProjectRevision.objects.create(
        project=proj, number=1, track=track, title="AI Copilot", summary="A helpful assistant",
        created_by=judge_a, source=ProjectRevision.Source.USER
    )
    proj.submitted_revision = rev
    proj.save()

    grant_judge_track_permission(org, m_ja, track)

    # Attempting to assign before rubric freeze must fail
    with pytest.raises(Exception) as exc:
        assign_judge_to_project(org, event, m_ja, proj, rubric)
    assert "frozen" in str(exc.value).lower()

    # Freeze rubric
    freeze_rubric(org, rubric)
    assert rubric.state == Rubric.State.FROZEN
    assert rubric.frozen_at is not None


@pytest.mark.django_db
def test_conflict_of_interest_check():
    event = Event.objects.get(slug="judging-active-hack")
    org = User.objects.get(email="organiser@example.org")
    judge_a = User.objects.get(email="judge_a@example.org")
    m_ja = EventMembership.objects.get(event=event, user=judge_a)
    track = Track.objects.get(event=event, slug="ai-track")
    rubric = Rubric.objects.get(event=event, version_number=1)
    freeze_rubric(org, rubric)

    # Judge A is also on the project team!
    team = Team.objects.create(event=event, name="Conflicted Team", captain_user=judge_a)
    TeamMember.objects.create(event=event, team=team, user=judge_a)

    proj = Project.objects.create(event=event, team=team, state=Project.State.SUBMITTED)
    rev = ProjectRevision.objects.create(
        project=proj, number=1, track=track, title="My Team Project", summary="Summary",
        created_by=judge_a, source=ProjectRevision.Source.USER
    )
    proj.submitted_revision = rev
    proj.save()

    grant_judge_track_permission(org, m_ja, track)

    # Must raise PermissionDenied due to conflict of interest
    with pytest.raises(Exception) as exc:
        assign_judge_to_project(org, event, m_ja, proj, rubric)
    assert "conflict" in str(exc.value).lower()


@pytest.mark.django_db
def test_track_permission_requirement_and_assignment():
    event = Event.objects.get(slug="judging-active-hack")
    org = User.objects.get(email="organiser@example.org")
    judge_b = User.objects.get(email="judge_b@example.org")
    pete = User.objects.get(email="participant@example.org")
    m_jb = EventMembership.objects.get(event=event, user=judge_b)
    track = Track.objects.get(event=event, slug="ai-track")
    rubric = Rubric.objects.get(event=event, version_number=1)
    freeze_rubric(org, rubric)

    team = Team.objects.create(event=event, name="Pete's Team", captain_user=pete)
    TeamMember.objects.create(event=event, team=team, user=pete)
    proj = Project.objects.create(event=event, team=team, state=Project.State.SUBMITTED)
    rev = ProjectRevision.objects.create(
        project=proj, number=1, track=track, title="Pete Project", summary="Summary",
        created_by=pete, source=ProjectRevision.Source.USER
    )
    proj.submitted_revision = rev
    proj.save()

    # Without track permission -> Fails
    with pytest.raises(Exception) as exc:
        assign_judge_to_project(org, event, m_jb, proj, rubric)
    assert "track permission" in str(exc.value).lower()

    # Grant track permission -> Assignment succeeds
    grant_judge_track_permission(org, m_jb, track)
    assignment = assign_judge_to_project(org, event, m_jb, proj, rubric)
    assert assignment.status == JudgeAssignment.Status.ACTIVE
    assert assignment.project == proj
    assert assignment.judge_membership == m_jb


@pytest.mark.django_db
def test_draft_and_final_review_workflow():
    event = Event.objects.get(slug="judging-active-hack")
    org = User.objects.get(email="organiser@example.org")
    judge_b = User.objects.get(email="judge_b@example.org")
    pete = User.objects.get(email="participant@example.org")
    m_jb = EventMembership.objects.get(event=event, user=judge_b)
    track = Track.objects.get(event=event, slug="ai-track")
    rubric = Rubric.objects.get(event=event, version_number=1)
    freeze_rubric(org, rubric)
    grant_judge_track_permission(org, m_jb, track)

    team = Team.objects.create(event=event, name="Review Test Team", captain_user=pete)
    proj = Project.objects.create(event=event, team=team, state=Project.State.SUBMITTED)
    rev = ProjectRevision.objects.create(
        project=proj, number=1, track=track, title="Target App", summary="Summary",
        created_by=pete, source=ProjectRevision.Source.USER
    )
    proj.submitted_revision = rev
    proj.save()

    assignment = assign_judge_to_project(org, event, m_jb, proj, rubric)

    # 1. Partial draft save
    draft_review = save_draft_review(
        judge_user=judge_b,
        assignment=assignment,
        scores_dict={"functionality": 4},
        comment="Promising project, haven't evaluated quality yet.",
    )
    assert draft_review.status == Review.Status.DRAFT
    assert draft_review.scores.count() == 1
    assert draft_review.scores.first().value == 4
    assert draft_review.revisions.count() == 1

    # 2. Incomplete submission attempt -> Must fail validation
    with pytest.raises(Exception) as exc:
        submit_review(
            judge_user=judge_b,
            assignment=assignment,
            scores_dict={"functionality": 4, "quality": 5},  # Missing "innovation"
            comment="Tried to submit without innovation",
        )
    assert "missing" in str(exc.value).lower()

    # 3. Complete submission
    submitted_review = submit_review(
        judge_user=judge_b,
        assignment=assignment,
        scores_dict={"functionality": 4, "quality": 5, "innovation": 3},
        comment="Fully evaluated and thoroughly tested.",
    )
    assert submitted_review.status == Review.Status.SUBMITTED
    assert submitted_review.submitted_at is not None
    assert submitted_review.scores.count() == 3
    assert submitted_review.revisions.count() == 2


@pytest.mark.django_db
def test_judge_score_confidentiality():
    client = Client()
    event = Event.objects.get(slug="judging-active-hack")
    org = User.objects.get(email="organiser@example.org")
    judge_a = User.objects.get(email="judge_a@example.org")
    judge_b = User.objects.get(email="judge_b@example.org")
    pete = User.objects.get(email="participant@example.org")
    m_ja = EventMembership.objects.get(event=event, user=judge_a)
    track = Track.objects.get(event=event, slug="ai-track")
    rubric = Rubric.objects.get(event=event, version_number=1)
    freeze_rubric(org, rubric)
    grant_judge_track_permission(org, m_ja, track)

    team = Team.objects.create(event=event, name="Confidentiality Project Team", captain_user=pete)
    proj = Project.objects.create(event=event, team=team, state=Project.State.SUBMITTED)
    rev = ProjectRevision.objects.create(
        project=proj, number=1, track=track, title="Confidential App", summary="Summary",
        created_by=pete, source=ProjectRevision.Source.USER
    )
    proj.submitted_revision = rev
    proj.save()

    assignment = assign_judge_to_project(org, event, m_ja, proj, rubric)
    submit_review(
        judge_user=judge_a,
        assignment=assignment,
        scores_dict={"functionality": 5, "quality": 4, "innovation": 5},
        comment="Confidential score notes",
    )

    url = f"/api/v1/events/{event.slug}/judges/{judge_a.id}/scores/"

    # 1. Judge A (owner) can view own scores -> 200 OK
    client.login(username="judge_a@example.org", password="pass1234")
    resp_owner = client.get(url)
    assert resp_owner.status_code == 200
    assert len(resp_owner.json()["reviews"]) == 1

    # 2. Judge B (peer) is strictly blocked -> 403 Forbidden
    client.login(username="judge_b@example.org", password="pass1234")
    resp_peer = client.get(url)
    assert resp_peer.status_code == 403

    # 3. Participant is strictly blocked -> 403 Forbidden
    client.login(username="participant@example.org", password="pass1234")
    resp_part = client.get(url)
    assert resp_part.status_code == 403

    # 4. Organiser can view any judge's scores -> 200 OK
    client.login(username="organiser@example.org", password="pass1234")
    resp_org = client.get(url)
    assert resp_org.status_code == 200


@pytest.mark.django_db
def test_organiser_progress_dashboard():
    client = Client()
    event = Event.objects.get(slug="judging-active-hack")

    # Participant blocked from organiser progress -> 403
    client.login(username="participant@example.org", password="pass1234")
    resp_blocked = client.get(f"/api/v1/events/{event.slug}/organiser/progress/")
    assert resp_blocked.status_code == 403

    # Organiser can access -> 200
    client.login(username="organiser@example.org", password="pass1234")
    resp_ok = client.get(f"/api/v1/events/{event.slug}/organiser/progress/")
    assert resp_ok.status_code == 200
    data = resp_ok.json()
    assert "total_projects" in data
    assert "total_completed_reviews" in data
    assert "required_reviews_per_project" in data
