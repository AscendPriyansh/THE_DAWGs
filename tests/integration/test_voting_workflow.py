import pytest
from django.utils import timezone
from django.urls import reverse
from django.core.exceptions import ValidationError, PermissionDenied
from apps.events.models import Event, EventMembership
from apps.teams.models import Team, TeamMember
from apps.submissions.models import Project, ProjectRevision
from apps.voting.models import VotingPolicy, VoterIdentity, Vote, VotingLinkGrant
from apps.voting.services import (
    configure_voting_policy,
    create_voting_link_grant,
    redeem_voting_link_grant,
    initiate_email_challenge,
    verify_email_challenge,
    get_or_create_authenticated_voter,
    cast_vote,
    get_or_create_ballot_session,
)


@pytest.fixture
def organiser_user(db):
    from apps.accounts.models import User
    user, _ = User.objects.get_or_create(email="org_v@example.com", defaults={"display_name": "Org"})
    return user

@pytest.fixture
def participant_user(db):
    from apps.accounts.models import User
    user, _ = User.objects.get_or_create(email="part_v@example.com", defaults={"display_name": "Part"})
    return user

@pytest.fixture
def another_user(db):
    from apps.accounts.models import User
    user, _ = User.objects.get_or_create(email="other_v@example.com", defaults={"display_name": "Other"})
    return user

@pytest.fixture
def voting_event(db, organiser_user, participant_user, another_user):
    now = timezone.now()
    event = Event.objects.create(
        slug="vote-fest",
        name="Vote Fest 2026",
        lifecycle=Event.Lifecycle.PUBLISHED,
        registration_opens_at=now - timezone.timedelta(days=10),
        registration_closes_at=now - timezone.timedelta(days=5),
        submissions_opens_at=now - timezone.timedelta(days=10),
        submissions_closes_at=now - timezone.timedelta(days=5),
        judging_opens_at=now - timezone.timedelta(days=4),
        judging_closes_at=now + timezone.timedelta(days=4),
        voting_opens_at=now - timezone.timedelta(days=1),
        voting_closes_at=now + timezone.timedelta(days=1),
    )
    
    from apps.events.models import Track
    
    track = Track.objects.create(
        event=event,
        slug="general",
        name="General Track"
    )
    
    EventMembership.objects.create(
        event=event, user=organiser_user, role=EventMembership.Role.ORGANISER
    )
    EventMembership.objects.create(
        event=event, user=participant_user, role=EventMembership.Role.PARTICIPANT
    )
    
    team = Team.objects.create(
        event=event, name="Alpha Team", captain_user=participant_user
    )
    TeamMember.objects.create(event=event, team=team, user=participant_user)
    
    project = Project.objects.create(
        event=event, team=team, state=Project.State.SUBMITTED
    )
    rev = ProjectRevision.objects.create(
        project=project,
        track=track,
        number=1,
        title="Alpha Project",
        summary="A cool project",
        roster_snapshot=[str(participant_user.id)]
    )
    project.submitted_revision_id = rev.id
    project.save()
    
    # Team 2
    team2 = Team.objects.create(
        event=event, name="Beta Team", captain_user=another_user
    )
    TeamMember.objects.create(event=event, team=team2, user=another_user)
    
    project2 = Project.objects.create(
        event=event, team=team2, state=Project.State.SUBMITTED
    )
    rev2 = ProjectRevision.objects.create(
        project=project2,
        track=track,
        number=1,
        title="Beta Project",
        summary="Another cool project",
        roster_snapshot=[str(another_user.id)]
    )
    project2.submitted_revision_id = rev2.id
    project2.save()

    return event, project, project2


@pytest.mark.django_db
def test_voting_policy_configuration(voting_event, organiser_user, participant_user):
    event, p1, p2 = voting_event
    
    # Participant cannot configure
    with pytest.raises(PermissionDenied):
        configure_voting_policy(event, participant_user, VotingPolicy.Mode.AUTHENTICATED)
        
    # Before voting opens it's fine, but wait, voting is currently OPEN in the fixture.
    # So configuring should fail!
    with pytest.raises(ValidationError, match="Cannot change voting policy after voting has opened"):
        configure_voting_policy(event, organiser_user, VotingPolicy.Mode.AUTHENTICATED)
        
    # Let's shift dates so it's not open yet
    event.voting_opens_at = timezone.now() + timezone.timedelta(days=1)
    event.save()
    
    policy = configure_voting_policy(event, organiser_user, VotingPolicy.Mode.AUTHENTICATED)
    assert policy.mode == VotingPolicy.Mode.AUTHENTICATED


@pytest.mark.django_db
def test_authenticated_voting_flow(voting_event, participant_user, another_user):
    event, p1, p2 = voting_event
    VotingPolicy.objects.create(event=event, mode=VotingPolicy.Mode.AUTHENTICATED)
    
    # Participant casts vote for P2
    identity = get_or_create_authenticated_voter(event, participant_user)
    vote = cast_vote(event, p2, identity)
    
    assert vote.state == Vote.State.ACTIVE
    
    # Cannot vote for own project
    with pytest.raises(ValidationError, match="cannot vote for your own team"):
        cast_vote(event, p1, identity)
        
    # Withdraw vote
    vote = cast_vote(event, p2, identity, state=Vote.State.WITHDRAWN)
    assert vote.state == Vote.State.WITHDRAWN
    
    # Cast again
    vote = cast_vote(event, p2, identity, state=Vote.State.ACTIVE)
    assert vote.state == Vote.State.ACTIVE


@pytest.mark.django_db
def test_link_grant_voting_flow(voting_event, organiser_user, another_user):
    event, p1, p2 = voting_event
    VotingPolicy.objects.create(event=event, mode=VotingPolicy.Mode.INVITE_LINK)
    
    token, grant = create_voting_link_grant(event, organiser_user)
    
    identity = redeem_voting_link_grant(token, another_user)
    assert identity.kind == VoterIdentity.Kind.LINK
    
    vote = cast_vote(event, p1, identity)
    assert vote.state == Vote.State.ACTIVE


@pytest.mark.django_db
def test_email_challenge_flow(voting_event, participant_user):
    event, p1, p2 = voting_event
    VotingPolicy.objects.create(event=event, mode=VotingPolicy.Mode.EMAIL_VERIFIED)
    
    token = initiate_email_challenge(event, "voter@example.com")
    identity = verify_email_challenge(event, "VOTER@example.com", token)
    
    assert identity.kind == VoterIdentity.Kind.EMAIL
    
    # Can vote
    vote = cast_vote(event, p1, identity)
    assert vote.state == Vote.State.ACTIVE


@pytest.mark.django_db
def test_ballot_session_pagination(voting_event, participant_user):
    event, p1, p2 = voting_event
    VotingPolicy.objects.create(event=event, mode=VotingPolicy.Mode.AUTHENTICATED)
    identity = get_or_create_authenticated_voter(event, participant_user)
    
    session1 = get_or_create_ballot_session(event, identity)
    assert len(session1.eligible_project_ids) == 2
    assert str(p1.id) in session1.eligible_project_ids
    assert str(p2.id) in session1.eligible_project_ids
    
    # If we get it again, seed and order is stable
    session2 = get_or_create_ballot_session(event, identity)
    assert session1.ordering_seed == session2.ordering_seed
    assert session1.eligible_project_ids == session2.eligible_project_ids


@pytest.mark.django_db
def test_voting_deadline_enforcement(voting_event, participant_user):
    event, p1, p2 = voting_event
    VotingPolicy.objects.create(event=event, mode=VotingPolicy.Mode.AUTHENTICATED)
    identity = get_or_create_authenticated_voter(event, participant_user)
    
    # Fast forward past deadline
    event.voting_closes_at = timezone.now() - timezone.timedelta(hours=1)
    event.save()
    
    with pytest.raises(PermissionDenied, match="Voting is not open"):
        cast_vote(event, p2, identity)
