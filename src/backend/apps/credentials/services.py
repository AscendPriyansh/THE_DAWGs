"""
M09 Domain services: eligibility checks, credential issuance, award decisions,
verification, and key management.
"""

import os
import uuid
from datetime import datetime

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.credentials.models import (
    AwardDecision,
    CertificateTemplate,
    CredentialStatusEvent,
    IssuedCredential,
    PublicRecordConsent,
    SigningKey,
)
from apps.credentials.signing import (
    build_credential_payload,
    build_downloadable_envelope,
    canonical_json_bytes,
    generate_ed25519_keypair,
    load_private_key,
    serialize_private_key,
    sha256_hex,
    sign_payload,
    verify_signature,
)
from apps.credentials.pdf_generator import generate_certificate_pdf, pdf_sha256
from apps.events.models import Event, EventMembership, Prize
from apps.judging.models import JudgeAssignment, Review
from apps.results.models import Publication, ResultRow
from apps.submissions.models import Project, ProjectRevision
from apps.teams.models import TeamMember


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def _get_key_storage_dir():
    """Get the directory for storing private key PEM files."""
    key_dir = os.environ.get("DOGFOOD_SIGNING_KEY_DIR")
    if not key_dir:
        key_dir = os.path.join(settings.BASE_DIR, "signing_keys")
    os.makedirs(key_dir, exist_ok=True)
    return key_dir


def create_signing_key(issuer_id: str = "dogfood-portal") -> SigningKey:
    """Generate a new Ed25519 signing key and persist it."""
    private_key, public_bytes, key_id = generate_ed25519_keypair()

    # Store private key PEM to filesystem
    key_dir = _get_key_storage_dir()
    pem_path = os.path.join(key_dir, f"{key_id}.pem")
    pem_bytes = serialize_private_key(private_key)
    with open(pem_path, "wb") as f:
        f.write(pem_bytes)

    signing_key = SigningKey.objects.create(
        key_id=key_id,
        issuer_id=issuer_id,
        algorithm="ED25519",
        public_key_bytes=public_bytes,
        private_key_ref=pem_path,
        state=SigningKey.State.ACTIVE,
    )
    return signing_key


def get_active_signing_key() -> SigningKey:
    """Get the most recently created active signing key, or create one."""
    key = SigningKey.objects.filter(
        state=SigningKey.State.ACTIVE,
        private_key_ref__isnull=False,
    ).order_by("-created_at").first()

    if not key:
        key = create_signing_key()
    return key


def retire_signing_key(key_id: str, actor) -> SigningKey:
    """Retire a signing key. Old credentials remain verifiable."""
    key = SigningKey.objects.get(key_id=key_id)
    key.state = SigningKey.State.RETIRED
    key.retired_at = timezone.now()
    key.save(update_fields=["state", "retired_at"])
    return key


def mark_key_compromised(key_id: str, actor) -> SigningKey:
    """Mark a signing key as compromised."""
    key = SigningKey.objects.get(key_id=key_id)
    key.state = SigningKey.State.COMPROMISED
    key.retired_at = timezone.now()
    key.save(update_fields=["state", "retired_at"])
    return key


# ---------------------------------------------------------------------------
# Eligibility checks
# ---------------------------------------------------------------------------

def check_participant_eligibility(event: Event, user) -> dict:
    """
    Check if user is eligible for a participant certificate.
    Requires membership on an official submitted roster.
    """
    # Must be a participant member
    membership = EventMembership.objects.filter(
        event=event, user=user, role=EventMembership.Role.PARTICIPANT, status=EventMembership.Status.ACTIVE,
    ).first()
    if not membership:
        return {"eligible": False, "reason": "Not an active participant in this event."}

    # Must be on a team with a submitted project
    team_memberships = TeamMember.objects.filter(user=user, team__event=event)
    if not team_memberships.exists():
        return {"eligible": False, "reason": "Not a member of any team."}

    # Check for submitted project with this user in roster
    for tm in team_memberships:
        submitted_revisions = ProjectRevision.objects.filter(
            project__team=tm.team,
            project__state="SUBMITTED",
        ).exists()
        if submitted_revisions:
            return {
                "eligible": True,
                "reason": f"Participant on submitted team '{tm.team.name}'.",
                "team_name": tm.team.name,
            }

    return {"eligible": False, "reason": "No submitted project found for this participant's team."}


def check_judge_eligibility(event: Event, user) -> dict:
    """
    Check if user is eligible for a judge certificate.
    Requires at least one complete review on an active, authorised assignment.
    Quarantined/revoked assignments do not establish eligibility.
    """
    membership = EventMembership.objects.filter(
        event=event, user=user, role=EventMembership.Role.JUDGE, status=EventMembership.Status.ACTIVE,
    ).first()
    if not membership:
        return {"eligible": False, "reason": "Not an active judge in this event."}

    # Count completed reviews on active assignments
    completed_reviews = Review.objects.filter(
        assignment__event=event,
        assignment__judge_membership=membership,
        assignment__status=JudgeAssignment.Status.ACTIVE,
        status=Review.Status.SUBMITTED,
    ).count()

    if completed_reviews == 0:
        return {"eligible": False, "reason": "No completed reviews on active assignments."}

    return {
        "eligible": True,
        "reason": f"Completed {completed_reviews} review(s) on active assignments.",
        "completed_review_count": completed_reviews,
    }


def check_winner_eligibility(event: Event, user, award_decision: AwardDecision) -> dict:
    """
    Check if user is eligible for a winner certificate.
    Must be on the team of the awarded project.
    """
    project = award_decision.project
    team_member = TeamMember.objects.filter(
        user=user, team=project.team,
    ).first()
    if not team_member:
        return {"eligible": False, "reason": "Not a team member of the awarded project."}

    return {
        "eligible": True,
        "reason": f"Team member of '{project.team.name}' awarded '{award_decision.prize.name}'.",
        "award_name": award_decision.prize.name,
        "project_title": str(project),
    }


def get_eligible_recipients(event: Event, kind: str, publication=None) -> list:
    """
    Get all eligible recipients for a given credential kind.
    Returns list of dicts with user and eligibility info.
    """
    recipients = []

    if kind == "PARTICIPANT":
        participants = EventMembership.objects.filter(
            event=event, role=EventMembership.Role.PARTICIPANT, status=EventMembership.Status.ACTIVE,
        ).select_related("user")
        for m in participants:
            elig = check_participant_eligibility(event, m.user)
            if elig["eligible"]:
                recipients.append({"user": m.user, "eligibility": elig})

    elif kind == "JUDGE":
        judges = EventMembership.objects.filter(
            event=event, role=EventMembership.Role.JUDGE, status=EventMembership.Status.ACTIVE,
        ).select_related("user")
        for m in judges:
            elig = check_judge_eligibility(event, m.user)
            if elig["eligible"]:
                recipients.append({"user": m.user, "eligibility": elig})

    elif kind == "WINNER":
        if not publication:
            return []
        awards = AwardDecision.objects.filter(
            publication=publication,
        ).select_related("project__team", "prize")
        for award in awards:
            team_members = TeamMember.objects.filter(
                team=award.project.team,
            ).select_related("user")
            for tm in team_members:
                elig = check_winner_eligibility(event, tm.user, award)
                if elig["eligible"]:
                    recipients.append({
                        "user": tm.user,
                        "eligibility": elig,
                        "award_decision": award,
                    })

    return recipients


# ---------------------------------------------------------------------------
# Award decisions
# ---------------------------------------------------------------------------

@transaction.atomic
def create_award_decision(
    event: Event,
    publication: Publication,
    prize: Prize,
    project: Project,
    actor,
    reason: str = "",
) -> AwardDecision:
    """
    Record an explicit organiser award decision.
    Validates that all references belong to the same event and the project is
    in the publication's result run as an eligible entry.
    """
    # Verify organiser
    membership = EventMembership.objects.filter(
        event=event, user=actor, role=EventMembership.Role.ORGANISER,
    ).first()
    if not membership:
        raise PermissionDenied("Only organisers can create award decisions.")

    # Verify all belong to same event
    if prize.event_id != event.id:
        raise ValidationError("Prize does not belong to this event.")
    if project.team.event_id != event.id:
        raise ValidationError("Project does not belong to this event.")
    if publication.event_id != event.id:
        raise ValidationError("Publication does not belong to this event.")

    # Verify project is an eligible result entry
    result_row = ResultRow.objects.filter(
        result_run=publication.result_run, project=project, eligible=True,
    ).first()
    if not result_row:
        raise ValidationError("Project is not an eligible entry in this publication's result run.")

    award = AwardDecision.objects.create(
        publication=publication,
        prize=prize,
        project=project,
        decided_by=actor,
        reason=reason,
    )

    AuditEvent.objects.create(
        event=event,
        actor_user=actor,
        actor_kind=AuditEvent.ActorKind.USER,
        action="AWARD_DECISION_CREATED",
        entity_type="AwardDecision",
        entity_id=award.id,
        after_json={
            "prize": str(prize.id),
            "prize_name": prize.name,
            "project": str(project.id),
            "publication": str(publication.id),
            "reason": reason,
        },
    )

    return award


# ---------------------------------------------------------------------------
# Credential issuance
# ---------------------------------------------------------------------------

def _get_verification_url(credential_id: str) -> str:
    """Build the public verification URL for a credential."""
    base = os.environ.get("DOGFOOD_BASE_URL", "http://localhost:8000")
    return f"{base}/verify/{credential_id}"


@transaction.atomic
def issue_credential(
    event: Event,
    user,
    kind: str,
    template: CertificateTemplate,
    signing_key: SigningKey,
    actor,
    publication: Publication = None,
    award_decision: AwardDecision = None,
    eligibility_snapshot: dict = None,
) -> IssuedCredential:
    """
    Issue a single credential: generate PDF, sign payload, persist record.

    Steps:
    1. Generate PDF with credential ID and verification URL
    2. Hash the PDF bytes
    3. Build canonical JSON payload including pdf_sha256
    4. Sign the payload bytes with Ed25519
    5. Persist everything
    """
    if not signing_key.can_issue:
        raise ValidationError("Signing key cannot issue credentials (inactive or public-only).")

    credential_id = uuid.uuid4()
    verification_url = _get_verification_url(str(credential_id))
    now = timezone.now()

    # Determine contribution text
    if kind == "WINNER" and award_decision:
        contribution_text = f"Won {award_decision.prize.name}"
        award_name = award_decision.prize.name
    elif kind == "JUDGE":
        review_count = eligibility_snapshot.get("completed_review_count", 0) if eligibility_snapshot else 0
        contribution_text = f"Served as judge and completed {review_count} review(s)"
        award_name = None
    else:
        contribution_text = "Successfully participated"
        award_name = None

    # 1. Generate PDF
    pdf_bytes = generate_certificate_pdf(
        event_name=event.name,
        recipient_name=user.display_name,
        kind=kind,
        contribution_text=contribution_text,
        issue_date=now.strftime("%B %d, %Y"),
        credential_id=str(credential_id),
        verification_url=verification_url,
        award_name=award_name,
    )

    # 2. Hash PDF
    pdf_hash = pdf_sha256(pdf_bytes)

    # 3. Build payload
    payload_dict = build_credential_payload(
        credential_id=str(credential_id),
        issuer_id=signing_key.issuer_id,
        key_id=signing_key.key_id,
        event_id=str(event.id),
        event_name=event.name,
        subject_display_name=user.display_name,
        kind=kind,
        issued_at=now.isoformat(),
        completed_review_count=eligibility_snapshot.get("completed_review_count") if eligibility_snapshot and kind == "JUDGE" else None,
        publication_id=str(publication.id) if publication else None,
        pdf_sha256=pdf_hash,
    )
    payload_bytes = canonical_json_bytes(payload_dict)

    # 4. Sign
    private_key = load_private_key(
        open(signing_key.private_key_ref, "rb").read()
    )
    signature = sign_payload(private_key, payload_bytes)

    # 5. Store PDF to media
    pdf_dir = os.path.join(settings.MEDIA_ROOT, "certificates")
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_filename = f"{credential_id}.pdf"
    pdf_path = os.path.join(pdf_dir, pdf_filename)
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    pdf_storage_key = f"certificates/{pdf_filename}"

    # 6. Persist credential
    credential = IssuedCredential(
        id=credential_id,
        event=event,
        subject_user=user,
        kind=kind,
        template=template,
        template_version=template.version if template else None,
        publication=publication,
        award_decision=award_decision,
        eligibility_snapshot=eligibility_snapshot or {},
        display_name_snapshot=user.display_name,
        payload_bytes=payload_bytes,
        signature_bytes=signature,
        signing_key=signing_key,
        pdf_storage_key=pdf_storage_key,
        pdf_sha256=pdf_hash,
        issued_at=now,
    )
    credential.save()

    # Audit
    AuditEvent.objects.create(
        event=event,
        actor_user=actor,
        actor_kind=AuditEvent.ActorKind.USER,
        action="CREDENTIAL_ISSUED",
        entity_type="IssuedCredential",
        entity_id=credential.id,
        after_json={
            "kind": kind,
            "subject_user": str(user.id),
            "display_name": user.display_name,
            "key_id": signing_key.key_id,
        },
    )

    return credential


@transaction.atomic
def issue_credentials_batch(
    event: Event,
    kind: str,
    actor,
    publication: Publication = None,
) -> list:
    """
    Issue credentials for all eligible recipients of a given kind.
    Returns list of issued credentials. Skips already-issued.
    """
    # Verify organiser
    membership = EventMembership.objects.filter(
        event=event, user=actor, role=EventMembership.Role.ORGANISER,
    ).first()
    if not membership:
        raise PermissionDenied("Only organisers can issue credentials.")

    # Get or create template
    template = CertificateTemplate.objects.filter(
        event=event, kind=kind,
    ).order_by("-version").first()
    if not template:
        template = CertificateTemplate.objects.create(
            event=event,
            kind=kind,
            version=1,
            title=f"{event.name} - {kind.title()} Certificate",
            created_by=actor,
        )

    signing_key = get_active_signing_key()
    recipients = get_eligible_recipients(event, kind, publication)

    issued = []
    for r in recipients:
        user = r["user"]
        eligibility = r["eligibility"]
        award = r.get("award_decision")

        # Skip if already issued
        existing = IssuedCredential.objects.filter(
            event=event,
            subject_user=user,
            kind=kind,
            template_version=template.version,
            publication=publication,
        ).first()
        if existing:
            # Check if revoked/superseded
            has_active_revocation = CredentialStatusEvent.objects.filter(
                credential=existing,
            ).exists()
            if not has_active_revocation:
                continue

        credential = issue_credential(
            event=event,
            user=user,
            kind=kind,
            template=template,
            signing_key=signing_key,
            actor=actor,
            publication=publication,
            award_decision=award,
            eligibility_snapshot=eligibility,
        )
        issued.append(credential)

    return issued


# ---------------------------------------------------------------------------
# Revocation / supersession
# ---------------------------------------------------------------------------

@transaction.atomic
def revoke_credential(credential: IssuedCredential, actor, reason: str = "") -> CredentialStatusEvent:
    """Revoke an issued credential."""
    status_event = CredentialStatusEvent.objects.create(
        credential=credential,
        state=CredentialStatusEvent.State.REVOKED,
        reason=reason,
        actor_user=actor,
    )
    AuditEvent.objects.create(
        event=credential.event,
        actor_user=actor,
        actor_kind=AuditEvent.ActorKind.USER,
        action="CREDENTIAL_REVOKED",
        entity_type="IssuedCredential",
        entity_id=credential.id,
        after_json={"reason": reason},
    )
    return status_event


@transaction.atomic
def supersede_credential(
    old_credential: IssuedCredential,
    new_credential: IssuedCredential,
    actor,
    reason: str = "",
) -> CredentialStatusEvent:
    """Mark a credential as superseded by a replacement."""
    status_event = CredentialStatusEvent.objects.create(
        credential=old_credential,
        state=CredentialStatusEvent.State.SUPERSEDED,
        reason=reason,
        replacement=new_credential,
        actor_user=actor,
    )
    AuditEvent.objects.create(
        event=old_credential.event,
        actor_user=actor,
        actor_kind=AuditEvent.ActorKind.USER,
        action="CREDENTIAL_SUPERSEDED",
        entity_type="IssuedCredential",
        entity_id=old_credential.id,
        after_json={
            "reason": reason,
            "replacement_id": str(new_credential.id),
        },
    )
    return status_event


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_credential(credential: IssuedCredential) -> dict:
    """
    Verify a credential and return a detailed verification report.

    Reports separately:
    1. Whether the signed bytes are cryptographically intact
    2. Which issuer/key signed them and key trust status
    3. Current revocation/supersession status
    """
    signing_key = credential.signing_key

    # 1. Signature check
    sig_valid = verify_signature(
        bytes(signing_key.public_key_bytes),
        bytes(credential.payload_bytes),
        bytes(credential.signature_bytes),
    )

    # 2. Key status
    key_status = {
        "key_id": signing_key.key_id,
        "issuer_id": signing_key.issuer_id,
        "algorithm": signing_key.algorithm,
        "key_state": signing_key.state,
        "is_trusted": signing_key.state in (SigningKey.State.ACTIVE, SigningKey.State.RETIRED),
    }

    # 3. Credential status
    latest_status = CredentialStatusEvent.objects.filter(
        credential=credential,
    ).order_by("-created_at").first()

    credential_status = {
        "is_revoked": False,
        "is_superseded": False,
        "status_reason": None,
        "replacement_id": None,
    }
    if latest_status:
        if latest_status.state == CredentialStatusEvent.State.REVOKED:
            credential_status["is_revoked"] = True
            credential_status["status_reason"] = latest_status.reason
        elif latest_status.state == CredentialStatusEvent.State.SUPERSEDED:
            credential_status["is_superseded"] = True
            credential_status["status_reason"] = latest_status.reason
            if latest_status.replacement:
                credential_status["replacement_id"] = str(latest_status.replacement.id)

    return {
        "signature_valid": sig_valid,
        "key": key_status,
        "credential_status": credential_status,
        "overall_valid": sig_valid and key_status["is_trusted"] and not credential_status["is_revoked"],
    }


def verify_credential_offline(payload_bytes: bytes, signature_bytes: bytes, public_key_bytes: bytes) -> dict:
    """
    Offline verification with just the payload, signature and a trusted public key.
    Cannot check revocation status without network access.
    """
    sig_valid = verify_signature(public_key_bytes, payload_bytes, signature_bytes)
    return {
        "signature_valid": sig_valid,
        "note": "Offline verification cannot confirm current revocation status. "
                "Check the issuer's status endpoint for the latest information.",
    }


# ---------------------------------------------------------------------------
# Public consent management
# ---------------------------------------------------------------------------

@transaction.atomic
def grant_public_consent(event: Event, user, allowed_fields: list = None) -> PublicRecordConsent:
    """Grant or update public record consent."""
    if allowed_fields is None:
        allowed_fields = ["display_name", "contribution_summary"]

    consent, created = PublicRecordConsent.objects.update_or_create(
        event=event,
        subject_user=user,
        defaults={
            "allowed_public_fields": allowed_fields,
            "granted_at": timezone.now(),
            "withdrawn_at": None,
        },
    )
    return consent


@transaction.atomic
def withdraw_public_consent(event: Event, user) -> PublicRecordConsent:
    """Withdraw public record consent. Public pages become unavailable."""
    try:
        consent = PublicRecordConsent.objects.get(event=event, subject_user=user)
    except PublicRecordConsent.DoesNotExist:
        raise ValidationError("No consent record found.")

    consent.withdrawn_at = timezone.now()
    consent.save(update_fields=["withdrawn_at"])
    return consent
