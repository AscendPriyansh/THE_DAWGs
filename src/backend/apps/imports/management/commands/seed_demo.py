import os
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.accounts.models import User
from apps.events.models import Event, EventMembership
from apps.imports.importer import FixtureImporter


class Command(BaseCommand):
    help = "Seed demo users, assign roles, create sessions, and output .dogfood.toml auth configuration"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-toml",
            action="store_true",
            help="Generate .dogfood.toml with real session cookies",
        )

    def handle(self, *args, **options):
        # 1. Ensure fixture is imported
        fixture_path = os.path.join(settings.BASE_DIR.parent.parent, "fixtures", "fixtures.json")
        importer = FixtureImporter(fixture_path)
        res = importer.import_fixture(force=False)
        self.stdout.write(f"Fixture status: {res['status']}")

        sample_event = Event.objects.get(slug="sample-hack-2026")
        demo_event = Event.objects.get(slug="demo-open-hack-2026")

        # 2. Organiser
        org_email = "organizer@example.org"
        org_user, _ = User.objects.get_or_create(
            email=org_email,
            defaults={"display_name": "Event Organiser", "is_staff": True},
        )
        org_user.set_password("dogfoodpass123")
        org_user.save()

        EventMembership.objects.get_or_create(
            event=sample_event,
            user=org_user,
            defaults={"role": EventMembership.Role.ORGANISER},
        )
        EventMembership.objects.get_or_create(
            event=demo_event,
            user=org_user,
            defaults={"role": EventMembership.Role.ORGANISER},
        )

        # 3. Judge A (Tomas Varga from fixtures, jdg_01)
        judge_a_email = "tomas.varga@example.org"
        judge_a, _ = User.objects.get_or_create(
            email=judge_a_email,
            defaults={"display_name": "Tomas Varga"},
        )
        judge_a.set_password("dogfoodpass123")
        judge_a.save()

        # 4. Judge B (Mariana Costa from fixtures, jdg_02)
        judge_b_email = "mariana.costa@example.org"
        judge_b, _ = User.objects.get_or_create(
            email=judge_b_email,
            defaults={"display_name": "Mariana Costa"},
        )
        judge_b.set_password("dogfoodpass123")
        judge_b.save()

        # 5. Participant (Priya from fixtures tm_01, priya1@example.org)
        part_email = "priya1@example.org"
        part_user, _ = User.objects.get_or_create(
            email=part_email,
            defaults={"display_name": "Priya Sharma"},
        )
        part_user.set_password("dogfoodpass123")
        part_user.save()

        # 6. Generate real sessions
        sessions = {}
        for role_name, u in [
            ("organizer", org_user),
            ("judge_a", judge_a),
            ("judge_b", judge_b),
            ("participant", part_user),
        ]:
            s = SessionStore()
            s["_auth_user_id"] = str(u.id)
            s["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
            s["_auth_user_hash"] = u.get_session_auth_hash()
            s.save()
            sessions[role_name] = s.session_key

        self.stdout.write(self.style.SUCCESS("Demo accounts and sessions ready:"))
        self.stdout.write(f"  Organiser:   {org_email} (Session: {sessions['organizer']})")
        self.stdout.write(f"  Judge A:     {judge_a_email} (Session: {sessions['judge_a']})")
        self.stdout.write(f"  Judge B:     {judge_b_email} (Session: {sessions['judge_b']})")
        self.stdout.write(f"  Participant: {part_email} (Session: {sessions['participant']})")
        self.stdout.write("  Default password for all demo accounts: dogfoodpass123")

        if options["output_toml"]:
            toml_path = os.path.join(settings.BASE_DIR.parent.parent, ".dogfood.toml")
            content = f"""# .dogfood.toml - isolated local development environment
[portal]
base_url = "http://localhost:8000"

[tiers]
claimed = ["T1"]
pitch = "Dogfood 2026: Fast, resilient hackathon portal with PostgreSQL persistence and faithful fixture audit."

[auth]
organizer   = "Cookie: session={sessions['organizer']}"
judge_a     = "Cookie: session={sessions['judge_a']}"
judge_b     = "Cookie: session={sessions['judge_b']}"
participant = "Cookie: session={sessions['participant']}"

[routes]
gallery      = "/projects"
submit       = "/projects/new"
judge_scores = "/api/judge/scores"
peer_scores  = "/api/judge/scores?judge=judge_a"
csv_export   = "/api/export.csv"
"""
            with open(toml_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.stdout.write(self.style.SUCCESS(f"Generated {toml_path} with live session cookies."))
