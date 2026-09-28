import hashlib
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from apps.audit.models import AuditEvent
from apps.events.models import Event, Track
from apps.submissions.models import Project, ProjectRevision
from apps.teams.models import Team, TeamMember


class StaleSaveConflict(Exception):
    def __init__(self, current_version, current_revision):
        super().__init__("Stale save conflict: project was modified by another user.")
        self.current_version = current_version
        self.current_revision = current_revision


def save_draft_submission(user, event, team, data, expected_version=None, project_id=None):
    now = timezone.now()
    if not event.is_submission_open(now):
        raise ValidationError(f"Submissions closed at {event.submissions_closes_at.isoformat()}. Editing is blocked.")

    # User must be member of this team
    if not TeamMember.objects.filter(event=event, team=team, user=user).exists():
        raise PermissionDenied("You are not a member of this team.")

    with transaction.atomic():
        if project_id:
            project = Project.objects.select_for_update().get(id=project_id, event=event, team=team)
            if expected_version is not None and project.version != expected_version:
                raise StaleSaveConflict(project.version, project.draft_revision)
        else:
            project = Project.objects.filter(event=event, team=team).first()
            if not project:
                project = Project.objects.create(event=event, team=team, state=Project.State.DRAFT, version=0)

        track_id = data.get("track_id")
        track = Track.objects.get(id=track_id, event=event) if track_id else None
        if not track:
            track = event.tracks.first()

        last_rev = project.revisions.order_by("-number").first()
        next_number = (last_rev.number + 1) if last_rev else 1

        roster_snapshot = [
            {"user_id": str(m.user.id), "display_name": m.user.display_name, "email": m.user.email}
            for m in team.members.select_related("user")
        ]

        new_rev = ProjectRevision.objects.create(
            project=project,
            number=next_number,
            track=track,
            title=data.get("title", "").strip() or "Untitled Draft",
            summary=data.get("summary", "").strip(),
            description_md=data.get("description_md", ""),
            repo_url=data.get("repo_url", "").strip() or None,
            demo_url=data.get("demo_url", "").strip() or None,
            roster_snapshot=roster_snapshot,
            created_by=user,
            source=ProjectRevision.Source.USER,
        )

        project.draft_revision = new_rev
        project.version += 1
        project.save(update_fields=["draft_revision", "version", "updated_at"])

        return project, new_rev


def submit_project(user, event, project_id):
    now = timezone.now()
    if not event.is_submission_open(now):
        raise ValidationError(f"Submissions closed at {event.submissions_closes_at.isoformat()}. No late submissions permitted.")

    with transaction.atomic():
        project = Project.objects.select_for_update().get(id=project_id, event=event)

        # Only the team captain can submit
        if project.team.captain_user_id != user.id:
            raise PermissionDenied("Only the team captain can submit or update the official submission.")

        # Team size check
        team_count = project.team.members.count()
        if team_count < event.min_team_size or team_count > event.max_team_size:
            raise ValidationError(
                f"Team size ({team_count}) must be between {event.min_team_size} and {event.max_team_size} members."
            )

        draft = project.draft_revision
        if not draft:
            raise ValidationError("Cannot submit without a project draft.")

        if not draft.title or not draft.summary or not draft.track or not draft.repo_url:
            raise ValidationError("Submission requires title, summary, track, and a valid repository URL.")

        # Fresh roster snapshot at explicit submit time
        current_roster = [
            {"user_id": str(m.user.id), "display_name": m.user.display_name, "email": m.user.email}
            for m in project.team.members.select_related("user")
        ]
        if draft.roster_snapshot != current_roster:
            draft = ProjectRevision.objects.create(
                project=project,
                number=draft.number + 1,
                track=draft.track,
                title=draft.title,
                summary=draft.summary,
                description_md=draft.description_md,
                repo_url=draft.repo_url,
                demo_url=draft.demo_url,
                roster_snapshot=current_roster,
                created_by=user,
                source=ProjectRevision.Source.USER,
            )
            project.draft_revision = draft

        # Set submitted revision
        is_first = project.first_submitted_at is None
        project.submitted_revision = draft
        project.state = Project.State.SUBMITTED
        if is_first:
            project.first_submitted_at = now
        project.last_submitted_at = now
        project.version += 1
        project.save(update_fields=[
            "submitted_revision", "draft_revision", "state",
            "first_submitted_at", "last_submitted_at", "version", "updated_at"
        ])

        event.data_version += 1
        event.save(update_fields=["data_version"])

        AuditEvent.objects.create(
            event=event,
            actor_user=user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="PROJECT_SUBMITTED",
            entity_type="Project",
            entity_id=project.id,
            after_json={
                "revision_number": draft.number,
                "title": draft.title,
                "timestamp": now.isoformat(),
            },
            reason="Explicit captain submission",
        )

        receipt = {
            "project_id": str(project.id),
            "revision_number": draft.number,
            "title": draft.title,
            "submitted_at": now.isoformat(),
            "submitted_by": user.email,
            "captain_name": user.display_name,
            "roster_size": len(current_roster),
            "roster": current_roster,
        }
        return project, receipt
