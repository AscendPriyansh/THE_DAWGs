from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.events.models import Event, EventMembership
from apps.judging.calculation import (
    canonical_json_hash,
    execute_scoring_run,
)
from apps.judging.models import Criterion, JudgeAssignment, Review, Rubric
from apps.results.models import Publication, ResultRow, ResultRun
from apps.submissions.models import Project


def build_event_input_snapshot(event: Event, cohort_scope: str = None) -> dict:
    """
    Extracts a canonical database snapshot of projects, frozen rubric criteria,
    and submitted reviews for calculation.
    Excludes account emails, invite tokens, and private comments.
    """
    if cohort_scope is None:
        cohort_scope = event.ranking_scope

    # 1. Eligible submitted projects
    projects_qs = (
        Project.objects.filter(event=event, state=Project.State.SUBMITTED)
        .select_related("submitted_revision__track")
        .order_by("id")
    )

    projects_data = []
    for p in projects_qs:
        rev = p.submitted_revision
        track_slug = rev.track.slug if (rev and rev.track) else "general"
        cohort_key = track_slug if cohort_scope == Event.RankingScope.TRACK else "EVENT"

        assigned_count = JudgeAssignment.objects.filter(
            event=event, project=p, status=JudgeAssignment.Status.ACTIVE
        ).count()

        projects_data.append({
            "id": str(p.id),
            "title": rev.title if rev else "",
            "track_slug": track_slug,
            "cohort_key": cohort_key,
            "eligible": True,
            "exclusion_reason": "",
            "assigned_count": assigned_count,
        })

    # 2. Frozen rubric criteria
    frozen_rubric = Rubric.objects.filter(event=event, state=Rubric.State.FROZEN).order_by("-version_number").first()
    if not frozen_rubric:
        # Fallback to latest rubric
        frozen_rubric = Rubric.objects.filter(event=event).order_by("-version_number").first()

    criteria_data = []
    if frozen_rubric:
        for c in frozen_rubric.criteria.all().order_by("display_order", "id"):
            criteria_data.append({
                "id": str(c.id),
                "key": c.key,
                "label": c.label,
                "weight": str(c.weight),
            })

    # 3. Submitted reviews
    reviews_qs = (
        Review.objects.filter(
            assignment__event=event,
            status=Review.Status.SUBMITTED,
            assignment__status=JudgeAssignment.Status.ACTIVE,
        )
        .select_related("assignment", "assignment__judge_membership")
        .prefetch_related("scores__criterion")
        .order_by("id")
    )

    reviews_data = []
    for r in reviews_qs:
        scores = {s.criterion.key: s.value for s in r.scores.all()}
        reviews_data.append({
            "review_id": str(r.id),
            "project_id": str(r.assignment.project_id),
            "judge_id": str(r.assignment.judge_membership.user_id),
            "scores": scores,
        })

    return {
        "event_id": str(event.id),
        "source_data_version": event.data_version,
        "ranking_scope": cohort_scope,
        "projects": projects_data,
        "criteria": criteria_data,
        "reviews": reviews_data,
    }


def calculate_result_run(
    actor_user,
    event: Event,
    algorithm: str = None,
    lambda_val: float = None,
) -> ResultRun:
    """
    Executes a private result run calculation and persists ResultRun & ResultRows.
    """
    membership = EventMembership.objects.filter(
        event=event, user=actor_user, status=EventMembership.Status.ACTIVE
    ).first()
    if not membership or (membership.role != EventMembership.Role.ORGANISER and not actor_user.is_staff):
        raise PermissionDenied("Only organisers can calculate result runs.")

    if algorithm is None:
        algorithm = event.ranking_method
    if lambda_val is None:
        lambda_val = event.normalisation_lambda

    snapshot = build_event_input_snapshot(event)
    input_sha256 = canonical_json_hash(snapshot)

    ranked_rows, diagnostics = execute_scoring_run(
        input_snapshot=snapshot,
        algorithm=algorithm,
        lambda_val=lambda_val,
        required_reviews=event.required_reviews,
    )

    output_sha256 = canonical_json_hash(ranked_rows)

    with transaction.atomic():
        run = ResultRun.objects.create(
            event=event,
            source_data_version=event.data_version,
            algorithm_version=algorithm,
            parameters_json={"lambda": lambda_val, "ranking_scope": event.ranking_scope},
            input_snapshot_json=snapshot,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
            diagnostics_json=diagnostics,
            created_by=actor_user,
        )

        row_objects = []
        for r in ranked_rows:
            row_objects.append(
                ResultRow(
                    result_run=run,
                    project_id=r["project_id"],
                    cohort_key=r["cohort_key"],
                    eligible=r["eligible"],
                    exclusion_reason=r["exclusion_reason"],
                    completed_review_count=r["completed_review_count"],
                    assigned_review_count=r["assigned_review_count"],
                    raw_mean=r["raw_mean"],
                    adjusted_value=r["adjusted_value"],
                    ranking_value=r["ranking_value"],
                    comparison_component=r["comparison_component"],
                    rank=r["rank"],
                    flags_json=r["flags_json"],
                )
            )
        ResultRow.objects.bulk_create(row_objects)

        AuditEvent.objects.create(
            event=event,
            actor_user=actor_user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="RESULT_RUN_CALCULATED",
            entity_type="ResultRun",
            entity_id=run.id,
            after_json={
                "algorithm": algorithm,
                "data_version": event.data_version,
                "input_sha256": input_sha256,
                "output_sha256": output_sha256,
            },
        )

    return run


def publish_results(
    actor_user,
    event: Event,
    result_run: ResultRun,
    public_note_md: str = "",
    waivers: list = None,
) -> Publication:
    """
    Formally publishes a result run as the public leaderboard.
    Validates judging window has closed, data version matches, and freezes judging.
    """
    membership = EventMembership.objects.filter(
        event=event, user=actor_user, status=EventMembership.Status.ACTIVE
    ).first()
    if not membership or (membership.role != EventMembership.Role.ORGANISER and not actor_user.is_staff):
        raise PermissionDenied("Only organisers can publish results.")

    if result_run.event_id != event.id:
        raise ValidationError("Result run does not belong to this event.")

    if result_run.source_data_version != event.data_version:
        raise ValidationError(
            f"Result run is stale: run data version v{result_run.source_data_version} does not match current event version v{event.data_version}. Recalculate before publishing."
        )

    now = timezone.now()
    if now < event.judging_closes_at:
        raise ValidationError(
            f"Cannot publish results before judging has closed ({event.judging_closes_at.isoformat()})."
        )

    if waivers is None:
        waivers = []

    with transaction.atomic():
        pub_count = Publication.objects.filter(event=event).count()
        current_active = Publication.objects.filter(id=event.active_publication_id).first() if event.active_publication_id else None

        pub = Publication.objects.create(
            event=event,
            result_run=result_run,
            number=pub_count + 1,
            published_by=actor_user,
            public_note_md=public_note_md,
            waivers_json=waivers,
            supersedes=current_active,
        )

        event.active_publication_id = pub.id
        if not event.judging_frozen_at:
            event.judging_frozen_at = now
        event.save(update_fields=["active_publication_id", "judging_frozen_at", "updated_at"])

        AuditEvent.objects.create(
            event=event,
            actor_user=actor_user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="RESULTS_PUBLISHED",
            entity_type="Publication",
            entity_id=pub.id,
            after_json={
                "publication_number": pub.number,
                "result_run_id": str(result_run.id),
                "data_version": event.data_version,
            },
            reason="Organiser published official results",
        )

    return pub
