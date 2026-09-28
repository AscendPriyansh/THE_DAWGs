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
from apps.submissions.models import Project, ProjectRevision
from apps.submissions.services import (
    StaleSaveConflict,
    save_draft_submission,
    submit_project,
)
from apps.teams.models import Team, TeamMember
from apps.teams.services import (
    accept_team_invitation,
    create_team,
    create_team_invitation,
)


def participant_workspace_page(request, slug=None):
    if not request.user.is_authenticated:
        return redirect("/login/")

    event = get_object_or_404(Event, slug=slug) if slug else get_default_event()
    if not event:
        return HttpResponse("No event configured.", status=404)

    # Ensure user has participant membership
    membership, _ = EventMembership.objects.get_or_create(
        event=event,
        user=request.user,
        defaults={"role": EventMembership.Role.PARTICIPANT},
    )

    team_member = TeamMember.objects.filter(event=event, user=request.user).select_related("team").first()
    team = team_member.team if team_member else None

    project = None
    draft = None
    submitted = None
    has_unsubmitted_changes = False

    if team:
        project = Project.objects.filter(event=event, team=team).first()
        if project:
            draft = project.draft_revision
            submitted = project.submitted_revision
            if submitted and draft and submitted.id != draft.id:
                has_unsubmitted_changes = True

    is_captain = bool(team and team.captain_user_id == request.user.id)
    tracks = event.tracks.all().order_by("display_order")

    context = {
        "event": event,
        "team": team,
        "is_captain": is_captain,
        "project": project,
        "draft": draft,
        "submitted": submitted,
        "has_unsubmitted_changes": has_unsubmitted_changes,
        "tracks": tracks,
        "is_open": event.is_submission_open(),
        "now": timezone.now(),
    }
    return render(request, "portal/workspace.html", context)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_save_draft(request):
    event_id = request.data.get("event_id")
    event = get_object_or_404(Event, id=event_id) if event_id else get_default_event()

    team_member = TeamMember.objects.filter(event=event, user=request.user).first()
    if not team_member:
        return Response(
            {"detail": "You must create or join a team before editing a submission."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    expected_version = request.data.get("version")
    project_id = request.data.get("project_id")

    try:
        project, rev = save_draft_submission(
            user=request.user,
            event=event,
            team=team_member.team,
            data=request.data,
            expected_version=int(expected_version) if expected_version is not None else None,
            project_id=project_id,
        )
        return Response({
            "status": "SAVED",
            "project_id": str(project.id),
            "version": project.version,
            "revision_number": rev.number,
            "updated_at": rev.created_at.isoformat(),
        })
    except StaleSaveConflict as e:
        return Response({
            "error": "STALE_SAVE_CONFLICT",
            "detail": "A teammate updated this submission. Your local changes were preserved so you can reconcile.",
            "server_version": e.current_version,
            "server_title": e.current_revision.title if e.current_revision else "",
            "server_summary": e.current_revision.summary if e.current_revision else "",
        }, status=status.HTTP_409_CONFLICT)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_submit_project(request):
    project_id = request.data.get("project_id")
    event_id = request.data.get("event_id")
    event = get_object_or_404(Event, id=event_id) if event_id else get_default_event()

    try:
        project, receipt = submit_project(request.user, event, project_id)
        return Response({
            "status": "SUBMITTED",
            "receipt": receipt,
        }, status=status.HTTP_200_OK)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_create_team(request):
    event_id = request.data.get("event_id")
    name = request.data.get("name", "")
    event = get_object_or_404(Event, id=event_id) if event_id else get_default_event()

    try:
        team = create_team(request.user, event, name)
        return Response({
            "status": "CREATED",
            "team_id": str(team.id),
            "name": team.name,
        }, status=status.HTTP_201_CREATED)
    except (ValidationError, PermissionDenied) as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_create_team_invitation(request):
    team_id = request.data.get("team_id")
    invited_email = request.data.get("invited_email")
    team = get_object_or_404(Team, id=team_id)

    try:
        inv, token = create_team_invitation(request.user, team, invited_email)
        return Response({
            "invite_token": token,
            "invite_url": f"/join/team/{token}/",
            "expires_at": inv.expires_at.isoformat(),
        }, status=status.HTTP_201_CREATED)
    except (ValidationError, PermissionDenied) as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


def accept_team_invite_page(request, token):
    if not request.user.is_authenticated:
        return redirect(f"/login/?next=/join/team/{token}/")

    error = None
    success = None

    if request.method == "POST":
        try:
            member = accept_team_invitation(request.user, token)
            return redirect("/workspace")
        except (ValidationError, PermissionDenied) as e:
            error = str(e)

    return render(request, "portal/accept_invite.html", {"token": token, "error": error})
