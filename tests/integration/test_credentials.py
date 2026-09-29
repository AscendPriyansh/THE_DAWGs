"""
M09 Integration Tests: Credentials, certificates, signed judge records, and verification.

Tests cover:
1. Certificate template management
2. Award decision creation and validation
3. Participant eligibility checks
4. Judge eligibility checks (requires completed reviews on active assignments)
5. Winner eligibility checks
6. Credential issuance with Ed25519 signing
7. PDF generation and SHA-256 integrity
8. Credential verification (signature, key trust, revocation status)
9. Offline verification
10. Key rotation (old keys verify, new key issues)
11. Key compromise handling
12. Credential revocation and supersession
13. Public consent management
14. Tampered payload detection
"""

import base64
import json
import os
import uuid
from datetime import timedelta

import pytest
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.credentials.models import (
    AwardDecision,
    CertificateTemplate,
    CredentialStatusEvent,
    IssuedCredential,
    PublicRecordConsent,
    SigningKey,
)
from apps.credentials.pdf_generator import generate_certificate_pdf, pdf_sha256
from apps.credentials.services import (
    check_judge_eligibility,
    check_participant_eligibility,
    create_award_decision,
    create_signing_key,
    get_active_signing_key,
    get_eligible_recipients,
    grant_public_consent,
    issue_credential,
    issue_credentials_batch,
    mark_key_compromised,
    retire_signing_key,
    revoke_credential,
    supersede_credential,
    verify_credential,
    verify_credential_offline,
    withdraw_public_consent,
)
from apps.credentials.signing import (
    build_credential_payload,
    build_downloadable_envelope,
    canonical_json_bytes,
    generate_ed25519_keypair,
    load_private_key,
    load_public_key,
    serialize_private_key,
    sha256_hex,
    sign_payload,
    verify_signature,
)
from apps.events.models import Event, EventMembership, Prize, Track
from apps.judging.models import (
    Criterion,
    JudgeAssignment,
    JudgeTrackPermission,
    Review,
    ReviewRevision,
    ReviewScore,
    Rubric,
)
from apps.results.models import Publication, ResultRow, ResultRun
from apps.submissions.models import Project, ProjectRevision
from apps.teams.models import Team, TeamMember


@pytest.mark.django_db(transaction=True)
class TestCredentialsM09(TransactionTestCase):
    """Full M09 integration tests."""

    def _create_base_event(self):
        """Create a base event with all required fields."""
        now = timezone.now()
        event = Event.objects.create(
            slug="cred-test-2026",
            name="Credential Test Hackathon 2026",
            lifecycle=Event.Lifecycle.PUBLISHED,
            registration_opens_at=now - timedelta(days=30),
            registration_closes_at=now - timedelta(days=20),
            submissions_opens_at=now - timedelta(days=20),
            submissions_closes_at=now - timedelta(days=5),
            judging_opens_at=now - timedelta(days=5),
            judging_closes_at=now - timedelta(days=1),
            voting_opens_at=now - timedelta(days=5),
            voting_closes_at=now - timedelta(days=1),
        )
        return event

    def _create_users(self):
        """Create test users."""
        organiser = User.objects.create_user(
            email="organiser@test.com", password="password123",
            display_name="Test Organiser",
        )
        judge = User.objects.create_user(
            email="judge@test.com", password="password123",
            display_name="Test Judge",
        )
        participant = User.objects.create_user(
            email="participant@test.com", password="password123",
            display_name="Test Participant",
        )
        return organiser, judge, participant

    def _setup_full_workflow(self):
        """Set up a complete event with teams, submissions, judging, and publication."""
        event = self._create_base_event()
        organiser, judge_user, participant = self._create_users()

        # Memberships
        org_mem = EventMembership.objects.create(
            event=event, user=organiser, role=EventMembership.Role.ORGANISER,
        )
        judge_mem = EventMembership.objects.create(
            event=event, user=judge_user, role=EventMembership.Role.JUDGE,
        )
        part_mem = EventMembership.objects.create(
            event=event, user=participant, role=EventMembership.Role.PARTICIPANT,
        )

        # Track & Prize
        track = Track.objects.create(event=event, slug="main", name="Main Track")
        prize = Prize.objects.create(event=event, name="Best Project", track=track)

        # Team & project
        team = Team.objects.create(event=event, name="Alpha Team", captain_user=participant)
        TeamMember.objects.create(event=event, team=team, user=participant)

        project = Project.objects.create(
            event=event, team=team, state="SUBMITTED",
        )
        revision = ProjectRevision.objects.create(
            project=project, number=1, track=track,
            title="Alpha Project", summary="A test project",
            description_md="Test",
            roster_snapshot=[{"display_name": "Test Participant"}],
        )
        project.submitted_revision = revision
        project.save()

        # Rubric & judging
        rubric = Rubric.objects.create(
            event=event, name="Test Rubric", version_number=1,
            state=Rubric.State.FROZEN, frozen_at=timezone.now(),
        )
        criterion = Criterion.objects.create(
            rubric=rubric, key="quality", label="Quality",
        )

        # Judge track permission
        JudgeTrackPermission.objects.create(
            membership=judge_mem, track=track, granted_by=organiser,
        )

        # Assignment & review
        assignment = JudgeAssignment.objects.create(
            event=event,
            judge_membership=judge_mem,
            project=project,
            project_revision=revision,
            rubric=rubric,
            status=JudgeAssignment.Status.ACTIVE,
            assigned_by=organiser,
        )

        review = Review.objects.create(
            assignment=assignment,
            status=Review.Status.SUBMITTED,
            submitted_at=timezone.now(),
        )
        ReviewScore.objects.create(review=review, criterion=criterion, value=4)

        # Result run & publication
        result_run = ResultRun.objects.create(
            event=event,
            source_data_version=1,
            algorithm_version="RAW_WEIGHTED_V1",
            input_snapshot_json={},
            input_sha256="abc123",
            output_sha256="def456",
        )
        result_row = ResultRow.objects.create(
            result_run=result_run,
            project=project,
            eligible=True,
            rank=1,
            ranking_value=4.0,
            completed_review_count=1,
        )
        publication = Publication.objects.create(
            event=event,
            result_run=result_run,
            number=1,
            published_by=organiser,
        )
        event.active_publication_id = publication.id
        event.save()

        return {
            "event": event,
            "organiser": organiser,
            "judge_user": judge_user,
            "participant": participant,
            "judge_mem": judge_mem,
            "team": team,
            "project": project,
            "track": track,
            "prize": prize,
            "rubric": rubric,
            "assignment": assignment,
            "review": review,
            "result_run": result_run,
            "result_row": result_row,
            "publication": publication,
        }

    # ----- Test 1: Ed25519 Key Generation & Signing -----
    def test_ed25519_key_generation_and_signing(self):
        """Test Ed25519 key generation, signing, and verification."""
        private_key, public_bytes, key_id = generate_ed25519_keypair()

        assert len(public_bytes) == 32
        assert key_id.startswith("dgk_")

        payload = canonical_json_bytes({"test": "data", "number": 42})
        signature = sign_payload(private_key, payload)
        assert len(signature) == 64

        # Verify with correct data
        assert verify_signature(public_bytes, payload, signature) is True

        # Verify with tampered data fails
        tampered = canonical_json_bytes({"test": "tampered", "number": 42})
        assert verify_signature(public_bytes, tampered, signature) is False

    # ----- Test 2: Deterministic JSON Encoding -----
    def test_canonical_json_deterministic(self):
        """Canonical JSON must produce identical bytes regardless of insertion order."""
        d1 = {"z": "last", "a": "first", "m": 42}
        d2 = {"a": "first", "m": 42, "z": "last"}

        assert canonical_json_bytes(d1) == canonical_json_bytes(d2)
        # Check compact separators
        result = canonical_json_bytes(d1)
        assert b" " not in result
        assert b": " not in result

    # ----- Test 3: Participant Eligibility -----
    def test_participant_eligibility(self):
        """Participant needs membership on a submitted roster."""
        ctx = self._setup_full_workflow()

        elig = check_participant_eligibility(ctx["event"], ctx["participant"])
        assert elig["eligible"] is True

        # Non-participant
        elig2 = check_participant_eligibility(ctx["event"], ctx["judge_user"])
        assert elig2["eligible"] is False

    # ----- Test 4: Judge Eligibility -----
    def test_judge_eligibility_requires_completed_reviews(self):
        """Judge eligibility requires at least one completed review on an active assignment."""
        ctx = self._setup_full_workflow()

        elig = check_judge_eligibility(ctx["event"], ctx["judge_user"])
        assert elig["eligible"] is True
        assert elig["completed_review_count"] == 1

        # Inactive judge (no reviews) is not eligible
        new_judge = User.objects.create_user(
            email="lazy_judge@test.com", password="password123",
            display_name="Lazy Judge",
        )
        EventMembership.objects.create(
            event=ctx["event"], user=new_judge, role=EventMembership.Role.JUDGE,
        )
        elig2 = check_judge_eligibility(ctx["event"], new_judge)
        assert elig2["eligible"] is False

    # ----- Test 5: Quarantined Assignment Ineligibility -----
    def test_quarantined_assignment_not_eligible(self):
        """Quarantined/revoked assignments do not establish judge eligibility."""
        ctx = self._setup_full_workflow()

        # Quarantine the assignment
        ctx["assignment"].status = JudgeAssignment.Status.QUARANTINED
        ctx["assignment"].save()

        elig = check_judge_eligibility(ctx["event"], ctx["judge_user"])
        assert elig["eligible"] is False

    # ----- Test 6: Award Decision Validation -----
    def test_award_decision_creation(self):
        """Award decisions require organiser role and valid references."""
        ctx = self._setup_full_workflow()

        award = create_award_decision(
            event=ctx["event"],
            publication=ctx["publication"],
            prize=ctx["prize"],
            project=ctx["project"],
            actor=ctx["organiser"],
            reason="Outstanding work",
        )
        assert award.id is not None
        assert award.reason == "Outstanding work"

        # Audit recorded
        audit = AuditEvent.objects.filter(action="AWARD_DECISION_CREATED").first()
        assert audit is not None

    # ----- Test 7: Non-Organiser Cannot Create Awards -----
    def test_non_organiser_cannot_create_award(self):
        """Only organisers can create award decisions."""
        ctx = self._setup_full_workflow()

        from django.core.exceptions import PermissionDenied
        with pytest.raises(PermissionDenied):
            create_award_decision(
                event=ctx["event"],
                publication=ctx["publication"],
                prize=ctx["prize"],
                project=ctx["project"],
                actor=ctx["participant"],
            )

    # ----- Test 8: Credential Issuance with PDF & Signing -----
    def test_credential_issuance_with_pdf(self):
        """Issue a credential with PDF and Ed25519 signature."""
        ctx = self._setup_full_workflow()

        signing_key = create_signing_key()
        template = CertificateTemplate.objects.create(
            event=ctx["event"], kind="PARTICIPANT", version=1,
            title="Test Certificate", created_by=ctx["organiser"],
        )
        eligibility = check_participant_eligibility(ctx["event"], ctx["participant"])

        credential = issue_credential(
            event=ctx["event"],
            user=ctx["participant"],
            kind="PARTICIPANT",
            template=template,
            signing_key=signing_key,
            actor=ctx["organiser"],
            eligibility_snapshot=eligibility,
        )

        assert credential.id is not None
        assert credential.kind == "PARTICIPANT"
        assert credential.display_name_snapshot == "Test Participant"
        assert credential.pdf_storage_key is not None
        assert credential.pdf_sha256 is not None
        assert len(bytes(credential.signature_bytes)) == 64

    # ----- Test 9: PDF SHA-256 Integrity -----
    def test_pdf_sha256_integrity(self):
        """Verify PDF hash matches the signed payload's pdf_sha256."""
        ctx = self._setup_full_workflow()

        signing_key = create_signing_key()
        template = CertificateTemplate.objects.create(
            event=ctx["event"], kind="JUDGE", version=1,
            title="Judge Certificate", created_by=ctx["organiser"],
        )
        eligibility = check_judge_eligibility(ctx["event"], ctx["judge_user"])

        credential = issue_credential(
            event=ctx["event"],
            user=ctx["judge_user"],
            kind="JUDGE",
            template=template,
            signing_key=signing_key,
            actor=ctx["organiser"],
            eligibility_snapshot=eligibility,
        )

        # Read the PDF and check hash
        from django.conf import settings
        pdf_path = os.path.join(settings.MEDIA_ROOT, credential.pdf_storage_key)
        with open(pdf_path, "rb") as f:
            actual_pdf_bytes = f.read()

        assert pdf_sha256(actual_pdf_bytes) == credential.pdf_sha256

        # Verify the payload contains the correct pdf_sha256
        payload = json.loads(bytes(credential.payload_bytes).decode("utf-8"))
        assert payload["pdf_sha256"] == credential.pdf_sha256

    # ----- Test 10: Signature Verification -----
    def test_credential_verification(self):
        """Verify a credential with full verification report."""
        ctx = self._setup_full_workflow()

        signing_key = create_signing_key()
        template = CertificateTemplate.objects.create(
            event=ctx["event"], kind="PARTICIPANT", version=1,
            title="Cert", created_by=ctx["organiser"],
        )
        credential = issue_credential(
            event=ctx["event"],
            user=ctx["participant"],
            kind="PARTICIPANT",
            template=template,
            signing_key=signing_key,
            actor=ctx["organiser"],
        )

        result = verify_credential(credential)
        assert result["signature_valid"] is True
        assert result["key"]["is_trusted"] is True
        assert result["credential_status"]["is_revoked"] is False
        assert result["overall_valid"] is True

    # ----- Test 11: Tampered Payload Detection -----
    def test_tampered_payload_detection(self):
        """Changed signed bytes must fail verification."""
        ctx = self._setup_full_workflow()

        signing_key = create_signing_key()
        template = CertificateTemplate.objects.create(
            event=ctx["event"], kind="PARTICIPANT", version=1,
            title="Cert", created_by=ctx["organiser"],
        )
        credential = issue_credential(
            event=ctx["event"],
            user=ctx["participant"],
            kind="PARTICIPANT",
            template=template,
            signing_key=signing_key,
            actor=ctx["organiser"],
        )

        # Tamper with the payload
        original_payload = json.loads(bytes(credential.payload_bytes).decode("utf-8"))
        original_payload["subject_display_name"] = "TAMPERED NAME"
        tampered_bytes = canonical_json_bytes(original_payload)
        credential.payload_bytes = tampered_bytes
        credential.save()

        result = verify_credential(credential)
        assert result["signature_valid"] is False
        assert result["overall_valid"] is False

    # ----- Test 12: Key Rotation -----
    def test_key_rotation_old_keys_verify(self):
        """Old valid keys survive rotation; credentials verify after key retirement."""
        ctx = self._setup_full_workflow()

        # Issue with key1
        key1 = create_signing_key()
        template = CertificateTemplate.objects.create(
            event=ctx["event"], kind="PARTICIPANT", version=1,
            title="Cert", created_by=ctx["organiser"],
        )
        cred1 = issue_credential(
            event=ctx["event"],
            user=ctx["participant"],
            kind="PARTICIPANT",
            template=template,
            signing_key=key1,
            actor=ctx["organiser"],
        )

        # Retire key1
        retire_signing_key(key1.key_id, ctx["organiser"])
        key1.refresh_from_db()
        assert key1.state == SigningKey.State.RETIRED

        # Old credential still verifies
        result = verify_credential(cred1)
        assert result["signature_valid"] is True
        assert result["key"]["key_state"] == "RETIRED"
        assert result["key"]["is_trusted"] is True  # Retired keys are still trusted
        assert result["overall_valid"] is True

    # ----- Test 13: Key Compromise -----
    def test_key_compromise_distinguished_from_signature_validity(self):
        """Compromised key status is distinguished from mathematical signature validity."""
        ctx = self._setup_full_workflow()

        key = create_signing_key()
        template = CertificateTemplate.objects.create(
            event=ctx["event"], kind="PARTICIPANT", version=1,
            title="Cert", created_by=ctx["organiser"],
        )
        credential = issue_credential(
            event=ctx["event"],
            user=ctx["participant"],
            kind="PARTICIPANT",
            template=template,
            signing_key=key,
            actor=ctx["organiser"],
        )

        # Mark key as compromised
        mark_key_compromised(key.key_id, ctx["organiser"])
        key.refresh_from_db()

        result = verify_credential(credential)
        # Signature is mathematically valid (bytes haven't changed)
        assert result["signature_valid"] is True
        # But key is NOT trusted
        assert result["key"]["key_state"] == "COMPROMISED"
        assert result["key"]["is_trusted"] is False
        # Overall: invalid due to compromised key
        assert result["overall_valid"] is False

    # ----- Test 14: Credential Revocation -----
    def test_credential_revocation(self):
        """Revoked credentials report revocation status."""
        ctx = self._setup_full_workflow()

        key = create_signing_key()
        template = CertificateTemplate.objects.create(
            event=ctx["event"], kind="PARTICIPANT", version=1,
            title="Cert", created_by=ctx["organiser"],
        )
        credential = issue_credential(
            event=ctx["event"],
            user=ctx["participant"],
            kind="PARTICIPANT",
            template=template,
            signing_key=key,
            actor=ctx["organiser"],
        )

        # Revoke
        revoke_credential(credential, ctx["organiser"], "Name correction needed")

        result = verify_credential(credential)
        assert result["signature_valid"] is True
        assert result["credential_status"]["is_revoked"] is True
        assert result["overall_valid"] is False

    # ----- Test 15: Credential Supersession -----
    def test_credential_supersession(self):
        """Superseded credentials point to replacement."""
        ctx = self._setup_full_workflow()

        key = create_signing_key()
        template = CertificateTemplate.objects.create(
            event=ctx["event"], kind="PARTICIPANT", version=1,
            title="Cert v1", created_by=ctx["organiser"],
        )
        cred_old = issue_credential(
            event=ctx["event"],
            user=ctx["participant"],
            kind="PARTICIPANT",
            template=template,
            signing_key=key,
            actor=ctx["organiser"],
        )

        # Issue replacement
        template2 = CertificateTemplate.objects.create(
            event=ctx["event"], kind="PARTICIPANT", version=2,
            title="Cert v2", created_by=ctx["organiser"],
        )
        cred_new = issue_credential(
            event=ctx["event"],
            user=ctx["participant"],
            kind="PARTICIPANT",
            template=template2,
            signing_key=key,
            actor=ctx["organiser"],
        )

        # Supersede old
        supersede_credential(cred_old, cred_new, ctx["organiser"], "Name corrected")

        result = verify_credential(cred_old)
        assert result["credential_status"]["is_superseded"] is True
        assert result["credential_status"]["replacement_id"] == str(cred_new.id)

    # ----- Test 16: Offline Verification -----
    def test_offline_verification(self):
        """Offline verification can prove signature but not revocation status."""
        private_key, public_bytes, key_id = generate_ed25519_keypair()

        payload = canonical_json_bytes({"test": "offline", "schema_version": "1.0"})
        signature = sign_payload(private_key, payload)

        result = verify_credential_offline(payload, signature, public_bytes)
        assert result["signature_valid"] is True
        assert "cannot confirm current revocation status" in result["note"]

    # ----- Test 17: Public Consent -----
    def test_public_consent_management(self):
        """Grant and withdraw public record consent."""
        ctx = self._setup_full_workflow()

        # Grant consent
        consent = grant_public_consent(
            ctx["event"], ctx["participant"], ["display_name"]
        )
        assert consent.is_active is True
        assert "display_name" in consent.allowed_public_fields

        # Withdraw consent
        consent = withdraw_public_consent(ctx["event"], ctx["participant"])
        assert consent.is_active is False

    # ----- Test 18: Downloadable Envelope -----
    def test_downloadable_envelope_format(self):
        """Envelope contains base64url payload, signature, key_id and algorithm."""
        private_key, public_bytes, key_id = generate_ed25519_keypair()

        payload_bytes = canonical_json_bytes({"test": "envelope"})
        signature = sign_payload(private_key, payload_bytes)

        envelope = build_downloadable_envelope(payload_bytes, signature, key_id)

        assert "payload_bytes" in envelope
        assert "signature" in envelope
        assert envelope["key_id"] == key_id
        assert envelope["algorithm"] == "ED25519"

        # Verify the base64url encoded values decode correctly
        decoded_payload = base64.urlsafe_b64decode(envelope["payload_bytes"])
        decoded_sig = base64.urlsafe_b64decode(envelope["signature"])
        assert decoded_payload == payload_bytes
        assert decoded_sig == signature

    # ----- Test 19: Batch Issuance -----
    def test_batch_credential_issuance(self):
        """Batch issuance issues credentials to all eligible recipients."""
        ctx = self._setup_full_workflow()

        issued = issue_credentials_batch(
            event=ctx["event"],
            kind="PARTICIPANT",
            actor=ctx["organiser"],
        )
        assert len(issued) >= 1
        assert issued[0].kind == "PARTICIPANT"
        assert issued[0].display_name_snapshot == "Test Participant"

    # ----- Test 20: Winner Credential with Award Decision -----
    def test_winner_credential_requires_award_decision(self):
        """Winner credentials reference an explicit award decision."""
        ctx = self._setup_full_workflow()

        # Create award decision
        award = create_award_decision(
            event=ctx["event"],
            publication=ctx["publication"],
            prize=ctx["prize"],
            project=ctx["project"],
            actor=ctx["organiser"],
            reason="Best overall project",
        )

        # Issue winner credentials
        issued = issue_credentials_batch(
            event=ctx["event"],
            kind="WINNER",
            actor=ctx["organiser"],
            publication=ctx["publication"],
        )
        assert len(issued) >= 1
        assert issued[0].kind == "WINNER"
        assert issued[0].award_decision == award

    # ----- Test 21: PDF Generation -----
    def test_pdf_generation(self):
        """PDF is generated with correct content."""
        pdf_bytes = generate_certificate_pdf(
            event_name="Test Hackathon",
            recipient_name="John Doe",
            kind="PARTICIPANT",
            contribution_text="Successfully participated",
            issue_date="January 1, 2026",
            credential_id=str(uuid.uuid4()),
            verification_url="http://localhost:8000/verify/test",
        )
        assert len(pdf_bytes) > 0
        assert pdf_bytes[:5] == b"%PDF-"  # Valid PDF header

    # ----- Test 22: API Endpoint - Public Verification -----
    def test_api_public_verification(self):
        """Public verification endpoint works without authentication."""
        ctx = self._setup_full_workflow()

        key = create_signing_key()
        template = CertificateTemplate.objects.create(
            event=ctx["event"], kind="PARTICIPANT", version=1,
            title="Cert", created_by=ctx["organiser"],
        )
        credential = issue_credential(
            event=ctx["event"],
            user=ctx["participant"],
            kind="PARTICIPANT",
            template=template,
            signing_key=key,
            actor=ctx["organiser"],
        )

        # Public API endpoint
        response = self.client.get(f"/api/v1/verify/{credential.id}/")
        assert response.status_code == 200
        data = response.json()
        assert data["verification"]["signature_valid"] is True
        assert data["verification"]["overall_valid"] is True

    # ----- Test 23: API Endpoint - Issuer Keys -----
    def test_api_issuer_keys_public(self):
        """Issuer key metadata is publicly accessible."""
        key = create_signing_key()

        response = self.client.get("/api/v1/issuer/keys/")
        assert response.status_code == 200
        data = response.json()
        assert len(data["keys"]) >= 1
        assert data["keys"][0]["algorithm"] == "ED25519"
        assert "public_key_base64" in data["keys"][0]

    # ----- Test 24: Judge Credential Payload Includes Review Count -----
    def test_judge_credential_includes_review_count(self):
        """Judge credential payload includes completed_review_count."""
        ctx = self._setup_full_workflow()

        key = create_signing_key()
        template = CertificateTemplate.objects.create(
            event=ctx["event"], kind="JUDGE", version=1,
            title="Judge Cert", created_by=ctx["organiser"],
        )
        eligibility = check_judge_eligibility(ctx["event"], ctx["judge_user"])

        credential = issue_credential(
            event=ctx["event"],
            user=ctx["judge_user"],
            kind="JUDGE",
            template=template,
            signing_key=key,
            actor=ctx["organiser"],
            eligibility_snapshot=eligibility,
        )

        payload = json.loads(bytes(credential.payload_bytes).decode("utf-8"))
        assert payload["kind"] == "JUDGE"
        assert payload["completed_review_count"] == 1
        assert "subject_display_name" in payload
        assert payload["subject_display_name"] == "Test Judge"
        # Must not contain private scores or email
        assert "score" not in json.dumps(payload).lower()
        assert "email" not in json.dumps(payload).lower()

    # ----- Test 25: Public-Only Key Cannot Issue -----
    def test_public_only_key_cannot_issue(self):
        """Imported public-only keys cannot issue new credentials."""
        _, public_bytes, key_id = generate_ed25519_keypair()
        key = SigningKey.objects.create(
            key_id=key_id,
            issuer_id="external-issuer",
            public_key_bytes=public_bytes,
            private_key_ref=None,  # No private key
            state=SigningKey.State.ACTIVE,
        )
        assert key.can_issue is False
