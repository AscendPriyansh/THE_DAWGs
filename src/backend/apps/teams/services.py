import hashlib
import secrets
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.audit.models import AuditEvent
from apps.events.models import Event, EventMembership
from apps.teams.models import Team, TeamMember, TeamInvitation


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_team(user, event, name: str) -> Team:
    now = timezone.now()
    if not (event.registration_opens_at <= now < event.registration_closes_at):
        raise ValidationError("Team formation is closed for this event.")

    membership = EventMembership.objects.filter(
        event=event, user=user, status=EventMembership.Status.ACTIVE
    ).first()
    if not membership or membership.role != EventMembership.Role.PARTICIPANT:
        raise PermissionDenied("Only registered participants can create a team.")

    if TeamMember.objects.filter(event=event, user=user).exists():
        raise ValidationError("You are already a member of a team in this event.")

    clean_name = name.strip()
    if not clean_name:
        raise ValidationError("Team name cannot be empty.")

    with transaction.atomic():
        team = Team.objects.create(
            event=event,
            name=clean_name,
            captain_user=user,
            status=Team.Status.ACTIVE,
        )
        TeamMember.objects.create(
            event=event,
            team=team,
            user=user,
            joined_at=now,
        )
        AuditEvent.objects.create(
            event=event,
            actor_user=user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="TEAM_CREATED",
            entity_type="Team",
            entity_id=team.id,
            after_json={"name": team.name, "captain": user.display_name},
        )
        return team


def create_team_invitation(captain_user, team, invited_email=None, expires_in_days=7):
    if team.captain_user_id != captain_user.id:
        raise PermissionDenied("Only the team captain can invite new members.")

    if team.members.count() >= team.event.max_team_size:
        raise ValidationError(f"Team already at maximum capacity of {team.event.max_team_size} members.")

    raw_token = secrets.token_urlsafe(32)
    token_digest = hash_token(raw_token)
    expires_at = timezone.now() + timezone.timedelta(days=expires_in_days)

    inv = TeamInvitation.objects.create(
        team=team,
        token_digest=token_digest,
        invited_email=invited_email.strip().lower() if invited_email else None,
        expires_at=expires_at,
        created_by=captain_user,
    )
    return inv, raw_token


def accept_team_invitation(user, raw_token: str) -> TeamMember:
    token_digest = hash_token(raw_token)
    now = timezone.now()

    with transaction.atomic():
        inv = TeamInvitation.objects.select_for_update().filter(
            token_digest=token_digest,
            revoked_at__isnull=True,
            accepted_at__isnull=True,
        ).first()

        if not inv:
            raise ValidationError("Invitation link is invalid, already used, or expired.")

        if now > inv.expires_at:
            raise ValidationError("Invitation link has expired.")

        team = inv.team
        event = team.event

        # Check invited email constraint if bound
        if inv.invited_email and user.email.lower() != inv.invited_email.lower():
            raise PermissionDenied(f"This invite is intended for {inv.invited_email}.")

        # Check membership and capacity
        if TeamMember.objects.filter(event=event, user=user).exists():
            raise ValidationError("You are already on a team in this event.")

        if team.members.count() >= event.max_team_size:
            raise ValidationError("This team has already reached its maximum member limit.")

        member = TeamMember.objects.create(
            event=event,
            team=team,
            user=user,
            joined_at=now,
        )

        inv.accepted_at = now
        inv.accepted_by = user
        inv.save(update_fields=["accepted_at", "accepted_by", "updated_at"])

        AuditEvent.objects.create(
            event=event,
            actor_user=user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="TEAM_INVITATION_ACCEPTED",
            entity_type="TeamInvitation",
            entity_id=inv.id,
            after_json={"team_id": str(team.id), "user_id": str(user.id)},
        )

        return member
