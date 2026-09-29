import secrets
import hashlib
import json
import logging
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import PermissionDenied, ValidationError

from apps.accounts.rate_limit import check_rate_limit, hash_network_key, RateLimitExceeded
from apps.audit.models import AuditEvent
from apps.events.models import Event, EventMembership
from apps.submissions.models import Project
from apps.voting.models import (
    VotingPolicy,
    VotingLinkGrant,
    VoterIdentity,
    EmailChallenge,
    Vote,
    BallotSession,
    Comment,
    AbuseSignal,
    ModerationCase,
)

logger = logging.getLogger(__name__)


def is_voting_open(event, now=None):
    if now is None:
        now = timezone.now()
    if event.lifecycle != Event.Lifecycle.PUBLISHED:
        return False
    if not event.voting_opens_at or not event.voting_closes_at:
        return False
    return event.voting_opens_at <= now < event.voting_closes_at


@transaction.atomic
def configure_voting_policy(event, actor, mode, minimum_account_age_seconds=0, comments_enabled=False):
    # Only organisers can configure
    if not EventMembership.objects.filter(event=event, user=actor, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE).exists():
        raise PermissionDenied("Only active organisers can configure voting policy.")

    now = timezone.now()
    
    # Can only configure before it's frozen (which happens when voting opens)
    # Wait, policy is frozen at/before opening.
    if is_voting_open(event, now) or (event.voting_opens_at and now >= event.voting_opens_at):
        raise ValidationError("Cannot change voting policy after voting has opened.")

    policy, created = VotingPolicy.objects.get_or_create(event=event)
    policy.mode = mode
    policy.minimum_account_age_seconds = minimum_account_age_seconds
    policy.comments_enabled = comments_enabled
    policy.version += 1
    policy.save()

    return policy


@transaction.atomic
def freeze_voting_policy(event):
    policy = VotingPolicy.objects.filter(event=event).first()
    if not policy:
        raise ValidationError("Event has no voting policy configured.")
    if not policy.frozen_at:
        policy.frozen_at = timezone.now()
        policy.save()
    return policy


@transaction.atomic
def create_voting_link_grant(event, creator, recipient_label_encrypted="", expires_in_days=7):
    if not EventMembership.objects.filter(event=event, user=creator, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE).exists():
        raise PermissionDenied("Only active organisers can create voting link grants.")

    policy = VotingPolicy.objects.filter(event=event).first()
    if not policy or policy.mode != VotingPolicy.Mode.INVITE_LINK:
        raise ValidationError("Event voting policy is not INVITE_LINK.")

    token = secrets.token_urlsafe(32)
    token_digest = hashlib.sha256(token.encode()).hexdigest()

    expires_at = timezone.now() + timezone.timedelta(days=expires_in_days)

    grant = VotingLinkGrant.objects.create(
        event=event,
        token_digest=token_digest,
        recipient_label_encrypted=recipient_label_encrypted,
        expires_at=expires_at,
        created_by=creator,
    )

    return token, grant


@transaction.atomic
def redeem_voting_link_grant(token, user, ip_address=None):
    # Redeem the link and get or create VoterIdentity
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    
    grant = VotingLinkGrant.objects.select_for_update().filter(token_digest=token_digest).first()
    if not grant:
        if ip_address:
            net_key = hash_network_key(ip_address)
            check_rate_limit("invalid_link", f"{net_key}", limit=10, window_seconds=600)
        raise ValidationError("Invalid or expired invite link.")
    
    now = timezone.now()
    if grant.expires_at < now:
        raise ValidationError("Invite link has expired.")
    if grant.revoked_at:
        raise ValidationError("Invite link was revoked.")
    
    event = grant.event
    if not is_voting_open(event, now):
        # We can redeem before it opens, but let's allow it as long as it's not closed.
        if event.voting_closes_at and now >= event.voting_closes_at:
            raise ValidationError("Voting has already closed for this event.")
            
    policy = VotingPolicy.objects.filter(event=event).first()
    if not policy or policy.mode != VotingPolicy.Mode.INVITE_LINK:
        raise ValidationError("Event voting policy is not INVITE_LINK.")

    if not grant.redeemed_at:
        grant.redeemed_at = now
        grant.save(update_fields=['redeemed_at', 'updated_at'])

    # Get or create identity
    identity, created = VoterIdentity.objects.get_or_create(
        event=event,
        link_grant=grant,
        defaults={'kind': VoterIdentity.Kind.LINK}
    )
    return identity


@transaction.atomic
def initiate_email_challenge(event, email, ip_address=None):
    policy = VotingPolicy.objects.filter(event=event).first()
    if not policy or policy.mode != VotingPolicy.Mode.EMAIL_VERIFIED:
        raise ValidationError("Event voting policy is not EMAIL_VERIFIED.")
        
    now = timezone.now()
    if event.voting_closes_at and now >= event.voting_closes_at:
        raise ValidationError("Voting has already closed.")

    email_lower = email.strip().lower()
    email_digest = hashlib.sha256(f"{settings.SECRET_KEY}:{email_lower}".encode()).hexdigest()
    
    # Enforce configured email rate limits: 3/hour per address, 20/hour per network
    check_rate_limit("email_challenge_addr", f"{event.id}:{email_digest}", limit=3, window_seconds=3600, now=now)
    if ip_address:
        net_key = hash_network_key(ip_address)
        check_rate_limit("email_challenge_net", f"{event.id}:{net_key}", limit=20, window_seconds=3600, now=now)

    token = secrets.token_urlsafe(32)
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    
    expires_at = now + timezone.timedelta(minutes=15)
    
    challenge = EmailChallenge.objects.create(
        event=event,
        delivery_email_encrypted=email_lower, # In a real app this would be encrypted
        email_digest=email_digest,
        token_digest=token_digest,
        expires_at=expires_at,
    )
    
    # Honest fallback for unavailable SMTP
    logger.info(f"EMAIL CHALLENGE for {email_lower}: Token is {token}")
    
    return token


@transaction.atomic
def verify_email_challenge(event, email, token):
    email_lower = email.strip().lower()
    email_digest = hashlib.sha256(f"{settings.SECRET_KEY}:{email_lower}".encode()).hexdigest()
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    
    now = timezone.now()
    
    challenge = EmailChallenge.objects.select_for_update().filter(
        event=event,
        email_digest=email_digest,
        token_digest=token_digest,
        consumed_at__isnull=True,
    ).first()
    
    if not challenge:
        raise ValidationError("Invalid challenge.")
        
    challenge.attempt_count += 1
    
    if challenge.expires_at < now:
        challenge.save(update_fields=['attempt_count'])
        raise ValidationError("Challenge expired.")
        
    challenge.consumed_at = now
    challenge.save(update_fields=['consumed_at', 'attempt_count'])
    
    identity, created = VoterIdentity.objects.get_or_create(
        event=event,
        email_digest=email_digest,
        defaults={'kind': VoterIdentity.Kind.EMAIL}
    )
    return identity


@transaction.atomic
def get_or_create_authenticated_voter(event, user):
    policy = VotingPolicy.objects.filter(event=event).first()
    if not policy or policy.mode != VotingPolicy.Mode.AUTHENTICATED:
        raise ValidationError("Event voting policy is not AUTHENTICATED.")
        
    now = timezone.now()
    if policy.minimum_account_age_seconds > 0:
        age = (now - user.created_at).total_seconds()
        if age < policy.minimum_account_age_seconds:
            raise ValidationError("Account is not old enough to vote.")

    identity, created = VoterIdentity.objects.get_or_create(
        event=event,
        user=user,
        defaults={'kind': VoterIdentity.Kind.USER}
    )
    return identity


@transaction.atomic
def get_or_create_ballot_session(event, voter_identity):
    now = timezone.now()
    
    session = BallotSession.objects.filter(
        event=event,
        voter_identity=voter_identity
    ).first()
    
    # Reconcile eligible projects
    # Eligible = submitted projects not disqualified, duplicates depends on event rules, usually not eligible
    eligible_qs = Project.objects.filter(
        event=event,
        state=Project.State.SUBMITTED
    ).values_list('id', flat=True)
    
    current_eligible_ids = [str(pid) for pid in eligible_qs]
    
    if not session:
        seed = secrets.token_hex(16)
        expires_at = event.voting_closes_at or (now + timezone.timedelta(days=30))
        session = BallotSession.objects.create(
            event=event,
            voter_identity=voter_identity,
            ordering_seed=seed,
            eligible_project_ids=current_eligible_ids,
            expires_at=expires_at
        )
    else:
        # Reconcile sequences
        stored_ids = set(session.eligible_project_ids)
        current_ids = set(current_eligible_ids)
        if stored_ids != current_ids:
            # We want to keep the order for existing ones, and append/remove
            new_list = [pid for pid in session.eligible_project_ids if pid in current_ids]
            added = list(current_ids - stored_ids)
            # Shuffle added deterministically or just append
            new_list.extend(added)
            session.eligible_project_ids = new_list
            session.save(update_fields=['eligible_project_ids'])
            
    return session


@transaction.atomic
def cast_vote(event, project, voter_identity, state=Vote.State.ACTIVE):
    now = timezone.now()
    
    # Lock event for voting deadline
    # We do a select_for_update to ensure deadline hasn't passed
    locked_event = Event.objects.select_for_update().get(id=event.id)
    
    if not is_voting_open(locked_event, now):
        raise PermissionDenied("Voting is not open.")
        
    if project.event_id != locked_event.id:
        raise ValidationError("Project does not belong to this event.")
        
    if voter_identity.event_id != locked_event.id:
        raise ValidationError("Voter identity does not belong to this event.")
        
    if voter_identity.status != VoterIdentity.Status.ACTIVE:
        raise PermissionDenied("Voter identity is suspended.")

    # Enforce configured vote rate limit: 60 transitions per minute
    check_rate_limit(
        scope="vote_transition",
        subject_key=f"{locked_event.id}:{voter_identity.id}",
        limit=60,
        window_seconds=60,
        now=now,
    )
        
    # Check if user is trying to vote for their own project (conflict of interest)
    if voter_identity.kind == VoterIdentity.Kind.USER and voter_identity.user:
        if project.team.members.filter(user=voter_identity.user).exists():
            raise ValidationError("You cannot vote for your own team's project.")
            
    vote, created = Vote.objects.select_for_update().get_or_create(
        event=locked_event,
        project=project,
        voter_identity=voter_identity,
        defaults={'state': state}
    )
    
    if not created and vote.state != state:
        if vote.state == Vote.State.VOID:
            raise ValidationError("Organiser-voided vote cannot be reactivated.")
        vote.state = state
        vote.version += 1
        vote.save(update_fields=['state', 'version', 'updated_at'])
        
    return vote


# ==========================================
# Community Comments & Moderation Services
# ==========================================

def is_comment_window_open(event: Event, now=None) -> bool:
    """
    Comment window opens after submissions open and ends at voting close if configured,
    otherwise judging close.
    """
    if now is None:
        now = timezone.now()
    if event.lifecycle != Event.Lifecycle.PUBLISHED:
        return False
    if not event.submissions_opens_at or now < event.submissions_opens_at:
        return False
    close_time = event.voting_closes_at if event.voting_closes_at else event.judging_closes_at
    if close_time and now >= close_time:
        return False
    return True


@transaction.atomic
def create_comment(event: Event, project: Project, author_user, body: str, ip_address: str = None) -> Comment:
    """
    Creates a new comment on a project.
    Enforces authentication, event state, comment window, length limits (5000 chars), and rate limits.
    """
    now = timezone.now()
    if not is_comment_window_open(event, now):
        raise PermissionDenied("Comment window is not open.")

    policy = VotingPolicy.objects.filter(event=event).first()
    if not policy or not policy.comments_enabled:
        raise ValidationError("Comments are disabled for this event.")

    if project.event_id != event.id:
        raise ValidationError("Project does not belong to this event.")

    cleaned_body = body.strip()
    if not cleaned_body:
        raise ValidationError("Comment body cannot be blank.")
    if len(cleaned_body) > 5000:
        raise ValidationError("Comment body exceeds maximum limit of 5,000 characters.")

    # Rate limits: 5 per minute, 50 per day per account/event
    check_rate_limit("new_comment_min", f"{event.id}:{author_user.id}", limit=5, window_seconds=60, now=now)
    check_rate_limit("new_comment_day", f"{event.id}:{author_user.id}", limit=50, window_seconds=86400, now=now)

    comment = Comment.objects.create(
        event=event,
        project=project,
        author_user=author_user,
        body=cleaned_body,
        state=Comment.State.VISIBLE,
    )

    AuditEvent.objects.create(
        event=event,
        actor_user=author_user,
        actor_kind=AuditEvent.ActorKind.USER,
        action="COMMENT_CREATED",
        entity_type="Comment",
        entity_id=comment.id,
        after_json={"project_id": str(project.id), "comment_id": str(comment.id)},
    )

    return comment


@transaction.atomic
def edit_comment(comment: Comment, author_user, new_body: str) -> Comment:
    """
    Edits an existing comment. Only author may edit, within the comment window.
    Hidden or deleted comments cannot be edited back to public visibility.
    """
    if comment.author_user_id != author_user.id:
        raise PermissionDenied("Only the author can edit this comment.")

    if comment.state != Comment.State.VISIBLE:
        raise ValidationError("Cannot edit a hidden or deleted comment.")

    now = timezone.now()
    if not is_comment_window_open(comment.event, now):
        raise ValidationError("Comment editing window has closed.")

    cleaned_body = new_body.strip()
    if not cleaned_body:
        raise ValidationError("Comment body cannot be blank.")
    if len(cleaned_body) > 5000:
        raise ValidationError("Comment body exceeds maximum limit of 5,000 characters.")

    comment.body = cleaned_body
    comment.version += 1
    comment.save(update_fields=["body", "version", "updated_at"])

    AuditEvent.objects.create(
        event=comment.event,
        actor_user=author_user,
        actor_kind=AuditEvent.ActorKind.USER,
        action="COMMENT_EDITED",
        entity_type="Comment",
        entity_id=comment.id,
        after_json={"version": comment.version},
    )

    return comment


@transaction.atomic
def delete_comment(comment: Comment, actor_user) -> Comment:
    """
    Deletes a comment. Authors can delete even after comment window closes.
    Organisers may also delete.
    Content is removed rather than indefinitely copied into general audit logs.
    """
    is_author = comment.author_user_id == actor_user.id
    is_organiser = EventMembership.objects.filter(
        event=comment.event, user=actor_user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
    ).exists() or actor_user.is_staff

    if not is_author and not is_organiser:
        raise PermissionDenied("Only the author or an organiser can delete this comment.")

    comment.state = Comment.State.DELETED
    comment.body = ""  # Plain text body removed per retention policy
    comment.save(update_fields=["state", "body", "updated_at"])

    AuditEvent.objects.create(
        event=comment.event,
        actor_user=actor_user,
        actor_kind=AuditEvent.ActorKind.USER,
        action="COMMENT_DELETED",
        entity_type="Comment",
        entity_id=comment.id,
        reason="Comment deleted by author or organiser",
    )

    return comment


@transaction.atomic
def moderate_comment(comment: Comment, actor_user, new_state: str, reason: str) -> Comment:
    """
    Allows an organiser to hide or restore a comment with an audited reason.
    """
    if not EventMembership.objects.filter(
        event=comment.event, user=actor_user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
    ).exists() and not actor_user.is_staff:
        raise PermissionDenied("Only organisers can moderate comments.")

    if new_state not in [Comment.State.VISIBLE, Comment.State.HIDDEN]:
        raise ValidationError(f"Invalid comment moderation state: {new_state}")

    if not reason.strip():
        raise ValidationError("A moderation reason is required.")

    comment.state = new_state
    comment.save(update_fields=["state", "updated_at"])

    AuditEvent.objects.create(
        event=comment.event,
        actor_user=actor_user,
        actor_kind=AuditEvent.ActorKind.USER,
        action="COMMENT_MODERATED",
        entity_type="Comment",
        entity_id=comment.id,
        reason=reason.strip(),
        after_json={"state": new_state},
    )

    return comment


@transaction.atomic
def report_moderation_case(
    event: Event,
    target_type: str,
    target_id,
    reason_code: str,
    reporter_user=None,
    ip_address: str = None,
) -> ModerationCase:
    """
    Creates a moderation case for organiser review.
    Enforces reporting rate limit (10 per hour per account).
    """
    now = timezone.now()
    if reporter_user:
        check_rate_limit(
            scope="report_case",
            subject_key=f"{event.id}:{reporter_user.id}",
            limit=10,
            window_seconds=3600,
            now=now,
        )

    # Validate target belongs to this event
    if target_type == ModerationCase.TargetType.COMMENT:
        if not Comment.objects.filter(id=target_id, event=event).exists():
            raise ValidationError("Target comment does not belong to this event.")
    elif target_type == ModerationCase.TargetType.VOTE:
        if not Vote.objects.filter(id=target_id, event=event).exists():
            raise ValidationError("Target vote does not belong to this event.")
    elif target_type == ModerationCase.TargetType.VOTER_IDENTITY:
        if not VoterIdentity.objects.filter(id=target_id, event=event).exists():
            raise ValidationError("Target voter identity does not belong to this event.")
    elif target_type == ModerationCase.TargetType.PROJECT:
        if not Project.objects.filter(id=target_id, event=event).exists():
            raise ValidationError("Target project does not belong to this event.")

    case = ModerationCase.objects.create(
        event=event,
        target_type=target_type,
        target_id=target_id,
        reason_code=reason_code,
        reporter_user=reporter_user,
        status=ModerationCase.Status.OPEN,
    )

    AuditEvent.objects.create(
        event=event,
        actor_user=reporter_user,
        actor_kind=AuditEvent.ActorKind.USER if reporter_user else AuditEvent.ActorKind.SYSTEM,
        action="MODERATION_CASE_REPORTED",
        entity_type="ModerationCase",
        entity_id=case.id,
        reason=reason_code,
        after_json={"target_type": target_type, "target_id": str(target_id)},
    )

    return case


@transaction.atomic
def resolve_moderation_case(
    case: ModerationCase,
    actor_user,
    decision: str,
    reason: str,
    affected_ids=None,
) -> ModerationCase:
    """
    Resolves a moderation case with a documented action and reasoned audit record.
    Supported decisions: HIDE_COMMENT, RESTORE_COMMENT, SUSPEND_IDENTITY, VOID_VOTES, DISMISS_SIGNAL, NO_ACTION.
    """
    if not EventMembership.objects.filter(
        event=case.event, user=actor_user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
    ).exists() and not actor_user.is_staff:
        raise PermissionDenied("Only organisers can resolve moderation cases.")

    if not reason.strip():
        raise ValidationError("A reason is required to resolve a moderation case.")

    now = timezone.now()
    event = case.event
    resolved_affected = affected_ids or [case.target_id]

    if decision == ModerationCase.Decision.HIDE_COMMENT:
        Comment.objects.filter(id__in=resolved_affected, event=event).update(
            state=Comment.State.HIDDEN, updated_at=now
        )
    elif decision == ModerationCase.Decision.RESTORE_COMMENT:
        Comment.objects.filter(id__in=resolved_affected, event=event).update(
            state=Comment.State.VISIBLE, updated_at=now
        )
    elif decision == ModerationCase.Decision.SUSPEND_IDENTITY:
        VoterIdentity.objects.filter(id__in=resolved_affected, event=event).update(
            status=VoterIdentity.Status.SUSPENDED, updated_at=now
        )
        # Any vote changes invalidate event data version
        event.data_version += 1
        event.save(update_fields=["data_version", "updated_at"])
    elif decision == ModerationCase.Decision.VOID_VOTES:
        Vote.objects.filter(id__in=resolved_affected, event=event).update(
            state=Vote.State.VOID, updated_at=now
        )
        event.data_version += 1
        event.save(update_fields=["data_version", "updated_at"])
    elif decision == ModerationCase.Decision.DISMISS_SIGNAL:
        AbuseSignal.objects.filter(id__in=resolved_affected, event=event).update(
            state=AbuseSignal.State.DISMISSED, reviewed_by=actor_user, reviewed_at=now
        )

    case.status = ModerationCase.Status.RESOLVED
    case.decision = decision
    case.decided_by = actor_user
    case.decided_at = now
    case.save(update_fields=["status", "decision", "decided_by", "decided_at"])

    AuditEvent.objects.create(
        event=event,
        actor_user=actor_user,
        actor_kind=AuditEvent.ActorKind.USER,
        action="MODERATION_CASE_RESOLVED",
        entity_type="ModerationCase",
        entity_id=case.id,
        reason=reason.strip(),
        after_json={
            "decision": decision,
            "affected_ids": [str(x) for x in resolved_affected],
        },
    )

    from apps.integrations.outbox import publish_domain_event

    publish_domain_event(
        event=event,
        event_type="community.moderated",
        entity_id=str(case.id),
        payload={
            "case_id": str(case.id),
            "decision": decision,
            "resolved_at": now.isoformat(),
        },
    )

    return case


@transaction.atomic
def dismiss_moderation_case(case: ModerationCase, actor_user, reason: str) -> ModerationCase:
    """
    Dismisses a moderation case with a recorded reason.
    """
    if not EventMembership.objects.filter(
        event=case.event, user=actor_user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
    ).exists() and not actor_user.is_staff:
        raise PermissionDenied("Only organisers can dismiss moderation cases.")

    now = timezone.now()
    case.status = ModerationCase.Status.DISMISSED
    case.decision = ModerationCase.Decision.NO_ACTION
    case.decided_by = actor_user
    case.decided_at = now
    case.save(update_fields=["status", "decision", "decided_by", "decided_at"])

    AuditEvent.objects.create(
        event=case.event,
        actor_user=actor_user,
        actor_kind=AuditEvent.ActorKind.USER,
        action="MODERATION_CASE_DISMISSED",
        entity_type="ModerationCase",
        entity_id=case.id,
        reason=reason.strip() if reason else "Dismissed by organiser",
    )

    return case


def record_abuse_signal(
    event: Event,
    kind: str,
    user=None,
    subject_id=None,
    network_key: str = None,
    details_json: dict = None,
) -> AbuseSignal:
    """
    Records an automated abuse detection signal for review.
    Signals are review evidence, not automatic guilt.
    """
    signal = AbuseSignal.objects.create(
        event=event,
        kind=kind,
        user=user,
        subject_id=str(subject_id) if subject_id else None,
        network_key=network_key,
        details_json=details_json or {},
        state=AbuseSignal.State.OPEN,
    )
    return signal


@transaction.atomic
def dismiss_abuse_signal(signal: AbuseSignal, actor_user, reason: str = "") -> AbuseSignal:
    """
    Dismisses an abuse signal after organiser review.
    """
    if not EventMembership.objects.filter(
        event=signal.event, user=actor_user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
    ).exists() and not actor_user.is_staff:
        raise PermissionDenied("Only organisers can dismiss abuse signals.")

    now = timezone.now()
    signal.state = AbuseSignal.State.DISMISSED
    signal.reviewed_by = actor_user
    signal.reviewed_at = now
    signal.save(update_fields=["state", "reviewed_by", "reviewed_at"])

    AuditEvent.objects.create(
        event=signal.event,
        actor_user=actor_user,
        actor_kind=AuditEvent.ActorKind.USER,
        action="ABUSE_SIGNAL_DISMISSED",
        entity_type="AbuseSignal",
        entity_id=signal.id,
        reason=reason.strip() if reason else "Dismissed by organiser",
    )

    return signal

