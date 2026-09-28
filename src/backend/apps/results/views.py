import csv
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.events.models import Event, EventMembership
from apps.events.views import get_default_event
from apps.results.models import CommunityResultRow, Publication, ResultRow, ResultRun
from apps.results.services import calculate_result_run, publish_results


def sanitize_csv_cell(val: str) -> str:
    """Escapes formula injection in CSV cells."""
    if val is None:
        return ""
    s = str(val)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{s}"
    return s


def results_page(request, slug=None):
    event = get_object_or_404(Event, slug=slug) if slug else get_default_event()
    if not event:
        return HttpResponse("No event configured.", status=404)

    publication = None
    if event.active_publication_id:
        publication = (
            Publication.objects.filter(id=event.active_publication_id)
            .select_related("result_run")
            .first()
        )

    rows = []
    if publication:
        rows = (
            ResultRow.objects.filter(result_run=publication.result_run)
            .select_related("project__submitted_revision__track", "project__team")
            .order_by("cohort_key", "rank", "id")
        )

    context = {
        "event": event,
        "publication": publication,
        "rows": rows,
        "now": timezone.now(),
    }
    return render(request, "portal/results.html", context)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_preview_results(request, slug):
    """Private result run calculation preview for organisers."""
    event = get_object_or_404(Event, slug=slug)
    algorithm = request.data.get("algorithm", event.ranking_method)
    lambda_val = request.data.get("lambda", event.normalisation_lambda)

    try:
        run = calculate_result_run(request.user, event, algorithm=algorithm, lambda_val=lambda_val)
        rows = (
            ResultRow.objects.filter(result_run=run)
            .select_related("project__submitted_revision")
            .order_by("cohort_key", "rank", "id")
        )

        rows_data = []
        for r in rows:
            rev = r.project.submitted_revision
            rows_data.append({
                "project_id": str(r.project_id),
                "title": rev.title if rev else "",
                "rank": r.rank,
                "ranking_value": r.ranking_value,
                "raw_mean": r.raw_mean,
                "adjusted_value": r.adjusted_value,
                "completed_reviews": r.completed_review_count,
                "flags": r.flags_json,
                "eligible": r.eligible,
            })

        comm_rows = (
            CommunityResultRow.objects.filter(result_run=run)
            .select_related("project__submitted_revision")
            .order_by("rank", "id")
        )
        comm_data = []
        for cr in comm_rows:
            rev = cr.project.submitted_revision
            comm_data.append({
                "project_id": str(cr.project_id),
                "title": rev.title if rev else "",
                "counted_votes": cr.counted_votes,
                "excluded_votes": cr.excluded_votes,
                "rank": cr.rank,
            })

        return Response({
            "result_run_id": str(run.id),
            "algorithm": run.algorithm_version,
            "source_data_version": run.source_data_version,
            "input_sha256": run.input_sha256,
            "output_sha256": run.output_sha256,
            "diagnostics": run.diagnostics_json,
            "rows": rows_data,
            "community_rows": comm_data,
        })
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_publish_results(request, slug):
    """Formal publication of a result run."""
    event = get_object_or_404(Event, slug=slug)
    run_id = request.data.get("result_run_id")
    if not run_id:
        return Response({"detail": "Missing result_run_id."}, status=status.HTTP_400_BAD_REQUEST)

    result_run = get_object_or_404(ResultRun, id=run_id, event=event)
    public_note = request.data.get("public_note_md", "")
    waivers = request.data.get("waivers", [])

    try:
        pub = publish_results(request.user, event, result_run, public_note, waivers)
        return Response({
            "status": "PUBLISHED",
            "publication_id": str(pub.id),
            "publication_number": pub.number,
            "published_at": pub.published_at.isoformat(),
        })
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
def api_get_results(request, slug):
    """Public official results endpoint. Serves active publication only."""
    event = get_object_or_404(Event, slug=slug)

    if not event.active_publication_id:
        return Response(
            {"detail": "Results have not been officially published yet."},
            status=status.HTTP_404_NOT_FOUND,
        )

    pub = Publication.objects.filter(id=event.active_publication_id).select_related("result_run").first()
    if not pub:
        return Response({"detail": "Active publication record not found."}, status=status.HTTP_404_NOT_FOUND)

    rows = (
        ResultRow.objects.filter(result_run=pub.result_run)
        .select_related("project__submitted_revision__track", "project__team")
        .order_by("cohort_key", "rank", "id")
    )

    data = []
    for r in rows:
        rev = r.project.submitted_revision
        data.append({
            "rank": r.rank,
            "project_id": str(r.project_id),
            "project_title": rev.title if rev else "",
            "team_name": r.project.team.name,
            "track": rev.track.name if (rev and rev.track) else "",
            "raw_mean": r.raw_mean,
            "adjusted_value": r.adjusted_value,
            "ranking_value": r.ranking_value,
            "reviews_count": r.completed_review_count,
        })

    comm_rows = (
        CommunityResultRow.objects.filter(result_run=pub.result_run)
        .select_related("project__submitted_revision")
        .order_by("rank", "id")
    )
    comm_data = []
    for cr in comm_rows:
        rev = cr.project.submitted_revision
        comm_data.append({
            "project_id": str(cr.project_id),
            "project_title": rev.title if rev else "",
            "counted_votes": cr.counted_votes,
            "rank": cr.rank,
        })

    return Response({
        "event_slug": event.slug,
        "publication_number": pub.number,
        "published_at": pub.published_at.isoformat(),
        "algorithm": pub.result_run.algorithm_version,
        "public_note": pub.public_note_md,
        "results": data,
        "community_results": comm_data,
    })


def results_csv_export(request, slug=None):
    """
    CSV export of results with formula injection escaping.
    Organiser-only endpoint.
    """
    if not request.user.is_authenticated:
        return HttpResponse("Unauthorized", status=401)

    event = get_object_or_404(Event, slug=slug) if slug else get_default_event()
    if not event:
        return HttpResponse("No event found", status=404)

    membership = EventMembership.objects.filter(
        event=event, user=request.user, status=EventMembership.Status.ACTIVE
    ).first()

    if not membership or (membership.role != EventMembership.Role.ORGANISER and not request.user.is_staff):
        return HttpResponse("Forbidden: Organiser access required.", status=403)

    # Use active publication or latest result run
    run = None
    if event.active_publication_id:
        pub = Publication.objects.filter(id=event.active_publication_id).first()
        if pub:
            run = pub.result_run
    if not run:
        run = ResultRun.objects.filter(event=event).order_by("-created_at").first()

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}-results.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Rank", "Project ID", "Title", "Team", "Track", "Eligible",
        "Completed Reviews", "Raw Mean", "Adjusted Score", "Ranking Score", "Flags"
    ])

    if run:
        rows = (
            ResultRow.objects.filter(result_run=run)
            .select_related("project__submitted_revision__track", "project__team")
            .order_by("cohort_key", "rank", "id")
        )
        for r in rows:
            rev = r.project.submitted_revision
            writer.writerow([
                sanitize_csv_cell(r.rank if r.rank is not None else "Unranked"),
                sanitize_csv_cell(str(r.project_id)),
                sanitize_csv_cell(rev.title if rev else "Untitled"),
                sanitize_csv_cell(r.project.team.name if r.project.team else ""),
                sanitize_csv_cell(rev.track.name if (rev and rev.track) else ""),
                sanitize_csv_cell("Yes" if r.eligible else "No"),
                sanitize_csv_cell(r.completed_review_count),
                sanitize_csv_cell(r.raw_mean if r.raw_mean is not None else ""),
                sanitize_csv_cell(r.adjusted_value if r.adjusted_value is not None else ""),
                sanitize_csv_cell(r.ranking_value if r.ranking_value is not None else ""),
                sanitize_csv_cell("; ".join(r.flags_json)),
            ])

    return response
