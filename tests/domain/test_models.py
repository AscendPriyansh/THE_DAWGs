import pytest
from django.utils import timezone
from apps.accounts.models import User
from apps.events.models import Event, EventMembership, Track
from apps.teams.models import Team
from apps.submissions.models import Project, ProjectRevision


@pytest.mark.django_db
def test_user_email_normalization():
    user = User.objects.create_user(email=" TEST.User@Example.COM ", display_name="Test User")
    assert user.email == "test.user@example.com"
    assert str(user) == "Test User <test.user@example.com>"


@pytest.mark.django_db
def test_event_submission_window():
    now = timezone.now()
    event = Event.objects.create(
        slug="test-event",
        name="Test Event",
        registration_opens_at=now - timezone.timedelta(days=10),
        registration_closes_at=now - timezone.timedelta(days=2),
        submissions_opens_at=now - timezone.timedelta(days=10),
        submissions_closes_at=now - timezone.timedelta(days=2),
        judging_opens_at=now - timezone.timedelta(days=2),
        judging_closes_at=now + timezone.timedelta(days=2),
        lifecycle=Event.Lifecycle.PUBLISHED,
    )
    assert not event.is_submission_open(now)
    assert event.current_phase(now) == "JUDGING"


@pytest.mark.django_db
def test_project_unique_active_per_team():
    now = timezone.now()
    event = Event.objects.create(
        slug="test-event-unique",
        name="Test Event Unique",
        registration_opens_at=now - timezone.timedelta(days=10),
        registration_closes_at=now + timezone.timedelta(days=2),
        submissions_opens_at=now - timezone.timedelta(days=10),
        submissions_closes_at=now + timezone.timedelta(days=2),
        judging_opens_at=now + timezone.timedelta(days=2),
        judging_closes_at=now + timezone.timedelta(days=5),
        lifecycle=Event.Lifecycle.PUBLISHED,
    )
    user = User.objects.create_user(email="captain@example.org", display_name="Captain")
    team = Team.objects.create(event=event, name="Team Unique", captain_user=user)

    p1 = Project.objects.create(event=event, team=team, state=Project.State.SUBMITTED)
    assert p1.state == Project.State.SUBMITTED

    # Second SUBMITTED project for same team should raise IntegrityError
    from django.db import transaction
    from django.db.utils import IntegrityError
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Project.objects.create(event=event, team=team, state=Project.State.SUBMITTED)

    # But a DUPLICATE project is permitted
    p2 = Project.objects.create(event=event, team=team, state=Project.State.DUPLICATE, duplicate_of=p1)
    assert p2.state == Project.State.DUPLICATE
    assert p2.duplicate_of == p1
