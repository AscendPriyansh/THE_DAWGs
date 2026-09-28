import os
import pytest
from apps.events.models import Event
from apps.submissions.models import Project
from apps.judging.models import Review, JudgeAssignment
from apps.imports.importer import FixtureImporter
from django.conf import settings


@pytest.mark.django_db(transaction=True)
def test_faithful_fixture_import():
    fixture_path = os.path.join(settings.BASE_DIR.parent.parent, "fixtures", "fixtures.json")
    importer = FixtureImporter(fixture_path)
    res = importer.import_fixture(force=True)

    assert res["status"] == "APPLIED"

    event = Event.objects.get(slug="sample-hack-2026")
    assert str(event.submissions_closes_at) == "2026-03-01 18:00:00+00:00"

    # All 41 projects preserved
    projects = Project.objects.filter(event=event)
    assert projects.count() == 41

    # 40 submitted, 1 duplicate
    submitted = projects.filter(state=Project.State.SUBMITTED)
    assert submitted.count() == 40

    dup = projects.get(state=Project.State.DUPLICATE)
    p07 = projects.get(submitted_revision__title="Dry Harbour", state=Project.State.SUBMITTED)
    assert dup.duplicate_of_id == p07.id
    assert "prj_07" in dup.disposition_reason

    # All 126 reviews and assignments preserved
    assert Review.objects.filter(assignment__event=event).count() == 126
    assert JudgeAssignment.objects.filter(event=event).count() == 126

    # Idempotent second run
    second_res = importer.import_fixture(force=False)
    assert second_res["status"] == "NOOP"
    assert Project.objects.filter(event=event).count() == 41
