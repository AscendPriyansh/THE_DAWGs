from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.events.models import Event, EventMembership, Track, Prize
from apps.events.services import update_event_settings


def organiser_manage_page(request, slug):
    if not request.user.is_authenticated:
        return redirect(f"/login/?next=/events/{slug}/manage/")

    event = get_object_or_404(Event, slug=slug)
    membership = EventMembership.objects.filter(
        event=event, user=request.user, status=EventMembership.Status.ACTIVE
    ).first()

    if not membership or (membership.role != EventMembership.Role.ORGANISER and not request.user.is_staff):
        return render(request, "portal/forbidden.html", {"message": "Organiser privileges required."}, status=403)

    tracks = event.tracks.all().order_by("display_order")
    prizes = event.prizes.all().order_by("display_order")

    context = {
        "event": event,
        "tracks": tracks,
        "prizes": prizes,
    }
    return render(request, "portal/organiser_manage.html", context)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def api_update_event_settings(request, slug):
    event = get_object_or_404(Event, slug=slug)
    try:
        updated = update_event_settings(request.user, event, request.data)
        return Response({
            "status": "UPDATED",
            "name": updated.name,
            "version": updated.version,
            "data_version": updated.data_version,
        })
    except PermissionDenied as e:
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
