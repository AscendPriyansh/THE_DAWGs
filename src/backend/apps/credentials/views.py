"""
M09 REST API views for credentials, certificates, awards, and verification.
"""

import base64
import json
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.audit.models import AuditEvent
from apps.credentials.models import (
    AwardDecision,
    CertificateTemplate,
    CredentialStatusEvent,
    IssuedCredential,
    PublicRecordConsent,
    SigningKey,
)
from apps.credentials.services import (
    check_judge_eligibility,
    check_participant_eligibility,
    create_award_decision,
    get_eligible_recipients,
    grant_public_consent,
    issue_credentials_batch,
    revoke_credential,
    verify_credential,
    withdraw_public_consent,
)
from apps.credentials.signing import build_downloadable_envelope
from apps.events.models import Event, EventMembership, Prize
from apps.results.models import Publication
from apps.submissions.models import Project


# ---------------------------------------------------------------------------
# Template management
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
def api_certificate_templates(request, slug):
    """List or create certificate templates for an event."""
    event = get_object_or_404(Event, slug=slug)

    membership = EventMembership.objects.filter(
        event=event, user=request.user, role=EventMembership.Role.ORGANISER,
    ).first()
    if not membership:
        return Response({"detail": "Organiser permissions required."}, status=403)

    if request.method == "GET":
        templates = CertificateTemplate.objects.filter(event=event)
        data = [
            {
                "id": str(t.id),
                "kind": t.kind,
                "version": t.version,
                "title": t.title,
                "subtitle": t.subtitle,
                "body_text": t.body_text,
                "footer_text": t.footer_text,
                "layout_version": t.layout_version,
                "created_at": t.created_at.isoformat(),
            }
            for t in templates
        ]
        return Response(data)

    # POST: create template
    kind = request.data.get("kind")
    if kind not in dict(CertificateTemplate.Kind.choices):
        return Response({"detail": f"Invalid kind. Must be one of {list(dict(CertificateTemplate.Kind.choices).keys())}"}, status=400)

    # Auto-increment version
    latest = CertificateTemplate.objects.filter(event=event, kind=kind).order_by("-version").first()
    version = (latest.version + 1) if latest else 1

    template = CertificateTemplate.objects.create(
        event=event,
        kind=kind,
        version=version,
        title=request.data.get("title", f"{event.name} - {kind.title()} Certificate"),
        subtitle=request.data.get("subtitle", ""),
        body_text=request.data.get("body_text", ""),
        footer_text=request.data.get("footer_text", ""),
        layout_version=request.data.get("layout_version", "v1"),
        created_by=request.user,
    )

    return Response({
        "id": str(template.id),
        "kind": template.kind,
        "version": template.version,
        "title": template.title,
    }, status=201)


# ---------------------------------------------------------------------------
# Award decisions
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
def api_award_decisions(request, slug):
    """List or create award decisions for an event."""
    event = get_object_or_404(Event, slug=slug)

    membership = EventMembership.objects.filter(
        event=event, user=request.user, role=EventMembership.Role.ORGANISER,
    ).first()
    if not membership:
        return Response({"detail": "Organiser permissions required."}, status=403)

    if request.method == "GET":
        awards = AwardDecision.objects.filter(
            publication__event=event,
        ).select_related("prize", "project", "decided_by", "publication")
        data = [
            {
                "id": str(a.id),
                "publication_number": a.publication.number,
                "prize_name": a.prize.name,
                "project_title": str(a.project),
                "decided_by": a.decided_by.display_name,
                "reason": a.reason,
                "created_at": a.created_at.isoformat(),
            }
            for a in awards
        ]
        return Response(data)

    # POST: create award decision
    publication_id = request.data.get("publication_id")
    prize_id = request.data.get("prize_id")
    project_id = request.data.get("project_id")
    reason = request.data.get("reason", "")

    if not all([publication_id, prize_id, project_id]):
        return Response({"detail": "publication_id, prize_id, and project_id are required."}, status=400)

    try:
        publication = Publication.objects.get(id=publication_id, event=event)
        prize = Prize.objects.get(id=prize_id, event=event)
        project = Project.objects.get(id=project_id)
    except (Publication.DoesNotExist, Prize.DoesNotExist, Project.DoesNotExist) as e:
        return Response({"detail": str(e)}, status=404)

    try:
        award = create_award_decision(
            event=event,
            publication=publication,
            prize=prize,
            project=project,
            actor=request.user,
            reason=reason,
        )
    except (ValidationError, Exception) as e:
        return Response({"detail": str(e)}, status=400)

    return Response({
        "id": str(award.id),
        "prize_name": prize.name,
        "project_title": str(project),
    }, status=201)


# ---------------------------------------------------------------------------
# Eligible recipients preview
# ---------------------------------------------------------------------------

@api_view(["GET"])
def api_eligible_recipients(request, slug):
    """Preview eligible recipients for credential issuance."""
    event = get_object_or_404(Event, slug=slug)

    membership = EventMembership.objects.filter(
        event=event, user=request.user, role=EventMembership.Role.ORGANISER,
    ).first()
    if not membership:
        return Response({"detail": "Organiser permissions required."}, status=403)

    kind = request.query_params.get("kind", "PARTICIPANT")
    publication_id = request.query_params.get("publication_id")
    publication = None
    if publication_id:
        publication = Publication.objects.filter(id=publication_id, event=event).first()

    recipients = get_eligible_recipients(event, kind, publication)

    data = [
        {
            "user_id": str(r["user"].id),
            "display_name": r["user"].display_name,
            "eligible": r["eligibility"]["eligible"],
            "reason": r["eligibility"]["reason"],
        }
        for r in recipients
    ]

    return Response({
        "kind": kind,
        "count": len(data),
        "recipients": data,
    })


# ---------------------------------------------------------------------------
# Credential issuance
# ---------------------------------------------------------------------------

@api_view(["POST"])
def api_issue_credentials(request, slug):
    """Issue credentials for all eligible recipients of a given kind."""
    event = get_object_or_404(Event, slug=slug)

    kind = request.data.get("kind")
    if kind not in dict(IssuedCredential.Kind.choices):
        return Response({"detail": "Invalid kind."}, status=400)

    publication_id = request.data.get("publication_id")
    publication = None
    if publication_id:
        publication = Publication.objects.filter(id=publication_id, event=event).first()
        if not publication:
            return Response({"detail": "Publication not found."}, status=404)

    try:
        issued = issue_credentials_batch(
            event=event,
            kind=kind,
            actor=request.user,
            publication=publication,
        )
    except Exception as e:
        return Response({"detail": str(e)}, status=400)

    return Response({
        "issued_count": len(issued),
        "credentials": [
            {
                "id": str(c.id),
                "subject": c.display_name_snapshot,
                "kind": c.kind,
            }
            for c in issued
        ],
    }, status=201)


# ---------------------------------------------------------------------------
# Credential listing / detail / download
# ---------------------------------------------------------------------------

@api_view(["GET"])
def api_event_credentials(request, slug):
    """List issued credentials for an event."""
    event = get_object_or_404(Event, slug=slug)

    # Organisers see all; other users see only their own
    membership = EventMembership.objects.filter(
        event=event, user=request.user,
    ).first()

    if membership and membership.role == EventMembership.Role.ORGANISER:
        credentials = IssuedCredential.objects.filter(event=event)
    elif request.user.is_authenticated:
        credentials = IssuedCredential.objects.filter(event=event, subject_user=request.user)
    else:
        return Response({"detail": "Authentication required."}, status=401)

    data = []
    for c in credentials.select_related("signing_key"):
        latest_status = CredentialStatusEvent.objects.filter(
            credential=c,
        ).order_by("-created_at").first()

        status = "ACTIVE"
        if latest_status:
            status = latest_status.state

        data.append({
            "id": str(c.id),
            "kind": c.kind,
            "subject": c.display_name_snapshot,
            "status": status,
            "issued_at": c.issued_at.isoformat(),
            "has_pdf": bool(c.pdf_storage_key),
        })

    return Response(data)


@api_view(["GET"])
def api_credential_detail(request, slug, credential_id):
    """Get credential detail with verification report."""
    event = get_object_or_404(Event, slug=slug)
    credential = get_object_or_404(IssuedCredential, id=credential_id, event=event)

    # Access control: subject user, organisers, or public verification
    is_subject = request.user.is_authenticated and credential.subject_user_id == request.user.id
    is_organiser = EventMembership.objects.filter(
        event=event, user=request.user, role=EventMembership.Role.ORGANISER,
    ).exists() if request.user.is_authenticated else False

    if not is_subject and not is_organiser:
        return Response({"detail": "Access denied."}, status=403)

    verification = verify_credential(credential)

    # Build envelope for download
    envelope = build_downloadable_envelope(
        bytes(credential.payload_bytes),
        bytes(credential.signature_bytes),
        credential.signing_key.key_id,
    )

    return Response({
        "id": str(credential.id),
        "kind": credential.kind,
        "display_name": credential.display_name_snapshot,
        "eligibility_snapshot": credential.eligibility_snapshot,
        "issued_at": credential.issued_at.isoformat(),
        "key_id": credential.signing_key.key_id,
        "has_pdf": bool(credential.pdf_storage_key),
        "pdf_sha256": credential.pdf_sha256,
        "verification": verification,
        "envelope": envelope,
    })


@api_view(["POST"])
def api_revoke_credential(request, slug, credential_id):
    """Revoke an issued credential."""
    event = get_object_or_404(Event, slug=slug)
    credential = get_object_or_404(IssuedCredential, id=credential_id, event=event)

    membership = EventMembership.objects.filter(
        event=event, user=request.user, role=EventMembership.Role.ORGANISER,
    ).first()
    if not membership:
        return Response({"detail": "Organiser permissions required."}, status=403)

    reason = request.data.get("reason", "")
    status_event = revoke_credential(credential, request.user, reason)

    return Response({
        "id": str(status_event.id),
        "credential_id": str(credential.id),
        "state": status_event.state,
        "reason": status_event.reason,
    })


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

@api_view(["GET"])
def api_credential_pdf(request, slug, credential_id):
    """Download the PDF certificate for a credential."""
    event = get_object_or_404(Event, slug=slug)
    credential = get_object_or_404(IssuedCredential, id=credential_id, event=event)

    # Access: subject user or organiser
    is_subject = request.user.is_authenticated and credential.subject_user_id == request.user.id
    is_organiser = EventMembership.objects.filter(
        event=event, user=request.user, role=EventMembership.Role.ORGANISER,
    ).exists() if request.user.is_authenticated else False

    if not is_subject and not is_organiser:
        return Response({"detail": "Access denied."}, status=403)

    if not credential.pdf_storage_key:
        return Response({"detail": "No PDF available."}, status=404)

    pdf_path = os.path.join(settings.MEDIA_ROOT, credential.pdf_storage_key)
    if not os.path.exists(pdf_path):
        return Response({"detail": "PDF file not found."}, status=404)

    return FileResponse(
        open(pdf_path, "rb"),
        content_type="application/pdf",
        as_attachment=True,
        filename=f"certificate-{credential.kind.lower()}-{str(credential.id)[:8]}.pdf",
    )


# ---------------------------------------------------------------------------
# Public verification
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([AllowAny])
def api_verify_credential(request, credential_id):
    """
    Public machine-readable verification endpoint.
    Reports: signature integrity, key trust, revocation status.
    """
    credential = get_object_or_404(IssuedCredential, id=credential_id)

    verification = verify_credential(credential)

    # Check consent for public display
    consent = PublicRecordConsent.objects.filter(
        event=credential.event,
        subject_user=credential.subject_user,
        withdrawn_at__isnull=True,
    ).first()

    # Public display: show name only if consent is active
    display_name = credential.display_name_snapshot if consent else "[Name withheld]"

    return Response({
        "credential_id": str(credential.id),
        "kind": credential.kind,
        "display_name": display_name,
        "event_name": credential.event.name,
        "issued_at": credential.issued_at.isoformat(),
        "verification": verification,
    })


def verify_credential_page(request, credential_id):
    """Public HTML verification page."""
    try:
        credential = IssuedCredential.objects.select_related(
            "event", "signing_key"
        ).get(id=credential_id)
    except IssuedCredential.DoesNotExist:
        raise Http404("Credential not found.")

    verification = verify_credential(credential)

    consent = PublicRecordConsent.objects.filter(
        event=credential.event,
        subject_user=credential.subject_user,
        withdrawn_at__isnull=True,
    ).first()

    display_name = credential.display_name_snapshot if consent else "[Name withheld]"

    context = {
        "credential": credential,
        "display_name": display_name,
        "verification": verification,
        "consent_active": consent is not None,
    }

    return render(request, "portal/verify_credential.html", context)


# ---------------------------------------------------------------------------
# Signing key metadata (public)
# ---------------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([AllowAny])
def api_issuer_keys(request):
    """Public endpoint listing issuer signing key metadata."""
    keys = SigningKey.objects.all().order_by("-created_at")
    data = [
        {
            "key_id": k.key_id,
            "issuer_id": k.issuer_id,
            "algorithm": k.algorithm,
            "public_key_base64": base64.urlsafe_b64encode(bytes(k.public_key_bytes)).decode("ascii"),
            "state": k.state,
            "created_at": k.created_at.isoformat(),
            "retired_at": k.retired_at.isoformat() if k.retired_at else None,
        }
        for k in keys
    ]
    return Response({"keys": data})


# ---------------------------------------------------------------------------
# Public consent management
# ---------------------------------------------------------------------------

@api_view(["GET", "POST", "DELETE"])
def api_public_consent(request, slug):
    """Manage public record consent for the current user."""
    event = get_object_or_404(Event, slug=slug)

    if not request.user.is_authenticated:
        return Response({"detail": "Authentication required."}, status=401)

    if request.method == "GET":
        consent = PublicRecordConsent.objects.filter(
            event=event, subject_user=request.user,
        ).first()
        if not consent:
            return Response({"consented": False})
        return Response({
            "consented": consent.is_active,
            "allowed_fields": consent.allowed_public_fields,
            "granted_at": consent.granted_at.isoformat(),
            "withdrawn_at": consent.withdrawn_at.isoformat() if consent.withdrawn_at else None,
        })

    elif request.method == "POST":
        fields = request.data.get("allowed_fields", ["display_name", "contribution_summary"])
        consent = grant_public_consent(event, request.user, fields)
        return Response({
            "consented": True,
            "allowed_fields": consent.allowed_public_fields,
        })

    elif request.method == "DELETE":
        try:
            consent = withdraw_public_consent(event, request.user)
            return Response({"consented": False})
        except ValidationError as e:
            return Response({"detail": str(e)}, status=404)


# ---------------------------------------------------------------------------
# Credentials web page
# ---------------------------------------------------------------------------

def credentials_page(request, slug=None):
    """Web page for viewing issued credentials."""
    if not request.user.is_authenticated:
        return render(request, "portal/forbidden.html", status=403)

    event = Event.objects.first()
    if slug:
        event = get_object_or_404(Event, slug=slug)

    if not event:
        return render(request, "portal/credentials.html", {"event": None, "credentials": []})

    membership = EventMembership.objects.filter(
        event=event, user=request.user,
    ).first()

    is_organiser = membership and membership.role == EventMembership.Role.ORGANISER

    if is_organiser:
        credentials = IssuedCredential.objects.filter(event=event).select_related("signing_key", "subject_user")
    else:
        credentials = IssuedCredential.objects.filter(
            event=event, subject_user=request.user,
        ).select_related("signing_key")

    credential_list = []
    for c in credentials:
        latest_status = CredentialStatusEvent.objects.filter(
            credential=c,
        ).order_by("-created_at").first()
        status = latest_status.state if latest_status else "ACTIVE"
        credential_list.append({
            "id": str(c.id),
            "kind": c.kind,
            "subject": c.display_name_snapshot,
            "status": status,
            "issued_at": c.issued_at,
            "has_pdf": bool(c.pdf_storage_key),
        })

    context = {
        "event": event,
        "is_organiser": is_organiser,
        "credentials": credential_list,
    }

    return render(request, "portal/credentials.html", context)
