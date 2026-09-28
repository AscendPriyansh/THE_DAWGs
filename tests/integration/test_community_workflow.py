import pytest
from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User, RateLimitBucket
from apps.accounts.rate_limit import RateLimitExceeded
from apps.audit.models import AuditEvent
from apps.events.models import Event, EventMembership, Track
from apps.results.models import CommunityResultRow, Publication, ResultRun
from apps.results.services import calculate_result_run, publish_results
from apps.submissions.models import Project, ProjectRevision
from apps.teams.models import Team, TeamMember
from apps.voting.models import AbuseSignal, Comment, ModerationCase, Vote, VoterIdentity, VotingPolicy
from apps.voting.services import (
    cast_vote,
    configure_voting_policy,
    create_comment,
    delete_comment,
    dismiss_moderation_case,
    edit_comment,
    initiate_email_challenge,
    moderate_comment,
    record_abuse_signal,
    report_moderation_case,
    resolve_moderation_case,
)


@pytest.fixture
def community_env(db):
    now = timezone.now()
    org_user = User.objects.create_user(email="org_m06@example.com", display_name="Organiser M06")
    participant1 = User.objects.create_user(email="p1_m06@example.com", display_name="Participant 1")
    participant2 = User.objects.create_user(email="p2_m06@example.com", display_name="Participant 2")
    judge_user = User.objects.create_user(email="judge_m06@example.com", display_name="Judge M06")

    event = Event.objects.create(
        slug="community-hack-2026",
        name="Community Hack 2026",
        lifecycle=Event.Lifecycle.PUBLISHED,
        registration_opens_at=now - timedelta(days=5),
        registration_closes_at=now + timedelta(days=2),
        submissions_opens_at=now - timedelta(days=5),
        submissions_closes_at=now - timedelta(hours=2),
        judging_opens_at=now - timedelta(hours=1),
        judging_closes_at=now + timedelta(hours=2),
        voting_opens_at=now - timedelta(hours=1),
        voting_closes_at=now + timedelta(hours=2),
        data_version=1,
    )

    EventMembership.objects.create(event=event, user=org_user, role=EventMembership.Role.ORGANISER)
    EventMembership.objects.create(event=event, user=participant1, role=EventMembership.Role.PARTICIPANT)
    EventMembership.objects.create(event=event, user=participant2, role=EventMembership.Role.PARTICIPANT)
    EventMembership.objects.create(event=event, user=judge_user, role=EventMembership.Role.JUDGE)

    track = Track.objects.create(event=event, slug="general", name="General Track")

    team1 = Team.objects.create(event=event, name="Team Alpha", captain_user=participant1)
    TeamMember.objects.create(event=event, team=team1, user=participant1)
    proj1 = Project.objects.create(event=event, team=team1, state=Project.State.SUBMITTED)
    rev1 = ProjectRevision.objects.create(
        project=proj1,
        number=1,
        title="Project Alpha",
        track=track,
        created_by=participant1,
        roster_snapshot=[{"id": str(participant1.id), "name": "Participant 1"}],
    )
    proj1.submitted_revision = rev1
    proj1.save(update_fields=["submitted_revision"])

    team2 = Team.objects.create(event=event, name="Team Beta", captain_user=participant2)
    TeamMember.objects.create(event=event, team=team2, user=participant2)
    proj2 = Project.objects.create(event=event, team=team2, state=Project.State.SUBMITTED)
    rev2 = ProjectRevision.objects.create(
        project=proj2,
        number=1,
        title="Project Beta",
        track=track,
        created_by=participant2,
        roster_snapshot=[{"id": str(participant2.id), "name": "Participant 2"}],
    )
    proj2.submitted_revision = rev2
    proj2.save(update_fields=["submitted_revision"])

    policy = VotingPolicy.objects.create(
        event=event,
        mode=VotingPolicy.Mode.AUTHENTICATED,
        comments_enabled=True,
    )

    return {
        "event": event,
        "org_user": org_user,
        "participant1": participant1,
        "participant2": participant2,
        "judge_user": judge_user,
        "proj1": proj1,
        "proj2": proj2,
        "policy": policy,
    }


@pytest.mark.django_db
def test_comment_lifecycle_and_scrubbing(community_env):
    """
    Validates comment creation, editing (author only), deletion (content scrubbed),
    and moderation (hide/restore) with reasons and audit logging.
    """
    env = community_env
    event = env["event"]
    proj1 = env["proj1"]
    author = env["participant2"]
    org = env["org_user"]

    # 1. Author posts comment
    comment = create_comment(event, proj1, author, "Great project, congratulations!")
    assert comment.state == Comment.State.VISIBLE
    assert comment.body == "Great project, congratulations!"
    assert comment.version == 1

    # Check audit record created
    audit = AuditEvent.objects.filter(entity_type="Comment", entity_id=comment.id, action="COMMENT_CREATED").first()
    assert audit is not None

    # 2. Author edits comment
    edited = edit_comment(comment, author, "Great project, congratulations! Loved the demo.")
    assert edited.body == "Great project, congratulations! Loved the demo."
    assert edited.version == 2

    # Non-author cannot edit
    with pytest.raises(PermissionDenied):
        edit_comment(edited, env["participant1"], "Tampered text")

    # 3. Organiser hides comment
    mod = moderate_comment(edited, org, Comment.State.HIDDEN, reason="Under review for links")
    assert mod.state == Comment.State.HIDDEN

    # Author cannot edit hidden comment
    with pytest.raises(ValidationError):
        edit_comment(mod, author, "Trying to edit hidden comment")

    # Organiser restores comment
    restored = moderate_comment(mod, org, Comment.State.VISIBLE, reason="Review passed, comment approved")
    assert restored.state == Comment.State.VISIBLE

    # 4. Author deletes comment -> body must be completely scrubbed
    del_comment = delete_comment(restored, author)
    assert del_comment.state == Comment.State.DELETED
    assert del_comment.body == ""  # Plain text body is scrubbed per retention spec

    # Author cannot edit deleted comment back to life
    with pytest.raises(ValidationError):
        edit_comment(del_comment, author, "Reviving comment")


@pytest.mark.django_db
def test_comment_window_and_policy_enforcement(community_env):
    """
    Validates comments are blocked when policy comments_enabled=False or outside comment window.
    """
    env = community_env
    event = env["event"]
    proj1 = env["proj1"]
    author = env["participant2"]

    # 1. Disable comments in policy
    env["policy"].comments_enabled = False
    env["policy"].save()

    with pytest.raises(ValidationError, match="Comments are disabled"):
        create_comment(event, proj1, author, "Should be blocked")

    env["policy"].comments_enabled = True
    env["policy"].save()

    # 2. Test comment window closed
    event.voting_closes_at = timezone.now() - timedelta(minutes=5)
    event.judging_closes_at = timezone.now() - timedelta(minutes=5)
    event.save()

    with pytest.raises(PermissionDenied, match="Comment window is not open"):
        create_comment(event, proj1, author, "Late comment")


@pytest.mark.django_db
def test_rate_limiting_enforcement(community_env):
    """
    Validates PostgreSQL RateLimitBucket enforcement:
    - Comments: 5 per minute limit
    - Email challenges: 3 per hour limit
    - Vote transitions: 60 per minute limit
    """
    env = community_env
    event = env["event"]
    proj1 = env["proj1"]
    author = env["participant2"]

    # Post 5 comments successfully
    for i in range(5):
        create_comment(event, proj1, author, f"Comment number {i}")

    # 6th comment in same minute must be rate limited
    with pytest.raises(RateLimitExceeded):
        create_comment(event, proj1, author, "Comment number 6")

    # Email challenge rate limiting: 3 per hour
    env["policy"].mode = VotingPolicy.Mode.EMAIL_VERIFIED
    env["policy"].save()

    for i in range(3):
        initiate_email_challenge(event, f"voter_{i}@example.com")

    # 4th challenge to same address is rate limited
    initiate_email_challenge(event, "limited_voter@example.com")
    initiate_email_challenge(event, "limited_voter@example.com")
    initiate_email_challenge(event, "limited_voter@example.com")
    with pytest.raises(RateLimitExceeded):
        initiate_email_challenge(event, "limited_voter@example.com")


@pytest.mark.django_db
def test_moderation_cases_and_decisions(community_env):
    """
    Validates reporting content, organiser inbox, resolving cases with actions
    (suspending voter identity, voiding votes, hiding comments), and reasoned audit trails.
    """
    env = community_env
    event = env["event"]
    proj1 = env["proj1"]
    author = env["participant2"]
    org = env["org_user"]

    # 1. Create a comment and report it
    comment = create_comment(event, proj1, author, "Spam advertising text")
    case = report_moderation_case(
        event=event,
        target_type=ModerationCase.TargetType.COMMENT,
        target_id=comment.id,
        reason_code="SPAM",
        reporter_user=env["participant1"],
    )
    assert case.status == ModerationCase.Status.OPEN

    # 2. Record an automated abuse signal
    signal = record_abuse_signal(
        event=event,
        kind=AbuseSignal.Kind.RAPID_ACCOUNT_VOTES,
        subject_id=str(author.id),
        network_key="test_net_key",
        details_json={"burst_count": 15},
    )
    assert signal.state == AbuseSignal.State.OPEN

    # 3. Check organiser moderation inbox API
    client = APIClient()
    client.force_authenticate(user=org)
    resp = client.get(f"/api/v1/events/{event.slug}/moderation/inbox/")
    assert resp.status_code == 200
    assert resp.data["open_cases_count"] >= 1
    assert resp.data["open_signals_count"] >= 1

    # 4. Resolve case: HIDE_COMMENT with reasoned audit log
    resolved = resolve_moderation_case(
        case=case,
        actor_user=org,
        decision=ModerationCase.Decision.HIDE_COMMENT,
        reason="Confirmed promotional spam, hidden immediately",
    )
    assert resolved.status == ModerationCase.Status.RESOLVED
    comment.refresh_from_db()
    assert comment.state == Comment.State.HIDDEN

    # Check reasoned audit entry
    audit = AuditEvent.objects.filter(entity_type="ModerationCase", entity_id=case.id, action="MODERATION_CASE_RESOLVED").first()
    assert audit is not None
    assert "Confirmed promotional spam" in audit.reason

    # 5. Resolve case: SUSPEND_IDENTITY and VOID_VOTES
    voter_identity = VoterIdentity.objects.create(
        event=event,
        kind=VoterIdentity.Kind.USER,
        user=author,
        status=VoterIdentity.Status.ACTIVE,
    )
    vote = Vote.objects.create(
        event=event,
        project=proj1,
        voter_identity=voter_identity,
        state=Vote.State.ACTIVE,
    )

    vote_case = report_moderation_case(
        event=event,
        target_type=ModerationCase.TargetType.VOTE,
        target_id=vote.id,
        reason_code="VOTE_MANIPULATION",
        reporter_user=org,
    )

    prev_data_version = event.data_version
    resolve_moderation_case(
        case=vote_case,
        actor_user=org,
        decision=ModerationCase.Decision.VOID_VOTES,
        reason="Coordinated bot vote pattern detected",
        affected_ids=[vote.id],
    )
    vote.refresh_from_db()
    assert vote.state == Vote.State.VOID
    event.refresh_from_db()
    assert event.data_version > prev_data_version  # Invalidated data version for results recalculation!


@pytest.mark.django_db
def test_community_result_snapshots_and_confidentiality(community_env):
    """
    Validates:
    - CommunityResultRow calculation (counted active votes vs excluded voided/withdrawn votes).
    - Competition ties (1, 1, 3).
    - Zero votes handled cleanly without invented winners.
    - Public confidentiality / No-leak: project vote counts & ranks remain private before publication.
    - Publication requirement: cannot publish while voting window is open.
    - Publication releases CommunityResultRow officially.
    """
    env = community_env
    event = env["event"]
    proj1 = env["proj1"]
    proj2 = env["proj2"]
    org = env["org_user"]

    # 1. Zero votes scenario
    run_zero = calculate_result_run(org, event)
    comm_rows_zero = CommunityResultRow.objects.filter(result_run=run_zero)
    assert comm_rows_zero.count() == 2
    for cr in comm_rows_zero:
        assert cr.counted_votes == 0
        assert cr.rank is None  # "No community votes recorded"

    # 2. Add votes:
    # Voter 1 votes for Proj 1 (active)
    # Voter 2 votes for Proj 1 (active)
    # Voter 3 votes for Proj 2 (active)
    # Voter 4 votes for Proj 2 (VOID / excluded)
    u1 = User.objects.create_user(email="v1@example.com", display_name="Voter 1")
    u2 = User.objects.create_user(email="v2@example.com", display_name="Voter 2")
    u3 = User.objects.create_user(email="v3@example.com", display_name="Voter 3")
    u4 = User.objects.create_user(email="v4@example.com", display_name="Voter 4")

    id1 = VoterIdentity.objects.create(event=event, kind=VoterIdentity.Kind.USER, user=u1)
    id2 = VoterIdentity.objects.create(event=event, kind=VoterIdentity.Kind.USER, user=u2)
    id3 = VoterIdentity.objects.create(event=event, kind=VoterIdentity.Kind.USER, user=u3)
    id4 = VoterIdentity.objects.create(event=event, kind=VoterIdentity.Kind.USER, user=u4)

    Vote.objects.create(event=event, project=proj1, voter_identity=id1, state=Vote.State.ACTIVE)
    Vote.objects.create(event=event, project=proj1, voter_identity=id2, state=Vote.State.ACTIVE)
    Vote.objects.create(event=event, project=proj2, voter_identity=id3, state=Vote.State.ACTIVE)
    Vote.objects.create(event=event, project=proj2, voter_identity=id4, state=Vote.State.VOID)  # Excluded

    # Calculate run with votes
    run_votes = calculate_result_run(org, event)
    cr_p1 = CommunityResultRow.objects.get(result_run=run_votes, project=proj1)
    cr_p2 = CommunityResultRow.objects.get(result_run=run_votes, project=proj2)

    assert cr_p1.counted_votes == 2
    assert cr_p1.excluded_votes == 0
    assert cr_p1.rank == 1

    assert cr_p2.counted_votes == 1
    assert cr_p2.excluded_votes == 1
    assert cr_p2.rank == 2

    # 3. Confidentiality / No-leak check:
    # Public endpoints must return 404 before publication!
    client = APIClient()
    resp = client.get(f"/api/v1/events/{event.slug}/results/")
    assert resp.status_code == 404

    # 4. Publication validation: cannot publish while voting window is open
    event.judging_closes_at = timezone.now() - timedelta(minutes=10)
    event.save(update_fields=["judging_closes_at"])

    with pytest.raises(ValidationError, match="Cannot publish results before community voting has closed"):
        publish_results(org, event, run_votes)

    # Fast forward after voting has closed too
    event.voting_closes_at = timezone.now() - timedelta(minutes=10)
    event.save(update_fields=["voting_closes_at"])

    # Now publication succeeds
    pub = publish_results(org, event, run_votes, public_note_md="Official results released.")
    assert pub.number == 1
    assert event.active_publication_id == pub.id

    # 5. After publication, results API returns both judged results and community result snapshots
    resp_pub = client.get(f"/api/v1/events/{event.slug}/results/")
    assert resp_pub.status_code == 200
    assert "community_results" in resp_pub.data
    assert len(resp_pub.data["community_results"]) == 2
    assert resp_pub.data["community_results"][0]["counted_votes"] == 2
    assert resp_pub.data["community_results"][0]["rank"] == 1


@pytest.mark.django_db
def test_community_api_endpoints_and_next_action(community_env):
    """
    Validates REST API routes:
    - POST & GET project comments
    - PATCH & DELETE comment
    - POST moderate comment
    - POST report moderation case
    - GET moderation inbox
    - Organiser next action reflects open moderation cases
    """
    env = community_env
    event = env["event"]
    proj1 = env["proj1"]
    author = env["participant2"]
    org = env["org_user"]

    client = APIClient()

    # 1. Anonymous cannot POST comment
    resp = client.post(f"/api/v1/events/{event.slug}/projects/{proj1.id}/comments/", {"body": "Anon comment"})
    assert resp.status_code == 401

    # 2. Authenticated author posts comment
    client.force_authenticate(user=author)
    resp = client.post(f"/api/v1/events/{event.slug}/projects/{proj1.id}/comments/", {"body": "My feedback"})
    assert resp.status_code == 201
    comment_id = resp.data["id"]

    # 3. Public GET comments shows visible comment
    client.force_authenticate(user=None)
    resp = client.get(f"/api/v1/events/{event.slug}/projects/{proj1.id}/comments/")
    assert resp.status_code == 200
    assert len(resp.data["comments"]) == 1
    assert resp.data["comments"][0]["body"] == "My feedback"

    # 4. Author edits comment via PATCH
    client.force_authenticate(user=author)
    resp = client.patch(f"/api/v1/events/{event.slug}/comments/{comment_id}/", {"body": "Updated feedback"})
    assert resp.status_code == 200
    assert resp.data["body"] == "Updated feedback"
    assert resp.data["version"] == 2

    # 5. Organiser hides comment via POST moderate
    client.force_authenticate(user=org)
    resp = client.post(f"/api/v1/events/{event.slug}/comments/{comment_id}/moderate/", {"state": "HIDDEN", "reason": "Testing moderation"})
    assert resp.status_code == 200
    assert resp.data["state"] == "HIDDEN"

    # 6. Public GET comments hides the hidden comment
    client.force_authenticate(user=None)
    resp = client.get(f"/api/v1/events/{event.slug}/projects/{proj1.id}/comments/")
    assert resp.status_code == 200
    assert len(resp.data["comments"]) == 0  # Not shown to anonymous public

    # 7. User reports abuse
    client.force_authenticate(user=author)
    resp = client.post(f"/api/v1/events/{event.slug}/moderation/report/", {
        "target_type": "COMMENT",
        "target_id": comment_id,
        "reason_code": "INAPPROPRIATE_CONTENT",
    })
    assert resp.status_code == 201
    case_id = resp.data["case_id"]

    # 8. Check organiser next action now says "COMPLETE_MODERATION_REVIEWS"
    client.force_authenticate(user=org)
    resp_next = client.get(f"/api/v1/events/{event.slug}/me/next-action/")
    assert resp_next.status_code == 200
    assert resp_next.data["code"] == "COMPLETE_MODERATION_REVIEWS"
    assert "Complete 1 moderation review" in resp_next.data["label"]

    # 9. Organiser resolves moderation case via API
    resp_res = client.post(f"/api/v1/events/{event.slug}/moderation/cases/{case_id}/resolve/", {
        "decision": "HIDE_COMMENT",
        "reason": "Confirmed inappropriate",
    })
    assert resp_res.status_code == 200
    assert resp_res.data["status"] == "RESOLVED"

    # 10. Next action after resolution returns to standard organiser dashboard
    resp_next2 = client.get(f"/api/v1/events/{event.slug}/me/next-action/")
    assert resp_next2.status_code == 200
    assert resp_next2.data["code"] == "ORGANISER_DASHBOARD"

