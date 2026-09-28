import json
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.rate_limit import RateLimitExceeded
from apps.events.models import Event, EventMembership
from apps.submissions.models import Project
from apps.voting.models import AbuseSignal, Comment, ModerationCase
from apps.voting.services import (
    create_comment,
    delete_comment,
    dismiss_moderation_case,
    edit_comment,
    moderate_comment,
    report_moderation_case,
    resolve_moderation_case,
)


def get_client_ip(request) -> str:
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


@api_view(["GET", "POST"])
def api_project_comments(request, slug, project_id):
    """
    GET: Lists visible comments for a project (public view). Organisers see all.
    POST: Creates a comment on a project. Requires authenticated user.
    """
    event = get_object_or_404(Event, slug=slug)
    project = get_object_or_404(Project, id=project_id, event=event)

    if request.method == "GET":
        is_organiser = False
        if request.user.is_authenticated:
            is_organiser = EventMembership.objects.filter(
                event=event, user=request.user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
            ).exists() or request.user.is_staff

        comments_qs = Comment.objects.filter(event=event, project=project).select_related("author_user").order_by("created_at")

        data = []
        for c in comments_qs:
            # If author or organiser, show status
            is_author = request.user.is_authenticated and c.author_user_id == request.user.id
            if c.state == Comment.State.VISIBLE:
                data.append({
                    "id": str(c.id),
                    "author_name": c.author_user.display_name,
                    "author_id": str(c.author_user_id),
                    "body": c.body,
                    "state": c.state,
                    "version": c.version,
                    "created_at": c.created_at.isoformat(),
                    "is_author": is_author,
                })
            elif is_organiser or is_author:
                data.append({
                    "id": str(c.id),
                    "author_name": c.author_user.display_name,
                    "author_id": str(c.author_user_id),
                    "body": c.body if c.state != Comment.State.DELETED else "[Comment deleted by author]",
                    "state": c.state,
                    "version": c.version,
                    "created_at": c.created_at.isoformat(),
                    "is_author": is_author,
                })

        return Response({"comments": data})

    elif request.method == "POST":
        if not request.user.is_authenticated:
            return Response({"detail": "Authentication required to post comments."}, status=status.HTTP_401_UNAUTHORIZED)

        body = request.data.get("body", "")
        ip_addr = get_client_ip(request)

        try:
            comment = create_comment(event, project, request.user, body, ip_address=ip_addr)
            return Response(
                {
                    "id": str(comment.id),
                    "author_name": request.user.display_name,
                    "body": comment.body,
                    "state": comment.state,
                    "version": comment.version,
                    "created_at": comment.created_at.isoformat(),
                },
                status=status.HTTP_201_CREATED,
            )
        except RateLimitExceeded as e:
            return Response({"detail": str(e), "retry_after": e.retry_after_seconds}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except PermissionDenied as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def api_comment_detail(request, slug, comment_id):
    """
    PATCH: Edits comment body (author only, inside comment window).
    DELETE: Deletes comment (author or organiser).
    """
    event = get_object_or_404(Event, slug=slug)
    comment = get_object_or_404(Comment, id=comment_id, event=event)

    if request.method == "PATCH":
        new_body = request.data.get("body", "")
        try:
            updated = edit_comment(comment, request.user, new_body)
            return Response({
                "id": str(updated.id),
                "body": updated.body,
                "version": updated.version,
                "state": updated.state,
                "updated_at": updated.updated_at.isoformat(),
            })
        except PermissionDenied as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    elif request.method == "DELETE":
        try:
            deleted = delete_comment(comment, request.user)
            return Response({"status": "DELETED", "id": str(deleted.id)})
        except PermissionDenied as e:
            return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_moderate_comment(request, slug, comment_id):
    """
    Organiser hides or restores comment with reason.
    """
    event = get_object_or_404(Event, slug=slug)
    comment = get_object_or_404(Comment, id=comment_id, event=event)

    new_state = request.data.get("state")
    reason = request.data.get("reason", "")

    try:
        mod = moderate_comment(comment, request.user, new_state, reason)
        return Response({
            "id": str(mod.id),
            "state": mod.state,
            "updated_at": mod.updated_at.isoformat(),
        })
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
def api_report_moderation(request, slug):
    """
    Reports content/abuse for organiser review.
    """
    event = get_object_or_404(Event, slug=slug)
    target_type = request.data.get("target_type")
    target_id = request.data.get("target_id")
    reason_code = request.data.get("reason_code", "OTHER")

    reporter_user = request.user if request.user.is_authenticated else None
    ip_addr = get_client_ip(request)

    try:
        case = report_moderation_case(
            event=event,
            target_type=target_type,
            target_id=target_id,
            reason_code=reason_code,
            reporter_user=reporter_user,
            ip_address=ip_addr,
        )
        return Response(
            {
                "case_id": str(case.id),
                "status": case.status,
                "created_at": case.created_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )
    except RateLimitExceeded as e:
        return Response({"detail": str(e), "retry_after": e.retry_after_seconds}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_moderation_inbox(request, slug):
    """
    Organiser moderation inbox listing open cases and abuse signals.
    """
    event = get_object_or_404(Event, slug=slug)
    membership = EventMembership.objects.filter(
        event=event, user=request.user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
    ).first()
    if not membership and not request.user.is_staff:
        return Response({"detail": "Organiser access required."}, status=status.HTTP_403_FORBIDDEN)

    cases = ModerationCase.objects.filter(event=event, status=ModerationCase.Status.OPEN).order_by("-created_at")
    signals = AbuseSignal.objects.filter(event=event, state=AbuseSignal.State.OPEN).order_by("-created_at")

    cases_data = [
        {
            "id": str(c.id),
            "target_type": c.target_type,
            "target_id": str(c.target_id),
            "reason_code": c.reason_code,
            "reporter_user_id": str(c.reporter_user_id) if c.reporter_user_id else None,
            "status": c.status,
            "created_at": c.created_at.isoformat(),
        }
        for c in cases
    ]

    signals_data = [
        {
            "id": str(s.id),
            "kind": s.kind,
            "subject_id": s.subject_id,
            "network_key": s.network_key,
            "details": s.details_json,
            "state": s.state,
            "created_at": s.created_at.isoformat(),
        }
        for s in signals
    ]

    return Response({
        "open_cases_count": len(cases_data),
        "open_signals_count": len(signals_data),
        "cases": cases_data,
        "signals": signals_data,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_resolve_moderation_case(request, slug, case_id):
    """
    Organiser resolves a moderation case with a decision and reason.
    """
    event = get_object_or_404(Event, slug=slug)
    case = get_object_or_404(ModerationCase, id=case_id, event=event)

    decision = request.data.get("decision")
    reason = request.data.get("reason", "")
    affected_ids = request.data.get("affected_ids", None)

    try:
        resolved = resolve_moderation_case(case, request.user, decision, reason, affected_ids=affected_ids)
        return Response({
            "case_id": str(resolved.id),
            "status": resolved.status,
            "decision": resolved.decision,
            "decided_at": resolved.decided_at.isoformat(),
        })
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_dismiss_moderation_case(request, slug, case_id):
    """
    Organiser dismisses a moderation case with a reason.
    """
    event = get_object_or_404(Event, slug=slug)
    case = get_object_or_404(ModerationCase, id=case_id, event=event)

    reason = request.data.get("reason", "")

    try:
        dismissed = dismiss_moderation_case(case, request.user, reason)
        return Response({
            "case_id": str(dismissed.id),
            "status": dismissed.status,
            "decided_at": dismissed.decided_at.isoformat(),
        })
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
