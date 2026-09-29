import io
import json
import os
import re
import uuid
import base64
import zipfile
import hashlib
from datetime import timedelta
from typing import Dict, Any, List, Optional, Tuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import User
from apps.events.models import Event, EventMembership, Track, Prize, EmbedConfiguration
from apps.teams.models import Team, TeamMember
from apps.submissions.models import Project, ProjectRevision
from apps.judging.models import (
    Rubric, Criterion, JudgeTrack, JudgeTrackPermission,
    JudgeAssignment, Review, ReviewScore, ReviewRevision,
)
from decimal import Decimal
from apps.results.models import Publication, ResultRun, ResultRow
from apps.credentials.models import (
    CertificateTemplate, SigningKey, AwardDecision,
    IssuedCredential, CredentialStatusEvent, PublicRecordConsent,
)
from apps.audit.models import AuditEvent
from apps.imports.models import PortableImportPlan

MAX_COMPRESSED_SIZE = 200 * 1024 * 1024  # 200 MB
MAX_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024  # 1 GB
MAX_ENTRY_COUNT = 10000

FORMAT_VERSION = "1"
INSTANCE_ID = getattr(settings, "INSTANCE_ID", "dogfood-portal-local")


def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def escape_csv_formula(val: Any) -> str:
    """Protects against CSV formula injection (e.g. =, +, -, @, tab, cr)."""
    if val is None:
        return ""
    text = str(val)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


# ---------------------------------------------------------------------------
# Portable Archive Exporter
# ---------------------------------------------------------------------------

class PortableArchiveExporter:
    """
    Exports a single Event into a standard Portable Archive v1 (zip).
    Layout:
      manifest.json
      records/events.json
      records/users.json (no password hashes)
      records/memberships.json
      records/teams.json
      records/projects.json
      records/project-revisions.json
      records/judging.json
      records/results.json
      records/community.json
      records/credentials.json (public keys only, no private keys)
      records/audit.json
      media/<opaque-storage-key>
    """

    def __init__(self, event: Event, exporting_user: Optional[User] = None):
        self.event = event
        self.exporting_user = exporting_user
        self.file_hashes: Dict[str, str] = {}
        self.entry_counts: Dict[str, int] = {}
        self.referenced_user_ids = set()

    def export_to_bytes(self) -> Tuple[bytes, str]:
        """
        Builds the zip archive in memory and returns (zip_bytes, sha256_hex).
        """
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            # 1. Export Records
            events_data = self._export_events()
            memberships_data = self._export_memberships()
            teams_data = self._export_teams()
            projects_data = self._export_projects()
            revisions_data = self._export_project_revisions()
            judging_data = self._export_judging()
            results_data = self._export_results()
            community_data = self._export_community()
            credentials_data = self._export_credentials()
            audit_data = self._export_audit()
            users_data = self._export_users()  # exports only collected referenced_user_ids

            records = {
                "records/events.json": events_data,
                "records/users.json": users_data,
                "records/memberships.json": memberships_data,
                "records/teams.json": teams_data,
                "records/projects.json": projects_data,
                "records/project-revisions.json": revisions_data,
                "records/judging.json": judging_data,
                "records/results.json": results_data,
                "records/community.json": community_data,
                "records/credentials.json": credentials_data,
                "records/audit.json": audit_data,
            }

            for path, data_obj in records.items():
                content_bytes = json.dumps(data_obj, indent=2, default=str).encode("utf-8")
                zf.writestr(path, content_bytes)
                self.file_hashes[path] = compute_sha256(content_bytes)

            # 2. Export Media Assets
            self._export_media(zf)

            # 3. Build & Write manifest.json
            manifest = {
                "format_version": FORMAT_VERSION,
                "source_instance_id": INSTANCE_ID,
                "event_id": str(self.event.id),
                "event_slug": self.event.slug,
                "entry_counts": self.entry_counts,
                "file_hashes": self.file_hashes,
                "exported_at": timezone.now().isoformat(),
            }
            manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
            zf.writestr("manifest.json", manifest_bytes)

        zip_bytes = bio.getvalue()
        archive_sha256 = compute_sha256(zip_bytes)
        return zip_bytes, archive_sha256

    def _export_events(self) -> List[Dict[str, Any]]:
        ev = self.event
        embed_config = getattr(ev, "embed_configuration", None)
        embed_dict = None
        if embed_config:
            embed_dict = {
                "enabled": embed_config.enabled,
                "allowed_parent_origins": embed_config.allowed_parent_origins_json,
                "theme": embed_config.theme,
                "default_track_id": str(embed_config.default_track_id) if embed_config.default_track_id else None,
                "show_search": embed_config.show_search,
                "version": embed_config.version,
            }

        tracks = [
            {
                "id": str(t.id),
                "slug": t.slug,
                "name": t.name,
                "description_md": t.description_md,
                "display_order": t.display_order,
            }
            for t in ev.tracks.all().order_by("display_order")
        ]

        prizes = [
            {
                "id": str(p.id),
                "track_id": str(p.track_id) if p.track_id else None,
                "name": p.name,
                "description_md": p.description_md,
                "amount_minor": p.amount_minor,
                "currency": p.currency,
                "display_order": p.display_order,
            }
            for p in ev.prizes.all().order_by("display_order")
        ]

        self.entry_counts["events"] = 1
        self.entry_counts["tracks"] = len(tracks)
        self.entry_counts["prizes"] = len(prizes)

        return [
            {
                "id": str(ev.id),
                "slug": ev.slug,
                "name": ev.name,
                "tagline": ev.tagline,
                "description_md": ev.description_md,
                "rules_md": ev.rules_md,
                "cover_asset_id": str(ev.cover_asset_id) if ev.cover_asset_id else None,
                "timezone": ev.timezone,
                "lifecycle": ev.lifecycle,
                "registration_opens_at": ev.registration_opens_at.isoformat() if ev.registration_opens_at else None,
                "registration_closes_at": ev.registration_closes_at.isoformat() if ev.registration_closes_at else None,
                "submissions_opens_at": ev.submissions_opens_at.isoformat() if ev.submissions_opens_at else None,
                "submissions_closes_at": ev.submissions_closes_at.isoformat() if ev.submissions_closes_at else None,
                "judging_opens_at": ev.judging_opens_at.isoformat() if ev.judging_opens_at else None,
                "judging_closes_at": ev.judging_closes_at.isoformat() if ev.judging_closes_at else None,
                "voting_opens_at": ev.voting_opens_at.isoformat() if ev.voting_opens_at else None,
                "voting_closes_at": ev.voting_closes_at.isoformat() if ev.voting_closes_at else None,
                "min_team_size": ev.min_team_size,
                "max_team_size": ev.max_team_size,
                "required_reviews": ev.required_reviews,
                "ranking_scope": ev.ranking_scope,
                "ranking_method": ev.ranking_method,
                "normalisation_lambda": ev.normalisation_lambda,
                "show_public_progress": ev.show_public_progress,
                "active_publication_id": str(ev.active_publication_id) if ev.active_publication_id else None,
                "tracks": tracks,
                "prizes": prizes,
                "embed_configuration": embed_dict,
            }
        ]

    def _export_memberships(self) -> List[Dict[str, Any]]:
        memberships = []
        for m in EventMembership.objects.filter(event=self.event):
            self.referenced_user_ids.add(m.user_id)
            memberships.append({
                "id": str(m.id),
                "user_id": str(m.user_id),
                "role": m.role,
                "status": m.status,
                "joined_at": m.joined_at.isoformat() if m.joined_at else None,
            })
        self.entry_counts["memberships"] = len(memberships)
        return memberships

    def _export_teams(self) -> List[Dict[str, Any]]:
        teams = []
        for t in Team.objects.filter(event=self.event):
            self.referenced_user_ids.add(t.captain_user_id)
            members = []
            for tm in t.members.all():
                self.referenced_user_ids.add(tm.user_id)
                members.append({
                    "id": str(tm.id),
                    "user_id": str(tm.user_id),
                    "joined_at": tm.joined_at.isoformat() if tm.joined_at else None,
                })
            teams.append({
                "id": str(t.id),
                "name": t.name,
                "captain_user_id": str(t.captain_user_id),
                "members": members,
            })
        self.entry_counts["teams"] = len(teams)
        return teams

    def _export_projects(self) -> List[Dict[str, Any]]:
        projects = []
        for p in Project.objects.filter(event=self.event):
            projects.append({
                "id": str(p.id),
                "team_id": str(p.team_id),
                "state": p.state,
                "draft_revision_id": str(p.draft_revision_id) if p.draft_revision_id else None,
                "submitted_revision_id": str(p.submitted_revision_id) if p.submitted_revision_id else None,
                "first_submitted_at": p.first_submitted_at.isoformat() if p.first_submitted_at else None,
                "last_submitted_at": p.last_submitted_at.isoformat() if p.last_submitted_at else None,
                "duplicate_of_id": str(p.duplicate_of_id) if p.duplicate_of_id else None,
                "disposition_reason": p.disposition_reason,
            })
        self.entry_counts["projects"] = len(projects)
        return projects

    def _export_project_revisions(self) -> List[Dict[str, Any]]:
        revisions = []
        for rev in ProjectRevision.objects.filter(project__event=self.event):
            revisions.append({
                "id": str(rev.id),
                "project_id": str(rev.project_id),
                "number": rev.number,
                "title": rev.title,
                "summary": rev.summary,
                "description_md": rev.description_md,
                "track_id": str(rev.track_id) if rev.track_id else None,
                "demo_url": rev.demo_url,
                "repo_url": rev.repo_url,
                "roster_snapshot": rev.roster_snapshot,
                "source": rev.source,
                "created_at": rev.created_at.isoformat() if rev.created_at else None,
            })
        self.entry_counts["project_revisions"] = len(revisions)
        return revisions

    def _export_judging(self) -> Dict[str, Any]:
        rubrics = []
        for r in Rubric.objects.filter(event=self.event):
            criteria = [
                {
                    "id": str(c.id),
                    "key": c.key,
                    "label": c.label,
                    "description_md": c.description_md,
                    "weight": float(c.weight),
                    "display_order": c.display_order,
                }
                for c in r.criteria.all().order_by("display_order")
            ]
            rubrics.append({
                "id": str(r.id),
                "name": r.name,
                "version_number": r.version_number,
                "state": r.state,
                "frozen_at": r.frozen_at.isoformat() if r.frozen_at else None,
                "criteria": criteria,
            })

        assignments = []
        for a in JudgeAssignment.objects.filter(event=self.event):
            self.referenced_user_ids.add(a.judge_membership.user_id)
            review_dict = None
            if hasattr(a, "review"):
                rev = a.review
                scores = [
                    {
                        "id": str(sc.id),
                        "criterion_id": str(sc.criterion_id),
                        "value": sc.value,
                    }
                    for sc in rev.scores.all()
                ]
                review_dict = {
                    "id": str(rev.id),
                    "status": rev.status,
                    "submitted_at": rev.submitted_at.isoformat() if rev.submitted_at else None,
                    "comment": rev.comment,
                    "scores": scores,
                }

            assignments.append({
                "id": str(a.id),
                "judge_user_id": str(a.judge_membership.user_id),
                "project_id": str(a.project_id),
                "project_revision_id": str(a.project_revision_id),
                "rubric_id": str(a.rubric_id),
                "status": a.status,
                "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None,
                "review": review_dict,
            })

        self.entry_counts["rubrics"] = len(rubrics)
        self.entry_counts["assignments"] = len(assignments)

        return {
            "rubrics": rubrics,
            "assignments": assignments,
        }

    def _export_results(self) -> Dict[str, Any]:
        result_runs = []
        for rr in ResultRun.objects.filter(event=self.event):
            rows = [
                {
                    "id": str(row.id),
                    "project_id": str(row.project_id),
                    "cohort_key": row.cohort_key,
                    "eligible": row.eligible,
                    "rank": row.rank,
                    "ranking_value": row.ranking_value,
                }
                for row in rr.rows.all()
            ]
            result_runs.append({
                "id": str(rr.id),
                "source_data_version": rr.source_data_version,
                "algorithm_version": rr.algorithm_version,
                "parameters_json": rr.parameters_json,
                "input_snapshot_json": rr.input_snapshot_json,
                "input_sha256": rr.input_sha256,
                "output_sha256": rr.output_sha256,
                "rows": rows,
            })

        publications = []
        for pub in Publication.objects.filter(event=self.event):
            if pub.published_by_id:
                self.referenced_user_ids.add(pub.published_by_id)
            publications.append({
                "id": str(pub.id),
                "result_run_id": str(pub.result_run_id),
                "number": pub.number,
                "published_at": pub.published_at.isoformat() if pub.published_at else None,
                "published_by_id": str(pub.published_by_id) if pub.published_by_id else None,
                "public_note_md": pub.public_note_md,
            })

        awards = []
        for ad in AwardDecision.objects.filter(publication__event=self.event):
            if ad.decided_by_id:
                self.referenced_user_ids.add(ad.decided_by_id)
            awards.append({
                "id": str(ad.id),
                "publication_id": str(ad.publication_id),
                "prize_id": str(ad.prize_id),
                "project_id": str(ad.project_id),
                "decided_by_id": str(ad.decided_by_id),
                "reason": ad.reason,
                "created_at": ad.created_at.isoformat() if ad.created_at else None,
            })

        self.entry_counts["publications"] = len(publications)
        self.entry_counts["awards"] = len(awards)

        return {
            "result_runs": result_runs,
            "publications": publications,
            "awards": awards,
        }

    def _export_community(self) -> Dict[str, Any]:
        # Comments / vote tallies if any
        return {"comments": [], "votes": []}

    def _export_credentials(self) -> Dict[str, Any]:
        templates = [
            {
                "id": str(t.id),
                "title": t.title,
                "subtitle": t.subtitle,
                "body_text": t.body_text,
                "footer_text": t.footer_text,
                "kind": t.kind,
                "version": t.version,
                "layout_version": t.layout_version,
            }
            for t in CertificateTemplate.objects.filter(event=self.event)
        ]

        # Collect signing keys referenced by issued credentials
        issued_creds = IssuedCredential.objects.filter(event=self.event).select_related("signing_key")
        signing_key_ids = set()
        issued = []
        for cred in issued_creds:
            self.referenced_user_ids.add(cred.subject_user_id)
            signing_key_ids.add(cred.signing_key_id)
            issued.append({
                "id": str(cred.id),
                "subject_user_id": str(cred.subject_user_id),
                "kind": cred.kind,
                "template_id": str(cred.template_id) if cred.template_id else None,
                "template_version": cred.template_version,
                "publication_id": str(cred.publication_id) if cred.publication_id else None,
                "award_decision_id": str(cred.award_decision_id) if cred.award_decision_id else None,
                "eligibility_snapshot": cred.eligibility_snapshot,
                "display_name_snapshot": cred.display_name_snapshot,
                "payload_bytes_b64": base64.b64encode(cred.payload_bytes).decode("ascii"),
                "signature_bytes_b64": base64.b64encode(cred.signature_bytes).decode("ascii"),
                "signing_key_id": cred.signing_key.key_id,
                "pdf_storage_key": cred.pdf_storage_key,
                "pdf_sha256": cred.pdf_sha256,
                "issued_at": cred.issued_at.isoformat() if cred.issued_at else None,
            })

        # Signing keys: export ONLY public key bytes! NEVER export private keys!
        keys = [
            {
                "id": str(k.id),
                "key_id": k.key_id,
                "issuer_id": k.issuer_id,
                "algorithm": k.algorithm,
                "public_key_bytes_b64": base64.b64encode(k.public_key_bytes).decode("ascii"),
                "state": k.state,
                "created_at": k.created_at.isoformat() if k.created_at else None,
            }
            for k in SigningKey.objects.filter(id__in=signing_key_ids)
        ]

        status_events = []
        for ev in CredentialStatusEvent.objects.filter(credential__event=self.event):
            if ev.actor_user_id:
                self.referenced_user_ids.add(ev.actor_user_id)
            status_events.append({
                "id": str(ev.id),
                "credential_id": str(ev.credential_id),
                "state": ev.state,
                "reason": ev.reason,
                "replacement_id": str(ev.replacement_id) if ev.replacement_id else None,
                "actor_user_id": str(ev.actor_user_id) if ev.actor_user_id else None,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            })

        consents = []
        for c in PublicRecordConsent.objects.filter(event=self.event):
            self.referenced_user_ids.add(c.subject_user_id)
            consents.append({
                "id": str(c.id),
                "subject_user_id": str(c.subject_user_id),
                "allowed_public_fields": c.allowed_public_fields,
                "granted_at": c.granted_at.isoformat() if c.granted_at else None,
                "withdrawn_at": c.withdrawn_at.isoformat() if c.withdrawn_at else None,
            })

        self.entry_counts["templates"] = len(templates)
        self.entry_counts["signing_keys"] = len(keys)
        self.entry_counts["issued_credentials"] = len(issued)

        return {
            "templates": templates,
            "signing_keys": keys,
            "issued_credentials": issued,
            "status_events": status_events,
            "consents": consents,
        }

    def _export_audit(self) -> List[Dict[str, Any]]:
        audit_events = []
        for ae in AuditEvent.objects.filter(event=self.event).order_by("created_at"):
            if ae.actor_user_id:
                self.referenced_user_ids.add(ae.actor_user_id)
            audit_events.append({
                "id": str(ae.id),
                "action": ae.action,
                "actor_kind": ae.actor_kind,
                "actor_user_id": str(ae.actor_user_id) if ae.actor_user_id else None,
                "entity_type": ae.entity_type,
                "entity_id": str(ae.entity_id) if ae.entity_id else None,
                "before_json": ae.before_json,
                "after_json": ae.after_json,
                "reason": ae.reason,
                "created_at": ae.created_at.isoformat() if ae.created_at else None,
            })
        self.entry_counts["audit_events"] = len(audit_events)
        return audit_events

    def _export_users(self) -> List[Dict[str, Any]]:
        users = []
        for u in User.objects.filter(id__in=self.referenced_user_ids):
            # Authorised identity fields ONLY - STRICTLY NO PASSWORDS OR SENSITIVE TOKENS
            users.append({
                "id": str(u.id),
                "email": u.email,
                "display_name": u.display_name,
                "created_at": u.created_at.isoformat() if hasattr(u, "created_at") and u.created_at else None,
            })
        self.entry_counts["users"] = len(users)
        return users

    def _export_media(self, zf: zipfile.ZipFile):
        media_count = 0
        media_root = getattr(settings, "MEDIA_ROOT", None)
        if media_root and os.path.exists(media_root):
            for root, dirs, files in os.walk(media_root):
                for f in files:
                    full_p = os.path.join(root, f)
                    rel_p = os.path.relpath(full_p, media_root)
                    archive_p = f"media/{rel_p}"
                    with open(full_p, "rb") as mf:
                        m_bytes = mf.read()
                    zf.writestr(archive_p, m_bytes)
                    self.file_hashes[archive_p] = compute_sha256(m_bytes)
                    media_count += 1
        self.entry_counts["media"] = media_count


# ---------------------------------------------------------------------------
# Portable Archive Importer
# ---------------------------------------------------------------------------

class PortableArchiveImporter:
    """
    Validates and imports Portable Archive v1 archives.
    Enforces safe extraction, checksum verification, dry-run previews,
    immutable plan apply, and remapped identifiers.
    """

    def __init__(self, archive_bytes: bytes, operator_user: Optional[User] = None):
        self.archive_bytes = archive_bytes
        self.operator_user = operator_user
        self.archive_sha256 = compute_sha256(archive_bytes)
        self.manifest: Dict[str, Any] = {}
        self.records: Dict[str, Any] = {}
        self.media_files: Dict[str, bytes] = {}

    def validate_and_create_plan(self) -> PortableImportPlan:
        """
        Step 1: Dry-run validation. Reads archive without database modifications,
        computes dry-run preview, and saves PortableImportPlan.
        """
        # 1. Size and entry count limit checks
        if len(self.archive_bytes) > MAX_COMPRESSED_SIZE:
            raise ValidationError(f"Archive compressed size exceeds maximum limit of {MAX_COMPRESSED_SIZE} bytes.")

        bio = io.BytesIO(self.archive_bytes)
        try:
            zf = zipfile.ZipFile(bio, mode="r")
        except zipfile.BadZipFile:
            raise ValidationError("File is not a valid zip archive.")

        entries = zf.infolist()
        if len(entries) > MAX_ENTRY_COUNT:
            raise ValidationError(f"Entry count {len(entries)} exceeds maximum limit of {MAX_ENTRY_COUNT}.")

        total_uncompressed = sum(e.file_size for e in entries)
        if total_uncompressed > MAX_UNCOMPRESSED_SIZE:
            raise ValidationError(f"Uncompressed archive size {total_uncompressed} bytes exceeds limit of {MAX_UNCOMPRESSED_SIZE} bytes.")

        # 2. Path safety checks
        seen_names = set()
        for e in entries:
            name = e.filename
            if name in seen_names:
                raise ValidationError(f"Duplicate filename detected in archive: {name}")
            seen_names.add(name)

            # Check absolute path or drive letter
            if name.startswith("/") or name.startswith("\\") or re.match(r"^[a-zA-Z]:", name):
                raise ValidationError(f"Absolute or drive path detected: {name}")

            # Check directory traversal
            parts = re.split(r"[/\\]", name)
            if ".." in parts or "." in parts:
                raise ValidationError(f"Path traversal detected: {name}")

            # Check symlink
            mode = (e.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValidationError(f"Symlink detected in archive: {name}")

            # Disallow nested archives (but allow legitimate media blobs whose
            # original filenames happen to carry an archive extension)
            lower = name.lower()
            is_media_blob = lower.startswith("media/")
            if not is_media_blob and any(lower.endswith(ext) for ext in [".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar"]):
                raise ValidationError(f"Nested archive rejected: {name}")

        # 3. Read & validate manifest.json
        if "manifest.json" not in seen_names:
            raise ValidationError("Archive missing required manifest.json.")

        manifest_raw = zf.read("manifest.json")
        try:
            self.manifest = json.loads(manifest_raw.decode("utf-8"))
        except Exception:
            raise ValidationError("manifest.json is not valid JSON.")

        if str(self.manifest.get("format_version")) != FORMAT_VERSION:
            raise ValidationError(f"Unsupported format version '{self.manifest.get('format_version')}'. Expected '{FORMAT_VERSION}'.")

        source_instance_id = self.manifest.get("source_instance_id") or "unknown-instance"
        file_hashes = self.manifest.get("file_hashes", {})

        # 4. Verify checksum of every entry against manifest
        for name in seen_names:
            if name == "manifest.json":
                continue
            data = zf.read(name)
            computed_h = compute_sha256(data)
            expected_h = file_hashes.get(name)
            if not expected_h:
                raise ValidationError(f"File '{name}' in archive is not declared in manifest.json file_hashes.")
            if computed_h != expected_h:
                raise ValidationError(f"Integrity check failed: SHA-256 mismatch for '{name}'.")

            if name.startswith("records/"):
                try:
                    self.records[name] = json.loads(data.decode("utf-8"))
                except Exception:
                    raise ValidationError(f"Record file '{name}' is not valid JSON.")
            elif name.startswith("media/"):
                self.media_files[name] = data

        # Check all manifest files exist in zip
        for declared_path in file_hashes.keys():
            if declared_path not in seen_names:
                raise ValidationError(f"Declared file '{declared_path}' missing from archive.")

        # 5. Schema verification for essential records
        events_data = self.records.get("records/events.json")
        if not events_data or not isinstance(events_data, list) or len(events_data) == 0:
            raise ValidationError("records/events.json must contain at least one event.")

        users_data = self.records.get("records/users.json", [])
        for u in users_data:
            if "password" in u or "password_hash" in u:
                raise ValidationError("Malicious input: records/users.json must never contain password hashes.")

        # 6. Build Dry-Run Preview
        event_info = events_data[0]
        original_slug = event_info.get("slug", "event")
        proposed_slug = self._determine_import_slug(original_slug)

        preview = {
            "source_instance_id": source_instance_id,
            "format_version": FORMAT_VERSION,
            "original_event_slug": original_slug,
            "proposed_event_slug": proposed_slug,
            "original_event_name": event_info.get("name"),
            "counts": {
                "events": len(events_data),
                "tracks": len(event_info.get("tracks", [])),
                "prizes": len(event_info.get("prizes", [])),
                "users": len(users_data),
                "memberships": len(self.records.get("records/memberships.json", [])),
                "teams": len(self.records.get("records/teams.json", [])),
                "projects": len(self.records.get("records/projects.json", [])),
                "project_revisions": len(self.records.get("records/project-revisions.json", [])),
                "rubrics": len(self.records.get("records/judging.json", {}).get("rubrics", [])),
                "assignments": len(self.records.get("records/judging.json", {}).get("assignments", [])),
                "publications": len(self.records.get("records/results.json", {}).get("publications", [])),
                "awards": len(self.records.get("records/results.json", {}).get("awards", [])),
                "credentials": len(self.records.get("records/credentials.json", {}).get("issued_credentials", [])),
                "media_files": len(self.media_files),
            },
            "warnings": [
                "All imported user accounts will have unusable passwords set until ownership is explicitly established.",
                "Imported webhooks and background jobs are disabled by default.",
                "No external email notifications or credential issuances will be triggered upon import.",
            ],
            "target_lifecycle": "DRAFT",
        }

        # Check existing plan with same archive hash
        existing_plan = PortableImportPlan.objects.filter(
            source_instance_id=source_instance_id,
            archive_sha256=self.archive_sha256,
        ).first()

        if existing_plan:
            existing_plan.preview_json = preview
            existing_plan.expires_at = timezone.now() + timedelta(hours=24)
            existing_plan.save()
            return existing_plan

        plan = PortableImportPlan.objects.create(
            archive_sha256=self.archive_sha256,
            source_instance_id=source_instance_id,
            format_version=FORMAT_VERSION,
            preview_json=preview,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        return plan

    def apply_plan(self, plan: PortableImportPlan) -> Event:
        """
        Step 2: Applies a validated, unexpired plan. Creates a new DRAFT event
        with remapped IDs and provenance.
        """
        if plan.applied_at:
            raise ValidationError("This import plan has already been applied.")

        if plan.expires_at <= timezone.now():
            raise ValidationError("This import plan has expired. Please run a new preview.")

        if plan.archive_sha256 != self.archive_sha256:
            raise ValidationError("Archive bytes do not match the validated import plan SHA-256.")

        # Check uniqueness constraint: (source_instance_id, archive_sha256) when applied
        already_applied = PortableImportPlan.objects.filter(
            source_instance_id=plan.source_instance_id,
            archive_sha256=plan.archive_sha256,
            applied_at__isnull=False,
        ).first()
        if already_applied:
            raise ValidationError(f"Archive from source '{plan.source_instance_id}' has already been successfully applied to event {already_applied.target_event_id}.")

        # Re-load records if not populated
        if not self.records:
            bio = io.BytesIO(self.archive_bytes)
            with zipfile.ZipFile(bio, mode="r") as zf:
                for name in zf.namelist():
                    if name.startswith("records/"):
                        self.records[name] = json.loads(zf.read(name).decode("utf-8"))
                    elif name.startswith("media/"):
                        self.media_files[name] = zf.read(name)

        id_map: Dict[str, uuid.UUID] = {}

        with transaction.atomic():
            # 1. Event creation in DRAFT state
            events_data = self.records["records/events.json"]
            ev_data = events_data[0]
            new_slug = self._determine_import_slug(ev_data["slug"])

            new_event = Event.objects.create(
                slug=new_slug,
                name=f"{ev_data['name']} (Imported)",
                tagline=ev_data.get("tagline", ""),
                description_md=ev_data.get("description_md", ""),
                rules_md=ev_data.get("rules_md", ""),
                timezone=ev_data.get("timezone", "Australia/Melbourne"),
                lifecycle=Event.Lifecycle.DRAFT,
                registration_opens_at=parse_datetime(ev_data["registration_opens_at"]) or timezone.now(),
                registration_closes_at=parse_datetime(ev_data["registration_closes_at"]) or timezone.now() + timedelta(days=7),
                submissions_opens_at=parse_datetime(ev_data["submissions_opens_at"]) or timezone.now(),
                submissions_closes_at=parse_datetime(ev_data["submissions_closes_at"]) or timezone.now() + timedelta(days=14),
                judging_opens_at=parse_datetime(ev_data["judging_opens_at"]) or timezone.now() + timedelta(days=14),
                judging_closes_at=parse_datetime(ev_data["judging_closes_at"]) or timezone.now() + timedelta(days=21),
                voting_opens_at=parse_datetime(ev_data["voting_opens_at"]) if ev_data.get("voting_opens_at") else None,
                voting_closes_at=parse_datetime(ev_data["voting_closes_at"]) if ev_data.get("voting_closes_at") else None,
                min_team_size=ev_data.get("min_team_size", 1),
                max_team_size=ev_data.get("max_team_size", 4),
                required_reviews=ev_data.get("required_reviews", 3),
                ranking_scope=ev_data.get("ranking_scope", Event.RankingScope.EVENT),
                ranking_method=ev_data.get("ranking_method", Event.RankingMethod.RAW_WEIGHTED_V1),
                normalisation_lambda=ev_data.get("normalisation_lambda", 5.0),
                show_public_progress=ev_data.get("show_public_progress", False),
            )
            id_map[ev_data["id"]] = new_event.id

            # Embed Configuration
            embed_cfg = ev_data.get("embed_configuration")
            if embed_cfg:
                EmbedConfiguration.objects.create(
                    event=new_event,
                    enabled=embed_cfg.get("enabled", True),
                    allowed_parent_origins_json=embed_cfg.get("allowed_parent_origins", []),
                    theme=embed_cfg.get("theme", "AUTO"),
                    show_search=embed_cfg.get("show_search", True),
                )

            # Tracks
            for t_data in ev_data.get("tracks", []):
                t = Track.objects.create(
                    event=new_event,
                    slug=t_data["slug"],
                    name=t_data["name"],
                    description_md=t_data.get("description_md", ""),
                    display_order=t_data.get("display_order", 0),
                )
                id_map[t_data["id"]] = t.id

            # Prizes
            for p_data in ev_data.get("prizes", []):
                track_id = id_map.get(p_data.get("track_id")) if p_data.get("track_id") else None
                p = Prize.objects.create(
                    event=new_event,
                    track_id=track_id,
                    name=p_data["name"],
                    description_md=p_data.get("description_md", ""),
                    amount_minor=p_data.get("amount_minor"),
                    currency=p_data.get("currency"),
                    display_order=p_data.get("display_order", 0),
                )
                id_map[p_data["id"]] = p.id

            # 2. Users (set unusable passwords, never overwrite local accounts)
            users_data = self.records.get("records/users.json", [])
            for u_data in users_data:
                orig_id = u_data["id"]
                email = u_data["email"].strip().lower()
                existing_user = User.objects.filter(email=email).first()
                if existing_user:
                    id_map[orig_id] = existing_user.id
                else:
                    new_u = User.objects.create_user(
                        email=email,
                        display_name=u_data.get("display_name", email.split("@")[0]),
                    )
                    new_u.set_unusable_password()
                    new_u.save()
                    id_map[orig_id] = new_u.id

            # 3. Memberships
            memberships_data = self.records.get("records/memberships.json", [])
            for m_data in memberships_data:
                user_id = id_map.get(m_data["user_id"])
                if user_id:
                    EventMembership.objects.get_or_create(
                        event=new_event,
                        user_id=user_id,
                        defaults={
                            "role": m_data["role"],
                            "status": m_data.get("status", EventMembership.Status.ACTIVE),
                            "joined_at": parse_datetime(m_data["joined_at"]) if m_data.get("joined_at") else timezone.now(),
                        },
                    )

            # 4. Teams & Members
            teams_data = self.records.get("records/teams.json", [])
            for t_data in teams_data:
                captain_id = id_map.get(t_data["captain_user_id"])
                if not captain_id:
                    continue
                team = Team.objects.create(
                    event=new_event,
                    name=t_data["name"],
                    captain_user_id=captain_id,
                )
                id_map[t_data["id"]] = team.id

                for tm_data in t_data.get("members", []):
                    m_user_id = id_map.get(tm_data["user_id"])
                    if m_user_id:
                        TeamMember.objects.create(
                            team=team,
                            user_id=m_user_id,
                            event=new_event,
                            joined_at=parse_datetime(tm_data["joined_at"]) if tm_data.get("joined_at") else timezone.now(),
                        )

            # 5. Projects & Revisions
            projects_data = self.records.get("records/projects.json", [])
            revisions_data = self.records.get("records/project-revisions.json", [])

            # Create project stubs first
            for p_data in projects_data:
                mapped_team_id = id_map.get(p_data["team_id"])
                if not mapped_team_id:
                    continue
                proj = Project.objects.create(
                    event=new_event,
                    team_id=mapped_team_id,
                    state=p_data.get("state", Project.State.DRAFT),
                    first_submitted_at=parse_datetime(p_data["first_submitted_at"]) if p_data.get("first_submitted_at") else None,
                    last_submitted_at=parse_datetime(p_data["last_submitted_at"]) if p_data.get("last_submitted_at") else None,
                    disposition_reason=p_data.get("disposition_reason", ""),
                )
                id_map[p_data["id"]] = proj.id

            # Create revisions
            for rev_data in revisions_data:
                mapped_proj_id = id_map.get(rev_data["project_id"])
                if not mapped_proj_id:
                    continue
                mapped_track_id = id_map.get(rev_data.get("track_id")) if rev_data.get("track_id") else None

                rev = ProjectRevision.objects.create(
                    project_id=mapped_proj_id,
                    number=rev_data.get("number", 1),
                    title=rev_data.get("title", "Untitled Project"),
                    summary=rev_data.get("summary", ""),
                    description_md=rev_data.get("description_md", ""),
                    track_id=mapped_track_id,
                    demo_url=rev_data.get("demo_url", ""),
                    repo_url=rev_data.get("repo_url", ""),
                    roster_snapshot=rev_data.get("roster_snapshot", []),
                    source=ProjectRevision.Source.PORTABLE_IMPORT,
                )
                id_map[rev_data["id"]] = rev.id

            # Connect revisions & duplicates back to projects
            for p_data in projects_data:
                proj_id = id_map.get(p_data["id"])
                if not proj_id:
                    continue
                proj = Project.objects.get(id=proj_id)
                if p_data.get("submitted_revision_id"):
                    proj.submitted_revision_id = id_map.get(p_data["submitted_revision_id"])
                if p_data.get("draft_revision_id"):
                    proj.draft_revision_id = id_map.get(p_data["draft_revision_id"])
                if p_data.get("duplicate_of_id"):
                    proj.duplicate_of_id = id_map.get(p_data["duplicate_of_id"])
                proj.save()

            # 6. Rubrics & Criteria
            judging_data = self.records.get("records/judging.json", {})
            for r_data in judging_data.get("rubrics", []):
                r = Rubric.objects.create(
                    event=new_event,
                    name=r_data["name"],
                    version_number=r_data.get("version_number", 1),
                    state=r_data.get("state", Rubric.State.FROZEN),
                    frozen_at=parse_datetime(r_data["frozen_at"]) if r_data.get("frozen_at") else timezone.now(),
                )
                id_map[r_data["id"]] = r.id
                for c_data in r_data.get("criteria", []):
                    c = Criterion.objects.create(
                        rubric=r,
                        key=c_data.get("key", f"crit_{uuid.uuid4().hex[:6]}"),
                        label=c_data.get("label", c_data.get("title", "Criterion")),
                        description_md=c_data.get("description_md", ""),
                        weight=Decimal(str(c_data.get("weight", "1.0"))),
                        display_order=c_data.get("display_order", 0),
                    )
                    id_map[c_data["id"]] = c.id

            # 7. Assignments & Reviews
            for a_data in judging_data.get("assignments", []):
                judge_id = id_map.get(a_data["judge_user_id"])
                proj_id = id_map.get(a_data["project_id"])
                if not judge_id or not proj_id:
                    continue

                # Ensure judge has EventMembership
                judge_mem = EventMembership.objects.filter(event=new_event, user_id=judge_id).first()
                if not judge_mem:
                    judge_mem = EventMembership.objects.create(
                        event=new_event,
                        user_id=judge_id,
                        role=EventMembership.Role.JUDGE,
                    )

                proj = Project.objects.get(id=proj_id)
                rev_id = proj.submitted_revision_id or proj.draft_revision_id
                rubric_obj = Rubric.objects.filter(event=new_event).first()

                assign = JudgeAssignment.objects.create(
                    event=new_event,
                    judge_membership=judge_mem,
                    project=proj,
                    project_revision_id=rev_id,
                    rubric=rubric_obj,
                    status=a_data.get("status", a_data.get("state", JudgeAssignment.Status.ACTIVE)),
                    assigned_at=parse_datetime(a_data["assigned_at"]) if a_data.get("assigned_at") else timezone.now(),
                )
                id_map[a_data["id"]] = assign.id

                rev_data = a_data.get("review")
                if rev_data:
                    review = Review.objects.create(
                        assignment=assign,
                        status=rev_data.get("status", rev_data.get("state", Review.Status.DRAFT)),
                        submitted_at=parse_datetime(rev_data["submitted_at"]) if rev_data.get("submitted_at") else None,
                        comment=rev_data.get("comment", rev_data.get("feedback_for_team", "")),
                    )
                    id_map[rev_data["id"]] = review.id
                    for s_data in rev_data.get("scores", []):
                        crit_id = id_map.get(s_data["criterion_id"])
                        if crit_id:
                            ReviewScore.objects.create(
                                review=review,
                                criterion_id=crit_id,
                                value=s_data.get("value", s_data.get("score", 5)),
                            )

            # 8. Publications & Award Decisions
            results_data = self.records.get("records/results.json", {})
            created_result_run = None
            for rr_data in results_data.get("result_runs", []):
                rr = ResultRun.objects.create(
                    event=new_event,
                    source_data_version=rr_data.get("source_data_version", 1),
                    algorithm_version=rr_data.get("algorithm_version", "RAW_WEIGHTED_V1"),
                    parameters_json=rr_data.get("parameters_json", {}),
                    input_snapshot_json=rr_data.get("input_snapshot_json", {}),
                    input_sha256=rr_data.get("input_sha256", "import-sha"),
                    output_sha256=rr_data.get("output_sha256", "import-sha"),
                )
                id_map[rr_data["id"]] = rr.id
                created_result_run = rr
                for row_data in rr_data.get("rows", []):
                    proj_id = id_map.get(row_data["project_id"])
                    if proj_id:
                        ResultRow.objects.create(
                            result_run=rr,
                            project_id=proj_id,
                            cohort_key=row_data.get("cohort_key", "EVENT"),
                            eligible=row_data.get("eligible", True),
                            rank=row_data.get("rank"),
                            ranking_value=row_data.get("ranking_value"),
                        )

            for pub_data in results_data.get("publications", []):
                pub_by_id = id_map.get(pub_data.get("published_by_id"))
                rr_id = id_map.get(pub_data.get("result_run_id"))
                if not rr_id:
                    if not created_result_run:
                        created_result_run = ResultRun.objects.create(
                            event=new_event,
                            source_data_version=1,
                            algorithm_version="RAW_WEIGHTED_V1",
                            input_snapshot_json={},
                            input_sha256="import-sha",
                            output_sha256="import-sha",
                        )
                    rr_id = created_result_run.id

                pub = Publication.objects.create(
                    event=new_event,
                    result_run_id=rr_id,
                    number=pub_data.get("number", 1),
                    published_at=parse_datetime(pub_data["published_at"]) if pub_data.get("published_at") else timezone.now(),
                    published_by_id=pub_by_id,
                    public_note_md=pub_data.get("public_note_md", ""),
                )
                id_map[pub_data["id"]] = pub.id
                if ev_data.get("active_publication_id") == pub_data["id"]:
                    new_event.active_publication_id = pub.id
                    new_event.save(update_fields=["active_publication_id"])

            for aw_data in results_data.get("awards", []):
                pub_id = id_map.get(aw_data["publication_id"])
                prz_id = id_map.get(aw_data["prize_id"])
                prj_id = id_map.get(aw_data["project_id"])
                dec_by_id = id_map.get(aw_data["decided_by_id"])
                if pub_id and prz_id and prj_id and dec_by_id:
                    AwardDecision.objects.create(
                        publication_id=pub_id,
                        prize_id=prz_id,
                        project_id=prj_id,
                        decided_by_id=dec_by_id,
                        reason=aw_data.get("reason", ""),
                        created_at=parse_datetime(aw_data["created_at"]) if aw_data.get("created_at") else timezone.now(),
                    )

            # 9. Credentials (templates, foreign public keys, issued credentials)
            creds_data = self.records.get("records/credentials.json", {})
            for tpl_data in creds_data.get("templates", []):
                t = CertificateTemplate.objects.create(
                    event=new_event,
                    title=tpl_data.get("title", "Certificate"),
                    subtitle=tpl_data.get("subtitle", ""),
                    body_text=tpl_data.get("body_text", ""),
                    footer_text=tpl_data.get("footer_text", ""),
                    kind=tpl_data["kind"],
                    version=tpl_data.get("version", 1),
                    layout_version=tpl_data.get("layout_version", "v1"),
                )
                id_map[tpl_data["id"]] = t.id

            # Import signing keys as retired/public-only keys (cannot sign new local credentials)
            key_map: Dict[str, SigningKey] = {}
            for k_data in creds_data.get("signing_keys", []):
                k_obj, _ = SigningKey.objects.get_or_create(
                    key_id=k_data["key_id"],
                    defaults={
                        "issuer_id": k_data.get("issuer_id", "foreign-issuer"),
                        "algorithm": k_data.get("algorithm", "ED25519"),
                        "public_key_bytes": base64.b64decode(k_data["public_key_bytes_b64"]),
                        "private_key_ref": None,  # No private key!
                        "state": SigningKey.State.RETIRED,
                        "retired_at": timezone.now(),
                    },
                )
                key_map[k_data["key_id"]] = k_obj

            for ic_data in creds_data.get("issued_credentials", []):
                subj_id = id_map.get(ic_data["subject_user_id"])
                if not subj_id:
                    continue
                pub_id = id_map.get(ic_data.get("publication_id")) if ic_data.get("publication_id") else None
                tpl_id = id_map.get(ic_data.get("template_id")) if ic_data.get("template_id") else None
                award_dec_id = id_map.get(ic_data.get("award_decision_id")) if ic_data.get("award_decision_id") else None
                key_obj = key_map.get(ic_data.get("signing_key_id"))
                if not key_obj:
                    continue

                raw_payload = base64.b64decode(ic_data["payload_bytes_b64"])
                raw_sig = base64.b64decode(ic_data["signature_bytes_b64"])

                cred = IssuedCredential.objects.create(
                    event=new_event,
                    subject_user_id=subj_id,
                    kind=ic_data["kind"],
                    template_id=tpl_id,
                    template_version=ic_data.get("template_version", 1),
                    publication_id=pub_id,
                    award_decision_id=award_dec_id,
                    eligibility_snapshot=ic_data.get("eligibility_snapshot", {}),
                    display_name_snapshot=ic_data.get("display_name_snapshot", ""),
                    payload_bytes=raw_payload,
                    signature_bytes=raw_sig,
                    signing_key=key_obj,
                    pdf_storage_key=ic_data.get("pdf_storage_key"),
                    pdf_sha256=ic_data.get("pdf_sha256"),
                    issued_at=parse_datetime(ic_data["issued_at"]) if ic_data.get("issued_at") else timezone.now(),
                )
                id_map[ic_data["id"]] = cred.id

            # 10. Extract media assets to media directory
            media_root = getattr(settings, "MEDIA_ROOT", None)
            if media_root:
                os.makedirs(media_root, exist_ok=True)
                for archive_path, file_data in self.media_files.items():
                    rel_dest = archive_path.removeprefix("media/").lstrip("/")
                    dest_full = os.path.join(media_root, rel_dest)
                    os.makedirs(os.path.dirname(dest_full), exist_ok=True)
                    with open(dest_full, "wb") as f:
                        f.write(file_data)

            # 11. Finalize plan
            plan.applied_at = timezone.now()
            plan.target_event_id = new_event.id
            plan.save()

            # 12. Audit event
            AuditEvent.objects.create(
                event=new_event,
                actor_user=self.operator_user,
                actor_kind=AuditEvent.ActorKind.USER if self.operator_user else AuditEvent.ActorKind.SYSTEM,
                action="PORTABLE_IMPORT_APPLIED",
                entity_type="Event",
                entity_id=new_event.id,
                after_json={
                    "plan_id": str(plan.id),
                    "archive_sha256": plan.archive_sha256,
                    "source_instance_id": plan.source_instance_id,
                    "target_event_slug": new_event.slug,
                },
                reason="Organiser applied portable archive import",
            )

        return new_event

    def _determine_import_slug(self, base_slug: str) -> str:
        candidate = f"{base_slug}-imported"
        if not Event.objects.filter(slug=candidate).exists():
            return candidate
        suffix = uuid.uuid4().hex[:6]
        return f"{base_slug}-imported-{suffix}"
