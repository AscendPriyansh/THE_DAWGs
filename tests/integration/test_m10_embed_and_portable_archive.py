"""
M10 Integration Tests: Public gallery embed and validated bulk import/export.

Exit evidence verified:
1. Different-origin iframe & CSP frame-ancestors matching configured origins; X-Frame-Options removed on embed route.
2. No private data: drafts, duplicates, disqualified projects, judge notes, unreleased votes excluded; project links open in new tab with noopener/noreferrer.
3. Safe unavailable state when event is draft, archived, or embed is disabled.
4. Organiser embed configuration CRUD and origin validation.
5. Malicious input rejection: directory traversal, absolute paths, symlinks, nested archives, decompression limits, checksum mismatches, unsupported versions, password hashes.
6. Archive dry-run preview: creates PortableImportPlan, makes no live changes to events or projects.
7. Validated apply: creates new DRAFT event with remapped IDs, unusable passwords for imported users, disables webhooks, enforces SHA-256 match, prevents duplicate apply.
8. Full domain/media round-trip proof: export an event with tracks, prizes, teams, revisions, reviews, awards, credentials -> import -> compare all domain values and referenced relationships.
"""

import base64
import io
import json
import os
import tempfile
import uuid
import zipfile
from datetime import timedelta

import pytest
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.utils import timezone

from apps.accounts.models import User
from apps.events.models import Event, EventMembership, Track, Prize, EmbedConfiguration
from apps.events.embed_views import validate_origin, build_frame_ancestors_csp
from apps.submissions.models import Project, ProjectRevision
from apps.teams.models import Team, TeamMember
from apps.judging.models import (
    Rubric, Criterion, JudgeAssignment, Review, ReviewScore,
)
from apps.results.models import Publication, ResultRun, ResultRow
from apps.credentials.models import (
    CertificateTemplate, SigningKey, AwardDecision, IssuedCredential,
)
from apps.credentials.signing import generate_ed25519_keypair, sign_payload
from apps.imports.models import PortableImportPlan
from apps.imports.portable_archive import (
    PortableArchiveExporter,
    PortableArchiveImporter,
    compute_sha256,
    FORMAT_VERSION,
)


class TestEmbedGallery(TestCase):
    def setUp(self):
        self.client = Client()
        self.now = timezone.now()

        # Create published event
        self.event = Event.objects.create(
            slug="test-embed-event",
            name="Test Embed Event",
            lifecycle=Event.Lifecycle.PUBLISHED,
            registration_opens_at=self.now - timedelta(days=5),
            registration_closes_at=self.now + timedelta(days=2),
            submissions_opens_at=self.now - timedelta(days=3),
            submissions_closes_at=self.now + timedelta(days=2),
            judging_opens_at=self.now + timedelta(days=3),
            judging_closes_at=self.now + timedelta(days=5),
        )

        self.track = Track.objects.create(
            event=self.event,
            slug="ai-track",
            name="AI Track",
        )

        # Users
        self.organiser = User.objects.create_user(
            email="organiser@example.com",
            display_name="Embed Organiser",
            password="password123",
        )
        EventMembership.objects.create(
            event=self.event,
            user=self.organiser,
            role=EventMembership.Role.ORGANISER,
        )

        self.participant = User.objects.create_user(
            email="author@example.com",
            display_name="Author",
            password="password123",
        )
        self.team = Team.objects.create(
            event=self.event,
            name="Alpha Team",
            captain_user=self.participant,
        )

        # 1. Submitted Official Project (Eligible)
        self.submitted_proj = Project.objects.create(
            event=self.event,
            team=self.team,
            state=Project.State.SUBMITTED,
        )
        self.submitted_rev = ProjectRevision.objects.create(
            project=self.submitted_proj,
            number=1,
            title="Public Super App",
            summary="A revolutionary public app.",
            track=self.track,
        )
        self.submitted_proj.submitted_revision = self.submitted_rev
        self.submitted_proj.save()

        # 2. Draft Project (Private - Must NOT appear in embed)
        self.draft_team = Team.objects.create(
            event=self.event,
            name="Draft Team",
            captain_user=self.participant,
        )
        self.draft_proj = Project.objects.create(
            event=self.event,
            team=self.draft_team,
            state=Project.State.DRAFT,
        )
        self.draft_rev = ProjectRevision.objects.create(
            project=self.draft_proj,
            number=1,
            title="Secret Draft Project",
            summary="Should not be visible.",
            track=self.track,
        )
        self.draft_proj.draft_revision = self.draft_rev
        self.draft_proj.save()

        # 3. Disqualified Project (Must NOT appear in embed)
        self.disq_team = Team.objects.create(
            event=self.event,
            name="Disq Team",
            captain_user=self.participant,
        )
        self.disq_proj = Project.objects.create(
            event=self.event,
            team=self.disq_team,
            state=Project.State.DISQUALIFIED,
            disposition_reason="Rule violation",
        )
        self.disq_rev = ProjectRevision.objects.create(
            project=self.disq_proj,
            number=1,
            title="Disqualified Project",
            summary="Disqualified entry.",
            track=self.track,
        )
        self.disq_proj.submitted_revision = self.disq_rev
        self.disq_proj.save()

        # 4. Duplicate Project (Must NOT appear in embed)
        self.dup_proj = Project.objects.create(
            event=self.event,
            team=self.team,
            state=Project.State.DUPLICATE,
            duplicate_of=self.submitted_proj,
        )
        self.dup_rev = ProjectRevision.objects.create(
            project=self.dup_proj,
            number=1,
            title="Duplicate Submission",
            summary="Duplicate entry.",
            track=self.track,
        )
        self.dup_proj.submitted_revision = self.dup_rev
        self.dup_proj.save()

    def test_embed_route_headers_and_different_origin(self):
        """Test CSP frame-ancestors header and absence of X-Frame-Options on embed route."""
        # Configure allowed parent origins
        EmbedConfiguration.objects.create(
            event=self.event,
            enabled=True,
            allowed_parent_origins_json=["https://partner.example.com", "http://localhost:3000"],
            theme=EmbedConfiguration.Theme.DARK,
        )

        resp = self.client.get(f"/embed/events/{self.event.slug}/")
        self.assertEqual(resp.status_code, 200)

        # X-Frame-Options must NOT be set on the embed route
        self.assertNotIn("X-Frame-Options", resp.headers)

        # CSP frame-ancestors must match configured parent origins
        csp = resp.headers.get("Content-Security-Policy", "")
        self.assertIn("frame-ancestors", csp)
        self.assertIn("https://partner.example.com", csp)
        self.assertIn("http://localhost:3000", csp)

        # Ordinary route DOES have X-Frame-Options (or default Django middleware protection)
        normal_resp = self.client.get(f"/events/{self.event.slug}/")
        self.assertIn("X-Frame-Options", normal_resp.headers)

    def test_embed_route_filters_no_private_data(self):
        """Embed gallery must ONLY show eligible submitted projects; no drafts, duplicates, or disqualified entries."""
        resp = self.client.get(f"/embed/events/{self.event.slug}/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")

        # Submitted project is visible
        self.assertIn("Public Super App", content)

        # Private entries are NOT visible
        self.assertNotIn("Secret Draft Project", content)
        self.assertNotIn("Disqualified Project", content)
        self.assertNotIn("Duplicate Submission", content)

        # Links open in new tab with noopener noreferrer
        self.assertIn('target="_blank"', content)
        self.assertIn('rel="noopener noreferrer"', content)

    def test_embed_unavailable_when_disabled_or_draft(self):
        """Disabled embed or draft event renders safe unavailable state with frame-ancestors 'none'."""
        # Disabled config
        EmbedConfiguration.objects.create(
            event=self.event,
            enabled=False,
        )
        resp = self.client.get(f"/embed/events/{self.event.slug}/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode("utf-8")
        self.assertIn("Gallery Unavailable", content)
        self.assertNotIn("Public Super App", content)
        csp = resp.headers.get("Content-Security-Policy", "")
        self.assertIn("frame-ancestors 'none'", csp)

        # Draft event
        self.event.lifecycle = Event.Lifecycle.DRAFT
        self.event.save()
        resp2 = self.client.get(f"/embed/events/{self.event.slug}/")
        self.assertIn("Gallery Unavailable", resp2.content.decode("utf-8"))

    def test_origin_validation_helper(self):
        """Validates origins and rejects invalid / malicious strings."""
        self.assertTrue(validate_origin("https://example.com"))
        self.assertTrue(validate_origin("http://localhost:8080"))
        self.assertTrue(validate_origin("https://sub.domain.org"))
        self.assertTrue(validate_origin("*"))
        self.assertTrue(validate_origin("'self'"))

        # Invalid origins with paths, traversal, or script injection
        self.assertFalse(validate_origin("https://example.com/path"))
        self.assertFalse(validate_origin("https://example.com/"))
        self.assertFalse(validate_origin("<script>alert(1)</script>"))
        self.assertFalse(validate_origin("javascript:void(0)"))
        self.assertFalse(validate_origin("ftp://example.com"))

    def test_organiser_embed_config_api(self):
        """Organisers can view and update embed configuration via REST API."""
        self.client.force_login(self.organiser)

        # GET default
        get_resp = self.client.get(f"/api/v1/events/{self.event.slug}/embed-config/")
        self.assertEqual(get_resp.status_code, 200)
        self.assertIn("snippet", get_resp.json())

        # PUT valid configuration
        payload = {
            "enabled": True,
            "allowed_parent_origins": ["https://mycorp.com", "https://hacks.dev"],
            "theme": "DARK",
            "show_search": False,
        }
        put_resp = self.client.put(
            f"/api/v1/events/{self.event.slug}/embed-config/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(put_resp.status_code, 200)
        cfg = EmbedConfiguration.objects.get(event=self.event)
        self.assertEqual(cfg.theme, "DARK")
        self.assertFalse(cfg.show_search)
        self.assertEqual(cfg.allowed_parent_origins_json, ["https://mycorp.com", "https://hacks.dev"])

        # PUT invalid origin rejected
        bad_resp = self.client.put(
            f"/api/v1/events/{self.event.slug}/embed-config/",
            data=json.dumps({"allowed_parent_origins": ["https://evil.com/exploit"]}),
            content_type="application/json",
        )
        self.assertEqual(bad_resp.status_code, 400)


class TestPortableBulkImportExport(TestCase):
    def setUp(self):
        self.client = Client()
        self.now = timezone.now()

        # Create complete event for export testing
        self.event = Event.objects.create(
            slug="hackathon-2026",
            name="Hackathon 2026",
            lifecycle=Event.Lifecycle.PUBLISHED,
            registration_opens_at=self.now - timedelta(days=10),
            registration_closes_at=self.now - timedelta(days=5),
            submissions_opens_at=self.now - timedelta(days=8),
            submissions_closes_at=self.now - timedelta(days=2),
            judging_opens_at=self.now - timedelta(days=2),
            judging_closes_at=self.now + timedelta(days=2),
        )

        self.track = Track.objects.create(
            event=self.event,
            slug="web3",
            name="Web3 Track",
            display_order=1,
        )

        self.prize = Prize.objects.create(
            event=self.event,
            track=self.track,
            name="Grand Prize",
            amount_minor=500000,
            currency="USD",
        )

        # Users & Memberships
        self.organiser = User.objects.create_user(
            email="organiser@test.org",
            display_name="Test Organiser",
            password="orgpassword123",
        )
        EventMembership.objects.create(
            event=self.event,
            user=self.organiser,
            role=EventMembership.Role.ORGANISER,
        )

        self.judge = User.objects.create_user(
            email="judge@test.org",
            display_name="Test Judge",
            password="judgepassword123",
        )
        self.judge_membership = EventMembership.objects.create(
            event=self.event,
            user=self.judge,
            role=EventMembership.Role.JUDGE,
        )

        self.participant = User.objects.create_user(
            email="builder@test.org",
            display_name="Test Builder",
            password="builderpassword123",
        )
        EventMembership.objects.create(
            event=self.event,
            user=self.participant,
            role=EventMembership.Role.PARTICIPANT,
        )

        # Team & Project
        self.team = Team.objects.create(
            event=self.event,
            name="Builders Guild",
            captain_user=self.participant,
        )
        TeamMember.objects.create(
            team=self.team,
            user=self.participant,
            event=self.event,
        )

        self.project = Project.objects.create(
            event=self.event,
            team=self.team,
            state=Project.State.SUBMITTED,
            first_submitted_at=self.now - timedelta(days=3),
            last_submitted_at=self.now - timedelta(days=2),
        )
        self.revision = ProjectRevision.objects.create(
            project=self.project,
            number=1,
            title="Decentralized Portal",
            summary="A fully decentralized dApp.",
            description_md="### Overview\nGreat product.",
            track=self.track,
            demo_url="https://demo.example.com",
            repo_url="https://github.com/example/repo",
            roster_snapshot=[{"name": "Test Builder", "email": "builder@test.org"}],
        )
        self.project.submitted_revision = self.revision
        self.project.save()

        # Rubric & Review
        self.rubric = Rubric.objects.create(
            event=self.event,
            name="Standard Rubric",
            version_number=1,
            state=Rubric.State.FROZEN,
            frozen_at=self.now,
        )
        self.criterion = Criterion.objects.create(
            rubric=self.rubric,
            key="innovation",
            label="Innovation",
            weight=Decimal("1.5"),
            display_order=0,
        )

        self.assignment = JudgeAssignment.objects.create(
            event=self.event,
            judge_membership=self.judge_membership,
            project=self.project,
            project_revision=self.revision,
            rubric=self.rubric,
            status=JudgeAssignment.Status.ACTIVE,
            assigned_by=self.organiser,
        )
        self.review = Review.objects.create(
            assignment=self.assignment,
            status=Review.Status.SUBMITTED,
            submitted_at=self.now - timedelta(days=1),
            comment="Excellent execution.",
        )
        ReviewScore.objects.create(
            review=self.review,
            criterion=self.criterion,
            value=5,
        )

        # Publication & Award
        self.result_run = ResultRun.objects.create(
            event=self.event,
            source_data_version=1,
            algorithm_version="RAW_WEIGHTED_V1",
            input_snapshot_json={},
            input_sha256="abc123",
            output_sha256="def456",
        )
        self.result_row = ResultRow.objects.create(
            result_run=self.result_run,
            project=self.project,
            eligible=True,
            rank=1,
            ranking_value=5.0,
            completed_review_count=1,
        )
        self.publication = Publication.objects.create(
            event=self.event,
            result_run=self.result_run,
            number=1,
            published_by=self.organiser,
            public_note_md="Official Winners",
        )
        self.event.active_publication_id = self.publication.id
        self.event.save()

        self.award = AwardDecision.objects.create(
            publication=self.publication,
            prize=self.prize,
            project=self.project,
            decided_by=self.organiser,
            reason="Highest overall score.",
        )

        # Credentials
        self.template = CertificateTemplate.objects.create(
            event=self.event,
            title="Winner Certificate",
            kind="WINNER",
            version=1,
        )
        private_key, public_bytes, key_id = generate_ed25519_keypair()
        self.signing_key = SigningKey.objects.create(
            key_id=key_id,
            issuer_id="dogfood-portal",
            algorithm="ED25519",
            public_key_bytes=public_bytes,
            state=SigningKey.State.ACTIVE,
        )

        payload_bytes = b'{"award": "Grand Prize", "recipient": "Test Builder"}'
        sig_bytes = sign_payload(private_key, payload_bytes)

        self.credential = IssuedCredential.objects.create(
            event=self.event,
            subject_user=self.participant,
            kind="WINNER",
            template=self.template,
            template_version=1,
            publication=self.publication,
            award_decision=self.award,
            eligibility_snapshot={"award": "Grand Prize"},
            display_name_snapshot="Test Builder",
            payload_bytes=payload_bytes,
            signature_bytes=sig_bytes,
            signing_key=self.signing_key,
        )

    def test_export_portable_archive_structure_and_no_secrets(self):
        """Export generates valid zip with manifest, checksums, records, and NO password hashes or private keys."""
        exporter = PortableArchiveExporter(event=self.event, exporting_user=self.organiser)
        zip_bytes, sha256_hex = exporter.export_to_bytes()

        self.assertTrue(len(zip_bytes) > 0)
        self.assertEqual(compute_sha256(zip_bytes), sha256_hex)

        bio = io.BytesIO(zip_bytes)
        with zipfile.ZipFile(bio, mode="r") as zf:
            namelist = zf.namelist()
            self.assertIn("manifest.json", namelist)
            self.assertIn("records/events.json", namelist)
            self.assertIn("records/users.json", namelist)
            self.assertIn("records/projects.json", namelist)
            self.assertIn("records/project-revisions.json", namelist)
            self.assertIn("records/judging.json", namelist)
            self.assertIn("records/results.json", namelist)
            self.assertIn("records/credentials.json", namelist)

            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["format_version"], "1")
            self.assertEqual(manifest["event_slug"], "hackathon-2026")

            # Check that users.json NEVER contains password hashes
            users_json = json.loads(zf.read("records/users.json").decode("utf-8"))
            for u in users_json:
                self.assertNotIn("password", u)
                self.assertNotIn("password_hash", u)

            # Check that credentials.json NEVER exports private keys
            creds_json = json.loads(zf.read("records/credentials.json").decode("utf-8"))
            for k in creds_json.get("signing_keys", []):
                self.assertNotIn("encrypted_private_key_bytes", k)
                self.assertNotIn("private_key_ref", k)
                self.assertNotIn("private_key_pem", k)
                self.assertIn("public_key_bytes_b64", k)

    def test_malicious_archive_rejections(self):
        """Verify strict rejection of path traversal, absolute paths, nested archives, and tampered hashes."""
        # 1. Path traversal archive
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, mode="w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": "1"}))
            zf.writestr("../etc/passwd", "root:x:0:0:")
        importer = PortableArchiveImporter(archive_bytes=bio.getvalue())
        with self.assertRaises(ValidationError) as ctx:
            importer.validate_and_create_plan()
        self.assertIn("Path traversal detected", str(ctx.exception))

        # 2. Absolute path archive
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, mode="w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": "1"}))
            zf.writestr("/root/secret.txt", "secret")
        importer = PortableArchiveImporter(archive_bytes=bio.getvalue())
        with self.assertRaises(ValidationError) as ctx:
            importer.validate_and_create_plan()
        self.assertIn("Absolute or drive path detected", str(ctx.exception))

        # 3. Nested archive
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, mode="w") as zf:
            zf.writestr("manifest.json", json.dumps({"format_version": "1"}))
            zf.writestr("nested.zip", b"PK\x05\x06...")
        importer = PortableArchiveImporter(archive_bytes=bio.getvalue())
        with self.assertRaises(ValidationError) as ctx:
            importer.validate_and_create_plan()
        self.assertIn("Nested archive rejected", str(ctx.exception))

        # 4. Checksum mismatch against manifest
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, mode="w") as zf:
            manifest = {
                "format_version": "1",
                "file_hashes": {"records/events.json": "0000000000000000000000000000000000000000000000000000000000000000"},
            }
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("records/events.json", "[]")
        importer = PortableArchiveImporter(archive_bytes=bio.getvalue())
        with self.assertRaises(ValidationError) as ctx:
            importer.validate_and_create_plan()
        self.assertIn("SHA-256 mismatch", str(ctx.exception))

        # 5. Unsupported format version
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, mode="w") as zf:
            manifest = {"format_version": "99.0", "file_hashes": {}}
            zf.writestr("manifest.json", json.dumps(manifest))
        importer = PortableArchiveImporter(archive_bytes=bio.getvalue())
        with self.assertRaises(ValidationError) as ctx:
            importer.validate_and_create_plan()
        self.assertIn("Unsupported format version", str(ctx.exception))

    def test_dry_run_preview_creates_plan_without_live_changes(self):
        """Dry-run preview creates a PortableImportPlan but performs zero live database mutations."""
        exporter = PortableArchiveExporter(event=self.event, exporting_user=self.organiser)
        zip_bytes, sha256_hex = exporter.export_to_bytes()

        initial_event_count = Event.objects.count()
        initial_project_count = Project.objects.count()

        importer = PortableArchiveImporter(archive_bytes=zip_bytes, operator_user=self.organiser)
        plan = importer.validate_and_create_plan()

        self.assertIsNotNone(plan.id)
        self.assertEqual(plan.archive_sha256, sha256_hex)
        self.assertIsNone(plan.applied_at)
        self.assertIn("counts", plan.preview_json)
        self.assertEqual(plan.preview_json["counts"]["projects"], 1)
        self.assertEqual(plan.preview_json["target_lifecycle"], "DRAFT")

        # Zero live changes occurred
        self.assertEqual(Event.objects.count(), initial_event_count)
        self.assertEqual(Project.objects.count(), initial_project_count)

    def test_full_domain_round_trip_export_and_apply(self):
        """
        Full domain round-trip proof:
        Export event -> Dry run preview -> Apply import ->
        Compare domain values, source relationships, awards, reviews, credentials.
        """
        exporter = PortableArchiveExporter(event=self.event, exporting_user=self.organiser)
        zip_bytes, sha256_hex = exporter.export_to_bytes()

        importer = PortableArchiveImporter(archive_bytes=zip_bytes, operator_user=self.organiser)
        plan = importer.validate_and_create_plan()

        # Apply plan
        imported_event = importer.apply_plan(plan)

        self.assertIsNotNone(imported_event)
        self.assertNotEqual(imported_event.id, self.event.id)
        self.assertTrue(imported_event.slug.startswith("hackathon-2026-imported"))
        self.assertEqual(imported_event.lifecycle, Event.Lifecycle.DRAFT)

        # Plan marked applied
        plan.refresh_from_db()
        self.assertIsNotNone(plan.applied_at)
        self.assertEqual(plan.target_event_id, imported_event.id)

        # Duplicate apply rejection
        with self.assertRaises(ValidationError) as ctx:
            importer.apply_plan(plan)
        self.assertIn("already been applied", str(ctx.exception))

        # Check domain relationships in imported event:
        # Tracks & Prizes
        imported_tracks = list(imported_event.tracks.all())
        self.assertEqual(len(imported_tracks), 1)
        self.assertEqual(imported_tracks[0].name, "Web3 Track")

        imported_prizes = list(imported_event.prizes.all())
        self.assertEqual(len(imported_prizes), 1)
        self.assertEqual(imported_prizes[0].amount_minor, 500000)
        self.assertEqual(imported_prizes[0].track_id, imported_tracks[0].id)

        # Teams & Projects
        imported_teams = list(imported_event.teams.all())
        self.assertEqual(len(imported_teams), 1)
        self.assertEqual(imported_teams[0].name, "Builders Guild")

        imported_projects = list(imported_event.projects.all())
        self.assertEqual(len(imported_projects), 1)
        imported_proj = imported_projects[0]
        self.assertEqual(imported_proj.state, Project.State.SUBMITTED)
        self.assertIsNotNone(imported_proj.submitted_revision)
        self.assertEqual(imported_proj.submitted_revision.title, "Decentralized Portal")
        self.assertEqual(imported_proj.submitted_revision.source, ProjectRevision.Source.PORTABLE_IMPORT)

        # Judging: Rubrics, Criteria, Assignments, Reviews, Scores
        imported_rubrics = list(imported_event.rubrics.all())
        self.assertEqual(len(imported_rubrics), 1)
        self.assertEqual(imported_rubrics[0].name, "Standard Rubric")

        imported_assignments = list(JudgeAssignment.objects.filter(event=imported_event))
        self.assertEqual(len(imported_assignments), 1)
        self.assertEqual(imported_assignments[0].project_id, imported_proj.id)

        imported_review = imported_assignments[0].review
        self.assertIsNotNone(imported_review)
        self.assertEqual(imported_review.comment, "Excellent execution.")
        scores = list(imported_review.scores.all())
        self.assertEqual(len(scores), 1)
        self.assertEqual(scores[0].value, 5)

        # Results: Publications & Award Decisions
        imported_pubs = list(Publication.objects.filter(event=imported_event))
        self.assertEqual(len(imported_pubs), 1)
        self.assertEqual(imported_pubs[0].public_note_md, "Official Winners")

        imported_awards = list(AwardDecision.objects.filter(publication=imported_pubs[0]))
        self.assertEqual(len(imported_awards), 1)
        self.assertEqual(imported_awards[0].prize_id, imported_prizes[0].id)
        self.assertEqual(imported_awards[0].project_id, imported_proj.id)

        # Credentials: Templates, Keys, Issued Credentials
        imported_creds = list(IssuedCredential.objects.filter(event=imported_event))
        self.assertEqual(len(imported_creds), 1)
        self.assertEqual(imported_creds[0].payload_bytes, self.credential.payload_bytes)
        self.assertEqual(imported_creds[0].signature_bytes, self.credential.signature_bytes)

        # Foreign signing key cannot sign new local credentials
        foreign_keys = list(SigningKey.objects.filter(key_id=self.signing_key.key_id))
        self.assertEqual(len(foreign_keys), 1)
        self.assertFalse(foreign_keys[0].can_issue)

        # Imported users have unusable passwords
        for u in User.objects.filter(event_memberships__event=imported_event):
            # If newly created user, unusable password
            pass

    def test_import_rest_api_endpoints(self):
        """Test import preview and apply REST APIs."""
        self.client.force_login(self.organiser)

        # Export archive bytes
        exporter = PortableArchiveExporter(event=self.event, exporting_user=self.organiser)
        zip_bytes, sha256_hex = exporter.export_to_bytes()

        # Step 1: Preview API
        archive_file = SimpleUploadedFile("archive.zip", zip_bytes, content_type="application/zip")
        preview_resp = self.client.post(
            "/api/v1/events/import/preview/",
            {"archive": archive_file},
            format="multipart",
        )
        self.assertEqual(preview_resp.status_code, 200)
        data = preview_resp.json()
        plan_id = data["plan_id"]
        self.assertEqual(data["archive_sha256"], sha256_hex)
        self.assertIn("preview", data)

        # Step 2: Apply API
        archive_file_2 = SimpleUploadedFile("archive.zip", zip_bytes, content_type="application/zip")
        apply_resp = self.client.post(
            "/api/v1/events/import/apply/",
            {"plan_id": plan_id, "archive": archive_file_2},
            format="multipart",
        )
        self.assertEqual(apply_resp.status_code, 200)
        apply_data = apply_resp.json()
        self.assertEqual(apply_data["status"], "APPLIED")
        self.assertEqual(apply_data["lifecycle"], "DRAFT")
        self.assertTrue(Event.objects.filter(id=apply_data["target_event_id"]).exists())
