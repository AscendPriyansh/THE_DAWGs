import hashlib
import json
import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import User
from apps.events.models import Event, EventMembership, Track, Prize
from apps.teams.models import Team, TeamMember
from apps.submissions.models import Project, ProjectRevision
from apps.judging.models import (
    Rubric, Criterion, JudgeTrack, JudgeTrackPermission,
    JudgeAssignment, Review, ReviewScore, ReviewRevision,
)
from apps.audit.models import AuditEvent
from apps.imports.models import ImportBatch, ExternalRecord

logger = logging.getLogger(__name__)


def compute_file_sha256(file_path):
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class FixtureImporter:
    NAMESPACE = "dogfood-supplied-2026"

    def __init__(self, fixture_path, operator_user=None):
        self.fixture_path = fixture_path
        self.operator_user = operator_user
        self.report = {}

    def import_fixture(self, force=False):
        file_sha256 = compute_file_sha256(self.fixture_path)
        existing = ImportBatch.objects.filter(
            namespace=self.NAMESPACE, file_sha256=file_sha256, status=ImportBatch.Status.APPLIED
        ).first()

        if existing and not force:
            return {
                "status": "NOOP",
                "message": "Fixture already imported with identical SHA-256",
                "batch_id": str(existing.id),
                "report": existing.report_json,
            }

        with open(self.fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.validate_payload(data)

        with transaction.atomic():
            batch = ImportBatch.objects.create(
                namespace=self.NAMESPACE,
                format_version="1.0",
                file_sha256=file_sha256,
                status=ImportBatch.Status.VALIDATED,
                created_by=self.operator_user,
            )

            # 1. Event
            event, event_ext = self._import_event(data["event"], batch)

            # 2. Tracks
            tracks_map = self._import_tracks(event, data["tracks"], batch)

            # 3. Rubric & Criteria
            rubric, criteria_map = self._import_rubric(event, batch)

            # 4. Judges
            judges_map = self._import_judges(event, data["judges"], tracks_map, batch)

            # 5. Teams & Participants
            teams_map = self._import_teams(event, data["teams"], batch)

            # 6. Projects & Revisions (including prj_41 duplicate disposition)
            projects_map = self._import_projects(
                event, data["projects"], teams_map, tracks_map, batch
            )

            # 7. Scores -> JudgeAssignments, Reviews, ReviewScores, ReviewRevisions
            scores_stats = self._import_scores(
                event, data["scores"], judges_map, projects_map, rubric, criteria_map, batch
            )

            # 8. Live Demo Event (separate labelled event with open window)
            demo_event = self._create_demo_event_if_missing(batch)

            self.report.update({
                "file_sha256": file_sha256,
                "event_id": str(event.id),
                "event_slug": event.slug,
                "tracks_count": len(tracks_map),
                "judges_count": len(judges_map),
                "teams_count": len(teams_map),
                "projects_count": len(projects_map),
                "scores_stats": scores_stats,
                "duplicate_disposition": {
                    "project_id": "prj_41",
                    "duplicate_of": "prj_07",
                    "status": "DUPLICATE",
                    "reason": "Preserved duplicate repository of prj_07 and tm_07",
                },
                "demo_event_slug": demo_event.slug,
                "assignment_coverage_diagnostic": "UNKNOWN",
                "imported_at": timezone.now().isoformat(),
            })

            batch.status = ImportBatch.Status.APPLIED
            batch.report_json = self.report
            batch.save()

            AuditEvent.objects.create(
                event=event,
                actor_user=self.operator_user,
                actor_kind=AuditEvent.ActorKind.IMPORT,
                action="FIXTURE_IMPORT_APPLIED",
                entity_type="ImportBatch",
                entity_id=batch.id,
                after_json=self.report,
                reason="Faithful import of supplied fixture dataset",
            )

        return {
            "status": "APPLIED",
            "batch_id": str(batch.id),
            "report": self.report,
        }

    def validate_payload(self, data):
        for req_key in ["event", "tracks", "judges", "teams", "projects", "scores"]:
            if req_key not in data:
                raise ValueError(f"Missing required key in fixture: {req_key}")

        # Check unique IDs in tracks, judges, teams, projects
        for coll, id_field in [
            (data["tracks"], "id"),
            (data["judges"], "id"),
            (data["teams"], "id"),
            (data["projects"], "id"),
        ]:
            ids = [item[id_field] for item in coll]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate IDs detected in {coll}")

        # Verify scores criteria ranges
        for score in data["scores"]:
            for crit_key, val in score.get("criteria", {}).items():
                if crit_key not in ("functionality", "quality", "innovation"):
                    raise ValueError(f"Unknown criterion key: {crit_key}")
                if not isinstance(val, int) or val < 1 or val > 5:
                    raise ValueError(f"Score {val} outside 1-5 range for {crit_key}")

    def _import_event(self, event_data, batch):
        raw_close = event_data.get("submissions_close", "2026-03-01T18:00:00Z")
        submissions_close = parse_datetime(raw_close)

        # Labelled import defaults (historical, closed event)
        submissions_open = parse_datetime("2026-02-20T00:00:00Z")
        reg_open = parse_datetime("2026-02-15T00:00:00Z")
        reg_close = submissions_close
        judging_open = submissions_close
        judging_close = parse_datetime("2026-03-05T18:00:00Z")

        slug = "sample-hack-2026"
        event, created = Event.objects.get_or_create(
            slug=slug,
            defaults={
                "name": event_data.get("name", "Sample Hack 2026"),
                "tagline": "The official DOGFOOD 2026 benchmark event",
                "description_md": (
                    "# Sample Hack 2026\n\n"
                    "Welcome to the official Sample Hackathon. "
                    "41 projects submitted across 8 specialised tracks.\n\n"
                    "## Rules\n"
                    "- Develop locally, submit repository links and documentation.\n"
                    "- Strict deadline enforcement at close.\n"
                    "- Anonymous, multi-criteria private judging.\n"
                ),
                "rules_md": "Submissions close strictly at the deadline. No late submissions accepted.",
                "lifecycle": Event.Lifecycle.PUBLISHED,
                "registration_opens_at": reg_open,
                "registration_closes_at": reg_close,
                "submissions_opens_at": submissions_open,
                "submissions_closes_at": submissions_close,
                "judging_opens_at": judging_open,
                "judging_closes_at": judging_close,
                "min_team_size": 1,
                "max_team_size": 5,
                "required_reviews": 3,
                "ranking_scope": Event.RankingScope.EVENT,
                "ranking_method": Event.RankingMethod.RAW_WEIGHTED_V1,
                "normalisation_lambda": 5.0,
            },
        )

        ext = ExternalRecord.objects.create(
            namespace=self.NAMESPACE,
            entity_type="Event",
            external_id=event_data["id"],
            internal_id=event.id,
            original_payload_json=event_data,
            import_batch=batch,
        )
        return event, ext

    def _import_tracks(self, event, tracks_data, batch):
        tracks_map = {}
        for idx, trk in enumerate(tracks_data):
            slug = trk["id"].lower()
            track, _ = Track.objects.get_or_create(
                event=event,
                slug=slug,
                defaults={
                    "name": trk["name"],
                    "description_md": f"Track for {trk['name']}",
                    "display_order": idx,
                },
            )
            tracks_map[trk["id"]] = track
            ExternalRecord.objects.create(
                namespace=self.NAMESPACE,
                entity_type="Track",
                external_id=trk["id"],
                internal_id=track.id,
                original_payload_json=trk,
                import_batch=batch,
            )
        return tracks_map

    def _import_rubric(self, event, batch):
        rubric, _ = Rubric.objects.get_or_create(
            event=event,
            version_number=1,
            defaults={
                "name": "Standard Hackathon 3-Criteria Rubric",
                "state": Rubric.State.FROZEN,
                "frozen_at": event.judging_opens_at,
            },
        )
        criteria_defs = [
            ("functionality", "Functionality", "Does the project work as advertised and run cleanly?", Decimal("1.0000"), 1),
            ("quality", "Code & Design Quality", "Is the implementation well-engineered, robust, and clean?", Decimal("1.0000"), 2),
            ("innovation", "Innovation & Impact", "How novel, creative, and impactful is the solution?", Decimal("1.0000"), 3),
        ]
        criteria_map = {}
        for key, label, desc, weight, order in criteria_defs:
            crit, _ = Criterion.objects.get_or_create(
                rubric=rubric,
                key=key,
                defaults={
                    "label": label,
                    "description_md": desc,
                    "weight": weight,
                    "display_order": order,
                },
            )
            criteria_map[key] = crit
        return rubric, criteria_map

    def _import_judges(self, event, judges_data, tracks_map, batch):
        judges_map = {}
        for j_data in judges_data:
            email = j_data["email"].strip().lower()
            user, _ = User.objects.get_or_create(
                email=email,
                defaults={"display_name": j_data["name"]},
            )
            if not user.password:
                user.set_unusable_password()
                user.save()

            membership, _ = EventMembership.objects.get_or_create(
                event=event,
                user=user,
                defaults={"role": EventMembership.Role.JUDGE, "status": EventMembership.Status.ACTIVE},
            )

            # Expertise tags and Track Permissions
            for trk_id in j_data.get("tracks", []):
                track = tracks_map.get(trk_id)
                if track:
                    JudgeTrack.objects.get_or_create(membership=membership, track=track)
                    JudgeTrackPermission.objects.get_or_create(
                        membership=membership,
                        track=track,
                        defaults={"reason": "Imported declared track scope"},
                    )

            judges_map[j_data["id"]] = {
                "user": user,
                "membership": membership,
                "tracks": j_data.get("tracks", []),
            }

            ExternalRecord.objects.create(
                namespace=self.NAMESPACE,
                entity_type="Judge",
                external_id=j_data["id"],
                internal_id=membership.id,
                original_payload_json=j_data,
                import_batch=batch,
            )
        return judges_map

    def _import_teams(self, event, teams_data, batch):
        teams_map = {}
        for tm in teams_data:
            members = tm.get("members", [])
            captain_user = None
            team_members = []

            for idx, email_raw in enumerate(members):
                email = email_raw.strip().lower()
                disp_name = email.split("@")[0].replace(".", " ").title()
                u, _ = User.objects.get_or_create(
                    email=email,
                    defaults={"display_name": disp_name},
                )
                if not u.password:
                    u.set_unusable_password()
                    u.save()

                EventMembership.objects.get_or_create(
                    event=event,
                    user=u,
                    defaults={
                        "role": EventMembership.Role.PARTICIPANT,
                        "status": EventMembership.Status.ACTIVE,
                    },
                )
                if idx == 0:
                    captain_user = u
                team_members.append(u)

            team = Team.objects.create(
                event=event,
                name=tm["name"],
                captain_user=captain_user,
                status=Team.Status.ACTIVE,
            )

            for u in team_members:
                TeamMember.objects.get_or_create(
                    event=event,
                    team=team,
                    user=u,
                )

            teams_map[tm["id"]] = team
            ExternalRecord.objects.create(
                namespace=self.NAMESPACE,
                entity_type="Team",
                external_id=tm["id"],
                internal_id=team.id,
                original_payload_json=tm,
                import_batch=batch,
            )
        return teams_map

    def _import_projects(self, event, projects_data, teams_map, tracks_map, batch):
        projects_map = {}

        # First pass: create all projects and revisions
        for p in projects_data:
            p_id = p["id"]
            team = teams_map[p["team"]]
            track = tracks_map[p["track"]]
            submitted_at = parse_datetime(p["submitted_at"])

            roster_snapshot = [
                {"user_id": str(m.user.id), "display_name": m.user.display_name}
                for m in team.members.select_related("user")
            ]

            is_duplicate = (p_id == "prj_41")
            initial_state = Project.State.DUPLICATE if is_duplicate else Project.State.SUBMITTED

            project = Project.objects.create(
                event=event,
                team=team,
                state=initial_state,
                first_submitted_at=submitted_at,
                last_submitted_at=submitted_at,
                disposition_reason="Preserved duplicate entry: matches repository and team of prj_07" if is_duplicate else "",
            )

            revision = ProjectRevision.objects.create(
                project=project,
                number=1,
                track=track,
                title=p["title"],
                summary=p["summary"],
                description_md=f"# {p['title']}\n\n{p['summary']}\n\nRepository: {p.get('repo_url', 'N/A')}",
                repo_url=p.get("repo_url"),
                roster_snapshot=roster_snapshot,
                source=ProjectRevision.Source.FIXTURE_IMPORT,
                created_at=submitted_at,
            )

            project.submitted_revision = revision
            project.save(update_fields=["submitted_revision"])

            projects_map[p_id] = project

            ExternalRecord.objects.create(
                namespace=self.NAMESPACE,
                entity_type="Project",
                external_id=p_id,
                internal_id=project.id,
                original_payload_json=p,
                import_batch=batch,
            )

        # Second pass: link duplicate prj_41 -> prj_07
        if "prj_41" in projects_map and "prj_07" in projects_map:
            p41 = projects_map["prj_41"]
            p07 = projects_map["prj_07"]
            p41.duplicate_of = p07
            p41.save(update_fields=["duplicate_of"])

            AuditEvent.objects.create(
                event=event,
                actor_user=self.operator_user,
                actor_kind=AuditEvent.ActorKind.IMPORT,
                action="PROJECT_DISPOSITION_DUPLICATE",
                entity_type="Project",
                entity_id=p41.id,
                after_json={
                    "duplicate_of_id": str(p07.id),
                    "reason": p41.disposition_reason,
                },
                reason="Imported fixture duplicate prj_41 identified against prj_07",
            )

        return projects_map

    def _import_scores(self, event, scores_data, judges_map, projects_map, rubric, criteria_map, batch):
        active_count = 0
        quarantined_count = 0

        for s in scores_data:
            judge_info = judges_map[s["judge"]]
            judge_membership = judge_info["membership"]
            judge_declared_tracks = judge_info["tracks"]

            project = projects_map[s["project"]]
            project_track_slug = project.submitted_revision.track.slug

            # Check if judge has permission for this track
            is_permitted = project_track_slug in [t.lower() for t in judge_declared_tracks]
            status = (
                JudgeAssignment.Status.ACTIVE
                if is_permitted
                else JudgeAssignment.Status.QUARANTINED
            )

            if status == JudgeAssignment.Status.ACTIVE:
                active_count += 1
            else:
                quarantined_count += 1

            assignment = JudgeAssignment.objects.create(
                event=event,
                judge_membership=judge_membership,
                project=project,
                project_revision=project.submitted_revision,
                rubric=rubric,
                status=status,
                source=JudgeAssignment.Source.FIXTURE_OBSERVED,
            )

            # Review (submitted)
            review = Review.objects.create(
                assignment=assignment,
                status=Review.Status.SUBMITTED,
                comment=s.get("comment", ""),
                submitted_at=timezone.now(),
            )

            # Review scores
            for crit_key, val in s.get("criteria", {}).items():
                crit = criteria_map[crit_key]
                ReviewScore.objects.create(
                    review=review,
                    criterion=crit,
                    value=val,
                )

            # Immutable review revision
            ReviewRevision.objects.create(
                review=review,
                number=1,
                status=Review.Status.SUBMITTED,
                scores_snapshot=s.get("criteria", {}),
                comment_snapshot=s.get("comment", ""),
                source=ReviewRevision.Source.FIXTURE_IMPORT,
                reason="Imported observed score",
            )

            ExternalRecord.objects.create(
                namespace=self.NAMESPACE,
                entity_type="Score",
                external_id=f"{s['judge']}_{s['project']}",
                internal_id=review.id,
                original_payload_json=s,
                import_batch=batch,
            )

        return {
            "total_scores": len(scores_data),
            "active_assignments": active_count,
            "quarantined_assignments": quarantined_count,
        }

    def _create_demo_event_if_missing(self, batch):
        now = timezone.now()
        slug = "demo-open-hack-2026"
        demo_event, created = Event.objects.get_or_create(
            slug=slug,
            defaults={
                "name": "Live Open Hack 2026",
                "tagline": "Active demonstration event for live submissions and interactive judging",
                "description_md": (
                    "# Live Open Hack 2026\n\n"
                    "This is the active demo event with an open submission window. "
                    "Use this event to test participant drafting, team invitations, submissions, and judging."
                ),
                "rules_md": "Open event for interactive demonstration.",
                "lifecycle": Event.Lifecycle.PUBLISHED,
                "registration_opens_at": now - timezone.timedelta(days=7),
                "registration_closes_at": now + timezone.timedelta(days=7),
                "submissions_opens_at": now - timezone.timedelta(days=3),
                "submissions_closes_at": now + timezone.timedelta(days=4),
                "judging_opens_at": now + timezone.timedelta(days=4),
                "judging_closes_at": now + timezone.timedelta(days=10),
                "min_team_size": 1,
                "max_team_size": 4,
                "required_reviews": 3,
            },
        )
        if created:
            Track.objects.create(event=demo_event, slug="ai-agents", name="AI & Autonomous Agents", display_order=1)
            Track.objects.create(event=demo_event, slug="web-infra", name="Developer Tools & Infrastructure", display_order=2)
            Rubric.objects.create(
                event=demo_event,
                version_number=1,
                name="Demo Rubric",
                state=Rubric.State.FROZEN,
                frozen_at=now,
            )
        return demo_event
