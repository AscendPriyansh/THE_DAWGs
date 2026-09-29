import re
import secrets
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from apps.events.models import Event, Track, EmbedConfiguration, EventMembership
from apps.submissions.models import Project
from apps.audit.models import AuditEvent

ORIGIN_REGEX = re.compile(r"^https?://[a-zA-Z0-9.\-]+(?::\d{1,5})?$")


def validate_origin(origin_str: str) -> bool:
    if not isinstance(origin_str, str):
        return False
    origin = origin_str.strip()
    if origin in ("*", "'self'"):
        return True
    return bool(ORIGIN_REGEX.match(origin))


def build_frame_ancestors_csp(embed_config: EmbedConfiguration | None, is_available: bool) -> str:
    if not is_available or not embed_config or not embed_config.enabled:
        return "frame-ancestors 'none';"
    origins = embed_config.allowed_parent_origins_json or []
    if not origins:
        return "frame-ancestors 'self';"
    if "*" in origins:
        return "frame-ancestors *;"
    valid_origins = [o for o in origins if validate_origin(o)]
    if not valid_origins:
        return "frame-ancestors 'self';"
    return f"frame-ancestors {' '.join(valid_origins)};"


def generate_embed_snippet(request, event: Event) -> str:
    scheme = "https" if request.is_secure() else "http"
    host = request.get_host()
    embed_url = f"{scheme}://{host}/embed/events/{event.slug}"
    return (
        f'<iframe\n'
        f'  src="{embed_url}"\n'
        f'  title="{event.name} project gallery"\n'
        f'  loading="lazy"\n'
        f'  sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"\n'
        f'  referrerpolicy="no-referrer"\n'
        f'  style="width:100%;height:640px;border:0"\n'
        f'></iframe>'
    )


@xframe_options_exempt
def embed_gallery_view(request, slug):
    """
    Read-only public embed route rendering eligible official submissions.
    Never uses a session to unlock additional content.
    Sets CSP frame-ancestors matching configured origins; removes X-Frame-Options.
    """
    event = Event.objects.filter(slug=slug).first()
    embed_config = EmbedConfiguration.objects.filter(event=event).first() if event else None

    # Verify event is published and embed is enabled
    is_available = (
        event is not None
        and event.lifecycle == Event.Lifecycle.PUBLISHED
        and (embed_config is None or embed_config.enabled)
    )

    csp_header = build_frame_ancestors_csp(embed_config, is_available)

    if not is_available:
        theme = embed_config.theme if embed_config else "auto"
        response = render(
            request,
            "portal/embed_unavailable.html",
            {
                "message": "This project gallery is currently unavailable or disabled by the event organiser.",
                "theme": theme.lower() if theme else "auto",
            },
            status=200,
        )
        response["Content-Security-Policy"] = csp_header
        if "X-Frame-Options" in response:
            del response["X-Frame-Options"]
        return response

    # Eligible official submissions: submitted projects only, no drafts, no duplicates, no disqualified
    qs = (
        Project.objects.filter(
            event=event,
            state=Project.State.SUBMITTED,
            duplicate_of__isnull=True,
        )
        .select_related("team", "submitted_revision", "submitted_revision__track")
        .order_by("submitted_revision__title")
    )

    track_slug = request.GET.get("track", "").strip()
    if not track_slug and embed_config and embed_config.default_track:
        track_slug = embed_config.default_track.slug

    if track_slug:
        qs = qs.filter(submitted_revision__track__slug=track_slug)

    query = request.GET.get("q", "").strip()
    show_search = embed_config.show_search if embed_config else True
    if show_search and query:
        from django.db.models import Q
        qs = qs.filter(
            Q(submitted_revision__title__icontains=query)
            | Q(submitted_revision__summary__icontains=query)
            | Q(team__name__icontains=query)
        )

    projects = list(qs)
    tracks = event.tracks.all().order_by("display_order")
    theme = (embed_config.theme if embed_config else "AUTO").lower()

    # Per-instance nonce for auto-resize message
    nonce = secrets.token_hex(8)

    context = {
        "event": event,
        "projects": projects,
        "tracks": tracks,
        "selected_track": track_slug,
        "query": query if show_search else "",
        "show_search": show_search,
        "theme": theme,
        "nonce": nonce,
    }

    response = render(request, "portal/embed_gallery.html", context)
    response["Content-Security-Policy"] = csp_header
    # Ensure X-Frame-Options is removed
    if "X-Frame-Options" in response:
        del response["X-Frame-Options"]

    return response


@api_view(["GET", "PUT"])
def api_embed_config(request, slug):
    """
    GET: Returns current embed configuration and HTML iframe snippet.
    PUT: Updates embed configuration (Organiser only).
    """
    event = get_object_or_404(Event, slug=slug)
    is_organiser = False
    if request.user and request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser:
            is_organiser = True
        else:
            is_organiser = EventMembership.objects.filter(
                event=event, user=request.user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
            ).exists()

    if request.method == "GET":
        embed_config, _ = EmbedConfiguration.objects.get_or_create(event=event)
        return Response({
            "id": str(embed_config.id),
            "event_slug": event.slug,
            "enabled": embed_config.enabled,
            "allowed_parent_origins": embed_config.allowed_parent_origins_json,
            "theme": embed_config.theme,
            "default_track_id": str(embed_config.default_track_id) if embed_config.default_track_id else None,
            "show_search": embed_config.show_search,
            "version": embed_config.version,
            "snippet": generate_embed_snippet(request, event),
        })

    # PUT: Organiser only
    if not is_organiser:
        return Response({"detail": "Organiser permission required."}, status=status.HTTP_403_FORBIDDEN)

    data = request.data
    origins = data.get("allowed_parent_origins", [])
    if not isinstance(origins, list):
        return Response({"detail": "allowed_parent_origins must be a list of origin strings."}, status=status.HTTP_400_BAD_REQUEST)

    # Validate each origin
    for o in origins:
        if not validate_origin(o):
            return Response({
                "detail": f"Invalid origin '{o}'. Must be a valid URI origin (e.g. 'https://example.com' or 'http://localhost:3000'), '*' or ''self'' without paths."
            }, status=status.HTTP_400_BAD_REQUEST)

    theme = data.get("theme", "AUTO").upper()
    if theme not in EmbedConfiguration.Theme.values:
        return Response({"detail": f"Invalid theme. Must be one of {EmbedConfiguration.Theme.values}"}, status=status.HTTP_400_BAD_REQUEST)

    default_track = None
    default_track_id = data.get("default_track_id")
    if default_track_id:
        try:
            default_track = Track.objects.get(id=default_track_id, event=event)
        except Track.DoesNotExist:
            return Response({"detail": "default_track_id does not belong to this event."}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        embed_config, created = EmbedConfiguration.objects.get_or_create(event=event)
        embed_config.enabled = bool(data.get("enabled", embed_config.enabled))
        embed_config.allowed_parent_origins_json = origins
        embed_config.theme = theme
        embed_config.default_track = default_track
        embed_config.show_search = bool(data.get("show_search", embed_config.show_search))
        embed_config.version += 1
        embed_config.save()

        actor_user = request.user if request.user and request.user.is_authenticated else None
        AuditEvent.objects.create(
            event=event,
            actor_user=actor_user,
            actor_kind=AuditEvent.ActorKind.USER if actor_user else AuditEvent.ActorKind.SYSTEM,
            action="EMBED_CONFIG_UPDATED",
            entity_type="EmbedConfiguration",
            entity_id=embed_config.id,
            after_json={
                "enabled": embed_config.enabled,
                "allowed_origins": embed_config.allowed_parent_origins_json,
                "theme": embed_config.theme,
                "show_search": embed_config.show_search,
                "version": embed_config.version,
            },
            reason="Organiser updated embed configuration",
        )

    return Response({
        "id": str(embed_config.id),
        "event_slug": event.slug,
        "enabled": embed_config.enabled,
        "allowed_parent_origins": embed_config.allowed_parent_origins_json,
        "theme": embed_config.theme,
        "default_track_id": str(embed_config.default_track_id) if embed_config.default_track_id else None,
        "show_search": embed_config.show_search,
        "version": embed_config.version,
        "snippet": generate_embed_snippet(request, event),
    })
