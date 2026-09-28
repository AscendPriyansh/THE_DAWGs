import os
from django.conf import settings
from django.core.management.base import BaseCommand
from apps.imports.importer import FixtureImporter


class Command(BaseCommand):
    help = "Faithfully import fixture dataset from fixtures/fixtures.json"

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=os.path.join(settings.BASE_DIR.parent.parent, "fixtures", "fixtures.json"),
            help="Path to fixtures.json",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force reimport even if SHA-256 matches",
        )

    def handle(self, *args, **options):
        path = options["path"]
        if not os.path.exists(path):
            self.stderr.write(self.style.ERROR(f"Fixture file not found: {path}"))
            return

        self.stdout.write(f"Importing fixtures from {path}...")
        importer = FixtureImporter(path)
        res = importer.import_fixture(force=options["force"])
        if res["status"] == "NOOP":
            self.stdout.write(self.style.WARNING(f"NOOP: {res['message']} (Batch {res['batch_id']})"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Successfully applied fixture batch {res['batch_id']}"))
            rep = res["report"]
            self.stdout.write(
                f"Projects: {rep['projects_count']}, "
                f"Scores: {rep['scores_stats']['total_scores']}, "
                f"Judges: {rep['judges_count']}, "
                f"Teams: {rep['teams_count']}, "
                f"Tracks: {rep['tracks_count']}"
            )
