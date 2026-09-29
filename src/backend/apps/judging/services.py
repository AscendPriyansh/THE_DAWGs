import uuid
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.events.models import Event, EventMembership, Track
from apps.judging.models import (
    Criterion,
    JudgeAssignment,
    JudgeTrackPermission,
    Review,
    ReviewRevision,
    ReviewScore,
    Rubric,
)
from apps.submissions.models import Project, ProjectRevision
from apps.teams.models import TeamMember


def freeze_rubric(actor_user, rubric: Rubric) -> Rubric:
    """
    Validates rubric has at least one criterion with positive weight,
    and freezes it. Once frozen, criteria are immutable.
    """
    if rubric.state == Rubric.State.FROZEN:
        return rubric

    criteria = list(rubric.criteria.all())
    if not criteria:
        raise ValidationError("Cannot freeze a rubric without criteria.")

    for c in criteria:
        if c.weight <= 0:
            raise ValidationError(f"Criterion '{c.label}' must have positive weight.")

    with transaction.atomic():
        rubric.state = Rubric.State.FROZEN
        rubric.frozen_at = timezone.now()
        rubric.save(update_fields=["state", "frozen_at", "updated_at"])

        AuditEvent.objects.create(
            event=rubric.event,
            actor_user=actor_user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="RUBRIC_FROZEN",
            entity_type="Rubric",
            entity_id=rubric.id,
            after_json={
                "version": rubric.version_number,
                "criteria_count": len(criteria),
            },
            reason="Organiser froze rubric prior to assignments",
        )
    return rubric


def grant_judge_track_permission(
    actor_user, judge_membership: EventMembership, track: Track, reason: str = ""
) -> JudgeTrackPermission:
    """Grants explicit track permission to a judge in an event."""
    if judge_membership.event_id != track.event_id:
        raise ValidationError("Judge membership and Track must belong to the same event.")

    if judge_membership.role != EventMembership.Role.JUDGE:
        raise ValidationError("Permissions can only be granted to members with the JUDGE role.")

    perm, _ = JudgeTrackPermission.objects.get_or_create(
        membership=judge_membership,
        track=track,
        defaults={"granted_by": actor_user, "reason": reason},
    )
    return perm


def assign_judge_to_project(
    actor_user,
    event: Event,
    judge_membership: EventMembership,
    project: Project,
    rubric: Rubric,
    source: str = JudgeAssignment.Source.MANUAL,
) -> JudgeAssignment:
    """
    Assigns an active judge to an official submitted project revision.
    Enforces event matching, frozen rubric, active project submission,
    track permission, and conflict of interest (judge is not team member).
    """
    if judge_membership.event_id != event.id:
        raise ValidationError("Judge membership does not belong to this event.")

    if judge_membership.role != EventMembership.Role.JUDGE or judge_membership.status != EventMembership.Status.ACTIVE:
        raise ValidationError("User is not an active judge for this event.")

    if project.event_id != event.id:
        raise ValidationError("Project does not belong to this event.")

    if rubric.event_id != event.id:
        raise ValidationError("Rubric does not belong to this event.")

    if rubric.state != Rubric.State.FROZEN:
        raise ValidationError("Rubric must be frozen before creating judge assignments.")

    if project.state != Project.State.SUBMITTED or not project.submitted_revision:
        raise ValidationError("Only officially submitted projects can be assigned to judges.")

    target_revision = project.submitted_revision
    target_track = target_revision.track
    if not target_track:
        raise ValidationError("Submitted project revision has no assigned track.")

    # Check track permission
    has_perm = JudgeTrackPermission.objects.filter(
        membership=judge_membership, track=target_track
    ).exists()
    if not has_perm:
        raise PermissionDenied(
            f"Judge {judge_membership.user.display_name} does not have track permission for '{target_track.name}'."
        )

    # Conflict check: judge cannot be on the project's team
    if TeamMember.objects.filter(team=project.team, user=judge_membership.user).exists():
        raise PermissionDenied("Conflict of interest: Judge is a member of the project team.")

    with transaction.atomic():
        assignment, created = JudgeAssignment.objects.get_or_create(
            event=event,
            judge_membership=judge_membership,
            project=project,
            status=JudgeAssignment.Status.ACTIVE,
            defaults={
                "project_revision": target_revision,
                "rubric": rubric,
                "assigned_by": actor_user,
                "source": source,
                "assigned_at": timezone.now(),
            },
        )
        if created:
            AuditEvent.objects.create(
                event=event,
                actor_user=actor_user,
                actor_kind=AuditEvent.ActorKind.USER,
                action="JUDGE_ASSIGNED",
                entity_type="JudgeAssignment",
                entity_id=assignment.id,
                after_json={
                    "judge_id": str(judge_membership.user_id),
                    "project_id": str(project.id),
                    "track_id": str(target_track.id),
                },
            )
    return assignment


def revoke_judge_assignment(actor_user, assignment: JudgeAssignment, reason: str = ""):
    if assignment.status != JudgeAssignment.Status.ACTIVE:
        return assignment

    with transaction.atomic():
        assignment.status = JudgeAssignment.Status.REVOKED
        assignment.revoked_at = timezone.now()
        assignment.revocation_reason = reason
        assignment.save(update_fields=["status", "revoked_at", "revocation_reason", "updated_at"])

        AuditEvent.objects.create(
            event=assignment.event,
            actor_user=actor_user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="JUDGE_ASSIGNMENT_REVOKED",
            entity_type="JudgeAssignment",
            entity_id=assignment.id,
            reason=reason,
        )
    return assignment


def save_draft_review(
    judge_user,
    assignment: JudgeAssignment,
    scores_dict: dict,
    comment: str = "",
) -> Review:
    """
    Saves an in-progress draft review.
    Allows partial criteria scoring, but each score provided must be between 1 and 5.
    Creates an immutable ReviewRevision audit snapshot.
    """
    now = timezone.now()
    event = assignment.event

    if not (event.judging_opens_at <= now < event.judging_closes_at):
        raise PermissionDenied("Judging window is closed. Scores cannot be edited.")

    if assignment.judge_membership.user_id != judge_user.id:
        raise PermissionDenied("You can only review assignments assigned to you.")

    if assignment.status != JudgeAssignment.Status.ACTIVE:
        raise ValidationError("Cannot review an inactive or revoked assignment.")

    valid_criteria = {str(c.id): c for c in assignment.rubric.criteria.all()}
    valid_keys = {c.key: c for c in assignment.rubric.criteria.all()}

    with transaction.atomic():
        review, _ = Review.objects.get_or_create(
            assignment=assignment,
            defaults={"status": Review.Status.DRAFT, "comment": comment},
        )
        review.comment = comment
        review.version += 1
        review.save(update_fields=["comment", "version", "updated_at"])

        # Update scores
        for crit_ref, val in scores_dict.items():
            criterion = valid_criteria.get(str(crit_ref)) or valid_keys.get(str(crit_ref))
            if not criterion:
                continue

            try:
                numeric_val = int(val)
            except (ValueError, TypeError):
                raise ValidationError(f"Score for {criterion.label} must be an integer between 1 and 5.")

            if numeric_val < 1 or numeric_val > 5:
                raise ValidationError(f"Score for {criterion.label} must be between 1 and 5.")

            ReviewScore.objects.update_or_create(
                review=review,
                criterion=criterion,
                defaults={"value": numeric_val},
            )

        # Snapshot
        current_scores = [
            {"criterion_key": s.criterion.key, "criterion_label": s.criterion.label, "value": s.value}
            for s in review.scores.select_related("criterion")
        ]
        rev_count = review.revisions.count()
        ReviewRevision.objects.create(
            review=review,
            number=rev_count + 1,
            status=Review.Status.DRAFT,
            scores_snapshot=current_scores,
            comment_snapshot=comment,
            actor_user=judge_user,
            source=ReviewRevision.Source.USER,
        )

    return review


def submit_review(
    judge_user,
    assignment: JudgeAssignment,
    scores_dict: dict,
    comment: str = "",
) -> Review:
    """
    Submits a final evaluation.
    Requires scores for ALL criteria in the rubric (1–5).
    Marks review as SUBMITTED and updates event data_version.
    """
    now = timezone.now()
    event = assignment.event

    if not (event.judging_opens_at <= now < event.judging_closes_at):
        raise PermissionDenied("Judging window is closed. Official evaluations cannot be submitted.")

    if assignment.judge_membership.user_id != judge_user.id:
        raise PermissionDenied("You can only review assignments assigned to you.")

    if assignment.status != JudgeAssignment.Status.ACTIVE:
        raise ValidationError("Cannot review an inactive or revoked assignment.")

    all_criteria = list(assignment.rubric.criteria.all())
    valid_criteria = {str(c.id): c for c in all_criteria}
    valid_keys = {c.key: c for c in all_criteria}

    # Verify every criterion has a score
    resolved_scores = {}
    for crit in all_criteria:
        val = scores_dict.get(str(crit.id)) or scores_dict.get(crit.key)
        if val is None:
            raise ValidationError(f"Missing required score for criterion '{crit.label}'.")
        try:
            numeric_val = int(val)
        except (ValueError, TypeError):
            raise ValidationError(f"Score for {crit.label} must be an integer between 1 and 5.")
        if numeric_val < 1 or numeric_val > 5:
            raise ValidationError(f"Score for {crit.label} must be between 1 and 5.")
        resolved_scores[crit] = numeric_val

    with transaction.atomic():
        review, _ = Review.objects.get_or_create(
            assignment=assignment,
            defaults={"status": Review.Status.SUBMITTED, "comment": comment},
        )
        review.comment = comment
        review.status = Review.Status.SUBMITTED
        review.submitted_at = now
        review.version += 1
        review.save(update_fields=["comment", "status", "submitted_at", "version", "updated_at"])

        for criterion, numeric_val in resolved_scores.items():
            ReviewScore.objects.update_or_create(
                review=review,
                criterion=criterion,
                defaults={"value": numeric_val},
            )

        current_scores = [
            {"criterion_key": s.criterion.key, "criterion_label": s.criterion.label, "value": s.value}
            for s in review.scores.select_related("criterion")
        ]
        rev_count = review.revisions.count()
        ReviewRevision.objects.create(
            review=review,
            number=rev_count + 1,
            status=Review.Status.SUBMITTED,
            scores_snapshot=current_scores,
            comment_snapshot=comment,
            actor_user=judge_user,
            source=ReviewRevision.Source.USER,
        )

        event.data_version += 1
        event.save(update_fields=["data_version"])

        AuditEvent.objects.create(
            event=event,
            actor_user=judge_user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="REVIEW_SUBMITTED",
            entity_type="Review",
            entity_id=review.id,
            after_json={
                "assignment_id": str(assignment.id),
                "project_id": str(assignment.project_id),
                "scores": current_scores,
            },
        )

        from apps.integrations.outbox import publish_domain_event

        publish_domain_event(
            event=event,
            event_type="review.submitted",
            entity_id=str(review.id),
            payload={
                "review_id": str(review.id),
                "assignment_id": str(assignment.id),
                "project_id": str(assignment.project_id),
                "status": review.status,
                "submitted_at": review.submitted_at.isoformat(),
            },
        )

    return review


def get_event_judging_progress(event: Event) -> dict:
    """
    Computes private coverage statistics for the organiser dashboard.
    Returns counts of required reviews, completed reviews, and per-project coverage.
    """
    projects = (
        Project.objects.filter(event=event, state=Project.State.SUBMITTED)
        .select_related("submitted_revision__track", "team")
        .order_by("submitted_revision__title")
    )

    required_per_project = event.required_reviews

    project_stats = []
    total_assigned = 0
    total_completed = 0

    assignments = (
        JudgeAssignment.objects.filter(event=event, status=JudgeAssignment.Status.ACTIVE)
        .select_related("review")
    )

    assignment_by_project = {}
    completed_by_project = {}

    for a in assignments:
        assignment_by_project[a.project_id] = assignment_by_project.get(a.project_id, 0) + 1
        total_assigned += 1
        if hasattr(a, "review") and a.review.status == Review.Status.SUBMITTED:
            completed_by_project[a.project_id] = completed_by_project.get(a.project_id, 0) + 1
            total_completed += 1

    fully_covered = 0
    partially_covered = 0
    uncovered = 0

    for p in projects:
        num_assigned = assignment_by_project.get(p.id, 0)
        num_completed = completed_by_project.get(p.id, 0)

        if num_completed >= required_per_project:
            status = "FULLY_COVERED"
            fully_covered += 1
        elif num_completed > 0:
            status = "PARTIALLY_COVERED"
            partially_covered += 1
        else:
            status = "UNCOVERED"
            uncovered += 1

        rev = p.submitted_revision
        project_stats.append({
            "project_id": str(p.id),
            "title": rev.title if rev else "Untitled",
            "track_name": rev.track.name if (rev and rev.track) else "No Track",
            "team_name": p.team.name,
            "assigned_reviews": num_assigned,
            "completed_reviews": num_completed,
            "required_reviews": required_per_project,
            "status": status,
        })

    return {
        "event_id": str(event.id),
        "event_name": event.name,
        "total_projects": len(project_stats),
        "required_reviews_per_project": required_per_project,
        "total_active_assignments": total_assigned,
        "total_completed_reviews": total_completed,
        "fully_covered_count": fully_covered,
        "partially_covered_count": partially_covered,
        "uncovered_count": uncovered,
        "projects": project_stats,
    }
