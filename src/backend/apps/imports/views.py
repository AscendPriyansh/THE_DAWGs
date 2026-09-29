import io
from django.core.exceptions import ValidationError
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from apps.events.models import Event, EventMembership
from apps.imports.models import PortableImportPlan
from apps.imports.portable_archive import (
    PortableArchiveExporter,
    PortableArchiveImporter,
    compute_sha256,
)


def is_user_organiser(user, event=None) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    if event:
        return EventMembership.objects.filter(
            event=event, user=user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
        ).exists()
    return EventMembership.objects.filter(
        user=user, role=EventMembership.Role.ORGANISER, status=EventMembership.Status.ACTIVE
    ).exists()


@api_view(["GET", "POST"])
def api_export_portable_archive(request, slug):
    """
    Exports an Event into a standard Portable Archive v1 (zip).
    Organisers only.
    """
    event = get_object_or_404(Event, slug=slug)
    if not is_user_organiser(request.user, event):
        return Response({"detail": "Organiser permission required."}, status=status.HTTP_403_FORBIDDEN)

    exporter = PortableArchiveExporter(event=event, exporting_user=request.user)
    zip_bytes, sha256_hex = exporter.export_to_bytes()

    filename = f"event-{event.slug}-export.zip"
    response = HttpResponse(zip_bytes, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Archive-SHA256"] = sha256_hex
    # 24-hour expiry indication
    response["Cache-Control"] = "private, max-age=86400"
    return response


@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
def api_import_preview(request):
    """
    Step 1 of Import: Validates uploaded archive, produces dry-run preview,
    and returns a PortableImportPlan. No live changes occur.
    """
    if not is_user_organiser(request.user):
        return Response({"detail": "Organiser permission required."}, status=status.HTTP_403_FORBIDDEN)

    archive_file = request.FILES.get("archive")
    if not archive_file:
        return Response({"detail": "Missing required 'archive' file in multipart upload."}, status=status.HTTP_400_BAD_REQUEST)

    archive_bytes = archive_file.read()
    importer = PortableArchiveImporter(archive_bytes=archive_bytes, operator_user=request.user)

    try:
        plan = importer.validate_and_create_plan()
    except ValidationError as e:
        return Response({"detail": str(e.message if hasattr(e, "message") else e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"detail": f"Archive validation error: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        "plan_id": str(plan.id),
        "archive_sha256": plan.archive_sha256,
        "source_instance_id": plan.source_instance_id,
        "format_version": plan.format_version,
        "preview": plan.preview_json,
        "expires_at": plan.expires_at.isoformat(),
    })


@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
def api_import_apply(request):
    """
    Step 2 of Import: Applies a validated, unexpired PortableImportPlan.
    Requires plan_id and matching archive bytes.
    Creates a new DRAFT event with remapped IDs.
    """
    if not is_user_organiser(request.user):
        return Response({"detail": "Organiser permission required."}, status=status.HTTP_403_FORBIDDEN)

    plan_id = request.data.get("plan_id")
    if not plan_id:
        return Response({"detail": "Missing required 'plan_id'."}, status=status.HTTP_400_BAD_REQUEST)

    plan = get_object_or_404(PortableImportPlan, id=plan_id)

    archive_file = request.FILES.get("archive")
    if not archive_file:
        return Response({"detail": "Missing required 'archive' file in multipart upload."}, status=status.HTTP_400_BAD_REQUEST)

    archive_bytes = archive_file.read()
    importer = PortableArchiveImporter(archive_bytes=archive_bytes, operator_user=request.user)

    try:
        new_event = importer.apply_plan(plan)
    except ValidationError as e:
        return Response({"detail": str(e.message if hasattr(e, "message") else e)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        return Response({"detail": f"Import failed: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        "status": "APPLIED",
        "plan_id": str(plan.id),
        "target_event_id": str(new_event.id),
        "target_event_slug": new_event.slug,
        "target_event_name": new_event.name,
        "lifecycle": new_event.lifecycle,
        "applied_at": plan.applied_at.isoformat(),
    })


@api_view(["GET"])
def api_import_plan_detail(request, plan_id):
    """
    Returns status and preview details for a PortableImportPlan.
    """
    plan = get_object_or_404(PortableImportPlan, id=plan_id)
    return Response({
        "plan_id": str(plan.id),
        "archive_sha256": plan.archive_sha256,
        "source_instance_id": plan.source_instance_id,
        "format_version": plan.format_version,
        "preview": plan.preview_json,
        "expires_at": plan.expires_at.isoformat(),
        "applied_at": plan.applied_at.isoformat() if plan.applied_at else None,
        "target_event_id": str(plan.target_event_id) if plan.target_event_id else None,
    })
