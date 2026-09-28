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
from apps.judging.models import (
    JudgeAssignment,
    JudgeTrackPermission,
    Review,
    ReviewScore,
    Rubric,
)
from apps.judging.services import (
    get_event_judging_progress,
    save_draft_review,
    submit_review,
)


def judge_workspace_page(request, slug=None):
    if not request.user.is_authenticated:
        return redirect("/login/?next=/judging/")

    event = get_object_or_404(Event, slug=slug) if slug else get_default_event()
    if not event:
        return HttpResponse("No event found.", status=404)

    membership = EventMembership.objects.filter(
        event=event, user=request.user, status=EventMembership.Status.ACTIVE
    ).first()

    if not membership or (membership.role != EventMembership.Role.JUDGE and not request.user.is_staff):
        return render(request, "portal/forbidden.html", {"message": "Active Judge privileges required."}, status=403)

    assignments = (
        JudgeAssignment.objects.filter(event=event, judge_membership=membership, status=JudgeAssignment.Status.ACTIVE)
        .select_related("project__submitted_revision__track", "project__team", "rubric")
        .prefetch_related("rubric__criteria", "review__scores__criterion")
    )

    now = timezone.now()
    is_judging_open = event.judging_opens_at <= now < event.judging_closes_at

    context = {
        "event": event,
        "membership": membership,
        "assignments": assignments,
        "is_judging_open": is_judging_open,
        "now": now,
    }
    return render(request, "portal/judge_workspace.html", context)


def organiser_judging_page(request, slug):
    if not request.user.is_authenticated:
        return redirect(f"/login/?next=/events/{slug}/judging/manage/")

    event = get_object_or_404(Event, slug=slug)
    membership = EventMembership.objects.filter(
        event=event, user=request.user, status=EventMembership.Status.ACTIVE
    ).first()

    if not membership or (membership.role != EventMembership.Role.ORGANISER and not request.user.is_staff):
        return render(request, "portal/forbidden.html", {"message": "Organiser privileges required."}, status=403)

    progress = get_event_judging_progress(event)

    context = {
        "event": event,
        "progress": progress,
        "now": timezone.now(),
    }
    return render(request, "portal/organiser_judging.html", context)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_judge_assignments(request, slug):
    event = get_object_or_404(Event, slug=slug)
    membership = EventMembership.objects.filter(
        event=event, user=request.user, status=EventMembership.Status.ACTIVE
    ).first()

    if not membership or (membership.role != EventMembership.Role.JUDGE and not request.user.is_staff):
        return Response({"detail": "Judge privileges required for this event."}, status=status.HTTP_403_FORBIDDEN)

    assignments = (
        JudgeAssignment.objects.filter(event=event, judge_membership=membership, status=JudgeAssignment.Status.ACTIVE)
        .select_related("project__submitted_revision__track", "rubric")
        .prefetch_related("rubric__criteria", "review__scores__criterion")
    )

    data = []
    for a in assignments:
        rev = a.project_revision
        rev_obj = getattr(a, "review", None)
        scores = {}
        if rev_obj:
            for s in rev_obj.scores.all():
                scores[str(s.criterion_id)] = s.value

        data.append({
            "assignment_id": str(a.id),
            "project_id": str(a.project_id),
            "project_title": rev.title if rev else "",
            "project_summary": rev.summary if rev else "",
            "repo_url": rev.repo_url if rev else None,
            "demo_url": rev.demo_url if rev else None,
            "track_name": rev.track.name if (rev and rev.track) else "",
            "review_status": rev_obj.status if rev_obj else "UNSTARTED",
            "scores": scores,
            "comment": rev_obj.comment if rev_obj else "",
        })

    return Response({"assignments": data})


@api_view(["GET", "PUT", "POST"])
@permission_classes([IsAuthenticated])
def api_assignment_review(request, pk):
    assignment = get_object_or_404(JudgeAssignment, id=pk)

    if assignment.judge_membership.user_id != request.user.id and not request.user.is_staff:
        return Response({"detail": "Confidential: You can only view or edit your own reviews."}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "GET":
        rev_obj = getattr(assignment, "review", None)
        scores = {}
        if rev_obj:
            for s in rev_obj.scores.select_related("criterion"):
                scores[str(s.criterion_id)] = {
                    "key": s.criterion.key,
                    "label": s.criterion.label,
                    "value": s.value,
                }

        criteria = [
            {"id": str(c.id), "key": c.key, "label": c.label, "weight": str(c.weight), "description_md": c.description_md}
            for c in assignment.rubric.criteria.all()
        ]

        return Response({
            "assignment_id": str(assignment.id),
            "rubric_name": assignment.rubric.name,
            "criteria": criteria,
            "review_status": rev_obj.status if rev_obj else "UNSTARTED",
            "comment": rev_obj.comment if rev_obj else "",
            "scores": scores,
        })

    scores_dict = request.data.get("scores", {})
    comment = request.data.get("comment", "")

    try:
        review = save_draft_review(request.user, assignment, scores_dict, comment)
        return Response({
            "status": "SAVED",
            "review_id": str(review.id),
            "review_status": review.status,
            "version": review.version,
        })
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_submit_review(request, pk):
    assignment = get_object_or_404(JudgeAssignment, id=pk)

    if assignment.judge_membership.user_id != request.user.id and not request.user.is_staff:
        return Response({"detail": "Confidential: You can only submit your own evaluations."}, status=status.HTTP_403_FORBIDDEN)

    scores_dict = request.data.get("scores", {})
    comment = request.data.get("comment", "")

    try:
        review = submit_review(request.user, assignment, scores_dict, comment)
        return Response({
            "status": "SUBMITTED",
            "review_id": str(review.id),
            "review_status": review.status,
            "submitted_at": review.submitted_at.isoformat(),
        })
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_judge_scores(request, slug, judge_user_id):
    """
    Confidentiality Check:
    A judge can ONLY see their own scores.
    An organiser can see any judge's scores for their event.
    Peers and participants are strictly rejected with 403 Forbidden.
    """
    event = get_object_or_404(Event, slug=slug)

    caller_membership = EventMembership.objects.filter(
        event=event, user=request.user, status=EventMembership.Status.ACTIVE
    ).first()

    is_owner_judge = str(request.user.id) == str(judge_user_id)
    is_organiser = bool(caller_membership and caller_membership.role == EventMembership.Role.ORGANISER) or request.user.is_staff

    if not is_owner_judge and not is_organiser:
        return Response(
            {"detail": "Forbidden: Judge evaluations are confidential."},
            status=status.HTTP_403_FORBIDDEN,
        )

    reviews = (
        Review.objects.filter(
            assignment__event=event,
            assignment__judge_membership__user_id=judge_user_id,
        )
        .select_related("assignment__project__submitted_revision")
        .prefetch_related("scores__criterion")
    )

    data = []
    for r in reviews:
        rev_scores = {s.criterion.key: s.value for s in r.scores.all()}
        data.append({
            "review_id": str(r.id),
            "project_id": str(r.assignment.project_id),
            "project_title": r.assignment.project_revision.title if r.assignment.project_revision else "",
            "status": r.status,
            "scores": rev_scores,
            "comment": r.comment,
            "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
        })

    return Response({
        "event": event.slug,
        "judge_user_id": str(judge_user_id),
        "reviews": data,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_organiser_progress(request, slug):
    """Private event judging coverage dashboard for organisers."""
    event = get_object_or_404(Event, slug=slug)

    membership = EventMembership.objects.filter(
        event=event, user=request.user, status=EventMembership.Status.ACTIVE
    ).first()

    if not membership or (membership.role != EventMembership.Role.ORGANISER and not request.user.is_staff):
        return Response({"detail": "Organiser privileges required."}, status=status.HTTP_403_FORBIDDEN)

    progress = get_event_judging_progress(event)
    return Response(progress)
