import uuid
from django.conf import settings
from django.db import models
from apps.events.models import Event, Track
from apps.teams.models import Team


class Project(models.Model):
    class State(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        DISQUALIFIED = "DISQUALIFIED", "Disqualified"
        DUPLICATE = "DUPLICATE", "Duplicate"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="projects")
    team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="projects")
    state = models.CharField(max_length=20, choices=State.choices, default=State.DRAFT)
    draft_revision = models.ForeignKey(
        "ProjectRevision", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    submitted_revision = models.ForeignKey(
        "ProjectRevision", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    first_submitted_at = models.DateTimeField(null=True, blank=True)
    last_submitted_at = models.DateTimeField(null=True, blank=True)
    duplicate_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="duplicates"
    )
    disposition_reason = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "projects"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "team"],
                condition=models.Q(state__in=["DRAFT", "SUBMITTED"]),
                name="unique_active_project_per_team",
            )
        ]
        indexes = [
            models.Index(fields=["event", "state", "last_submitted_at", "id"], name="idx_proj_gallery"),
        ]

    def __str__(self):
        rev = self.submitted_revision or self.draft_revision
        title = rev.title if rev else "Untitled"
        return f"{title} [{self.state}]"


class ProjectRevision(models.Model):
    class Source(models.TextChoices):
        USER = "USER", "User"
        FIXTURE_IMPORT = "FIXTURE_IMPORT", "Fixture Import"
        PORTABLE_IMPORT = "PORTABLE_IMPORT", "Portable Import"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    track = models.ForeignKey(Track, on_delete=models.PROTECT, related_name="project_revisions")
    title = models.CharField(max_length=160)
    summary = models.CharField(max_length=500)
    description_md = models.TextField(max_length=100000, blank=True)
    repo_url = models.URLField(max_length=2048, null=True, blank=True)
    demo_url = models.URLField(max_length=2048, null=True, blank=True)
    roster_snapshot = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.USER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "project_revisions"
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="unique_project_revision_number")
        ]
        indexes = [
            models.Index(fields=["track", "project"], name="idx_proj_rev_track"),
        ]

    def __str__(self):
        return f"{self.title} (v{self.number})"


class RevisionAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.ForeignKey(ProjectRevision, on_delete=models.CASCADE, related_name="revision_assets")
    asset = models.ForeignKey("media_assets.Asset", on_delete=models.CASCADE, related_name="revision_attachments")
    caption = models.CharField(max_length=240, blank=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "revision_assets"
        constraints = [
            models.UniqueConstraint(fields=["revision", "asset"], name="unique_revision_asset")
        ]
        ordering = ["display_order", "id"]

    def __str__(self):
        return f"Asset {self.asset_id} on Rev {self.revision_id}"

