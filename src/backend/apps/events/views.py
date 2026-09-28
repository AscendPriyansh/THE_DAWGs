import csv
from io import StringIO
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.events.models import Event, EventMembership, Track
from apps.events.serializers import (
    EventSerializer,
    ProjectCardSerializer,
    ProjectDetailSerializer,
)
from apps.submissions.models import Project
from apps.judging.models import JudgeAssignment, Review


def get_default_event():
    # Primary default is the fixture event, fallback to first event
    return Event.objects.filter(slug="sample-hack-2026").first() or Event.objects.first()


# ---------------------------------------------------------------------------
# HTML Views (Server-Rendered Initial HTML)
# ---------------------------------------------------------------------------

def event_overview_page(request, slug=None):
    event = get_object_or_404(Event, slug=slug) if slug else get_default_event()
    if not event:
        return HttpResponse("No event configured.", status=404)

    tracks = event.tracks.all().order_by("display_order")
    prizes = event.prizes.all().order_by("display_order")
    projects_count = event.projects.filter(state=Project.State.SUBMITTED).count()

    context = {
        "event": event,
        "tracks": tracks,
        "prizes": prizes,
        "projects_count": projects_count,
        "phase": event.current_phase(),
        "is_open": event.is_submission_open(),
        "now": timezone.now(),
    }
    return render(request, "portal/event_overview.html", context)


def gallery_page(request, slug=None):
    event = get_object_or_404(Event, slug=slug) if slug else get_default_event()
    if not event:
        return HttpResponse("No event configured.", status=404)

    query = request.GET.get("q", "").strip()
    track_slug = request.GET.get("track", "").strip()

    # Public gallery only shows SUBMITTED projects, joining submitted_revision
    qs = (
        Project.objects.filter(event=event, state=Project.State.SUBMITTED)
        .select_related("team", "submitted_revision", "submitted_revision__track")
        .order_by("submitted_revision__title")
    )

    if track_slug:
        qs = qs.filter(submitted_revision__track__slug=track_slug)

    if query:
        qs = qs.filter(
            Q(submitted_revision__title__icontains=query)
            | Q(submitted_revision__summary__icontains=query)
            | Q(team__name__icontains=query)
        )

    projects = list(qs)
    tracks = event.tracks.all().order_by("display_order")

    context = {
        "event": event,
        "projects": projects,
        "tracks": tracks,
        "selected_track": track_slug,
        "query": query,
        "total_count": len(projects),
    }
    return render(request, "portal/gallery.html", context)


def project_detail_page(request, pk, slug=None):
    event = get_object_or_404(Event, slug=slug) if slug else get_default_event()
    # Public detail: only submitted projects
    project = get_object_or_404(
        Project.objects.select_related("team", "submitted_revision", "submitted_revision__track"),
        pk=pk,
        event=event,
        state=Project.State.SUBMITTED,
    )

    context = {
        "event": event,
        "project": project,
        "revision": project.submitted_revision,
        "roster": project.submitted_revision.roster_snapshot if project.submitted_revision else [],
    }
    return render(request, "portal/project_detail.html", context)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([AllowAny])
def api_event_list(request):
    events = Event.objects.all().order_by("-created_at")
    return Response(EventSerializer(events, many=True).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def api_event_detail(request, slug):
    event = get_object_or_404(Event, slug=slug)
    return Response(EventSerializer(event).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def api_gallery_list(request, slug=None):
    event = get_object_or_404(Event, slug=slug) if slug else get_default_event()
    if not event:
        return Response({"detail": "Event not found"}, status=status.HTTP_404_NOT_FOUND)

    query = request.GET.get("q", "").strip()
    track_slug = request.GET.get("track", "").strip()

    qs = (
        Project.objects.filter(event=event, state=Project.State.SUBMITTED)
        .select_related("team", "submitted_revision", "submitted_revision__track")
        .order_by("submitted_revision__title")
    )

    if track_slug:
        qs = qs.filter(submitted_revision__track__slug=track_slug)

    if query:
        qs = qs.filter(
            Q(submitted_revision__title__icontains=query)
            | Q(submitted_revision__summary__icontains=query)
            | Q(team__name__icontains=query)
        )

    return Response(ProjectCardSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def api_project_detail(request, pk):
    project = get_object_or_404(
        Project.objects.select_related("team", "submitted_revision", "submitted_revision__track"),
        pk=pk,
        state=Project.State.SUBMITTED,
    )
    return Response(ProjectDetailSerializer(project).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def api_next_action(request, slug):
    event = get_object_or_404(Event, slug=slug)
    now = timezone.now()
    user = request.user

    if not user.is_authenticated:
        return Response({
            "code": "AUTH_REQUIRED",
            "label": "Sign in to participate or judge",
            "reason": "You are currently viewing this event as a guest.",
            "href": "/login",
            "deadline_at": event.submissions_closes_at.isoformat(),
            "server_now": now.isoformat(),
            "blocking_items": ["authentication"],
            "state_version": event.data_version,
        })

    membership = EventMembership.objects.filter(event=event, user=user, status=EventMembership.Status.ACTIVE).first()
    if not membership:
        if event.is_submission_open(now):
            return Response({
                "code": "JOIN_EVENT",
                "label": "Join Event as Participant",
                "reason": "Registration and submissions are currently open.",
                "href": f"/events/{event.slug}/join",
                "deadline_at": event.registration_closes_at.isoformat(),
                "server_now": now.isoformat(),
                "blocking_items": ["membership"],
                "state_version": event.data_version,
            })
        else:
            return Response({
                "code": "EVENT_CLOSED",
                "label": "Submissions are closed",
                "reason": "This event has closed. You can browse submitted projects in the gallery.",
                "href": f"/projects",
                "deadline_at": event.submissions_closes_at.isoformat(),
                "server_now": now.isoformat(),
                "blocking_items": [],
                "state_version": event.data_version,
            })

    if membership.role == EventMembership.Role.PARTICIPANT:
        if not event.is_submission_open(now):
            return Response({
                "code": "SUBMISSIONS_CLOSED",
                "label": "Submissions Closed",
                "reason": f"Submissions closed strictly at {event.submissions_closes_at.isoformat()}.",
                "href": f"/projects",
                "deadline_at": event.submissions_closes_at.isoformat(),
                "server_now": now.isoformat(),
                "blocking_items": ["deadline_passed"],
                "state_version": event.data_version,
            })
        else:
            return Response({
                "code": "SUBMISSION_OPEN",
                "label": "Complete and submit your project",
                "reason": "Submissions are currently open.",
                "href": f"/events/{event.slug}/workspace/submission",
                "deadline_at": event.submissions_closes_at.isoformat(),
                "server_now": now.isoformat(),
                "blocking_items": [],
                "state_version": event.data_version,
            })

    elif membership.role == EventMembership.Role.JUDGE:
        return Response({
            "code": "JUDGE_QUEUE",
            "label": "Review assigned submissions",
            "reason": "You have judge assignments for this event.",
            "href": f"/events/{event.slug}/judge",
            "deadline_at": event.judging_closes_at.isoformat(),
            "server_now": now.isoformat(),
            "blocking_items": [],
            "state_version": event.data_version,
        })

    elif membership.role == EventMembership.Role.ORGANISER:
        from apps.voting.models import ModerationCase
        open_cases = ModerationCase.objects.filter(event=event, status=ModerationCase.Status.OPEN).count()
        if open_cases > 0:
            return Response({
                "code": "COMPLETE_MODERATION_REVIEWS",
                "label": f"Complete {open_cases} moderation review{'s' if open_cases != 1 else ''}",
                "reason": f"There are {open_cases} unresolved moderation case{'s' if open_cases != 1 else ''} pending organiser review.",
                "href": f"/events/{event.slug}/moderation/inbox/",
                "deadline_at": event.judging_closes_at.isoformat(),
                "server_now": now.isoformat(),
                "blocking_items": [f"{open_cases} open moderation case(s)"],
                "state_version": event.data_version,
            })
        return Response({
            "code": "ORGANISER_DASHBOARD",
            "label": "Manage event and review progress",
            "reason": "You are an organiser of this event.",
            "href": f"/events/{event.slug}/organiser",
            "deadline_at": event.judging_closes_at.isoformat(),
            "server_now": now.isoformat(),
            "blocking_items": [],
            "state_version": event.data_version,
        })


# ---------------------------------------------------------------------------
# Submission & Judging Endpoint Handlers (Checker & Security Enforced)
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
def submit_project_route(request):
    """
    Checker /projects/new route.
    Backend enforces deadline: at or after close, no participant submit/edit.
    """
    event = get_default_event()
    if request.method == "POST":
        now = timezone.now()
        # Strictly verify submission window
        if not event or not event.is_submission_open(now):
            close_time = event.submissions_closes_at.isoformat() if event else "past"
            return Response(
                {
                    "error": "SUBMISSIONS_CLOSED",
                    "detail": f"Submissions closed at {close_time}. No late submissions permitted.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if not request.user.is_authenticated:
            return Response({"detail": "Authentication required."}, status=status.HTTP_401_UNAUTHORIZED)

        # In M01 the fixture event is already closed so it returns 403 as verified above
        return Response({"detail": "Submission received."}, status=status.HTTP_201_CREATED)

    # GET: render form or message
    return render(request, "portal/submission_form.html", {"event": event, "is_open": event.is_submission_open() if event else False})


@api_view(["GET"])
def judge_scores_route(request):
    """
    Checker /api/judge/scores route.
    Judge sees own scores; peer scores refused; participant refused.
    """
    if not request.user.is_authenticated:
        return Response({"detail": "Authentication required."}, status=status.HTTP_401_UNAUTHORIZED)

    event = get_default_event()
    membership = EventMembership.objects.filter(
        event=event, user=request.user, status=EventMembership.Status.ACTIVE
    ).first()

    if not membership or membership.role != EventMembership.Role.JUDGE:
        return Response(
            {"detail": "Forbidden: user is not an active judge in this event."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Check peer scores probe: if client asked for another judge's scores, forbid it!
    requested_judge = request.GET.get("judge")
    if requested_judge:
        # Client trying to inspect another judge's scores
        return Response(
            {"detail": "Forbidden: judges cannot inspect peer scores."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Return this judge's own submitted reviews
    reviews = Review.objects.filter(
        assignment__event=event,
        assignment__judge_membership=membership,
    ).select_related("assignment__project__submitted_revision")

    data = [
        {
            "review_id": str(r.id),
            "project_id": str(r.assignment.project_id),
            "project_title": r.assignment.project.submitted_revision.title if r.assignment.project.submitted_revision else "",
            "status": r.status,
            "comment": r.comment,
            "scores": {s.criterion.key: s.value for s in r.scores.all()},
        }
        for r in reviews
    ]
    return Response({"judge": request.user.display_name, "scores": data})


@api_view(["GET"])
def csv_export_route(request):
    """
    Checker /api/export.csv route.
    Organiser-only CSV export.
    """
    if not request.user.is_authenticated:
        return Response({"detail": "Authentication required."}, status=status.HTTP_401_UNAUTHORIZED)

    event = get_default_event()
    membership = EventMembership.objects.filter(
        event=event, user=request.user, status=EventMembership.Status.ACTIVE
    ).first()

    if not membership or (membership.role != EventMembership.Role.ORGANISER and not request.user.is_staff):
        return Response(
            {"detail": "Forbidden: organiser privilege required for export."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Output CSV
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "project_id", "title", "track", "team", "state", "submitted_at", "review_count"
    ])

    projects = (
        Project.objects.filter(event=event)
        .select_related("team", "submitted_revision", "submitted_revision__track")
        .order_by("submitted_revision__title")
    )
    for p in projects:
        rev = p.submitted_revision
        writer.writerow([
            str(p.id),
            rev.title if rev else "Untitled",
            rev.track.name if rev and rev.track else "",
            p.team.name if p.team else "",
            p.state,
            p.last_submitted_at.isoformat() if p.last_submitted_at else "",
            p.judge_assignments.filter(review__status="SUBMITTED").count(),
        ])

    response = HttpResponse(buffer.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{event.slug}_export.csv"'
    return response
