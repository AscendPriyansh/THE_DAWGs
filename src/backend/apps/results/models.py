import uuid
from django.conf import settings
from django.db import models
from apps.events.models import Event
from apps.submissions.models import Project


class ResultRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="result_runs")
    source_data_version = models.PositiveIntegerField()
    algorithm_version = models.CharField(max_length=64)
    parameters_json = models.JSONField(default=dict)
    input_snapshot_json = models.JSONField()
    input_sha256 = models.CharField(max_length=64)
    output_sha256 = models.CharField(max_length=64)
    diagnostics_json = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "result_runs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"ResultRun v{self.source_data_version} ({self.algorithm_version}) on {self.event.slug}"


class ResultRow(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    result_run = models.ForeignKey(ResultRun, on_delete=models.CASCADE, related_name="rows")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="result_rows")
    cohort_key = models.CharField(max_length=100, default="EVENT")
    eligible = models.BooleanField(default=True)
    exclusion_reason = models.TextField(blank=True)
    completed_review_count = models.PositiveIntegerField(default=0)
    assigned_review_count = models.PositiveIntegerField(default=0)
    raw_mean = models.FloatField(null=True, blank=True)
    adjusted_value = models.FloatField(null=True, blank=True)
    ranking_value = models.FloatField(null=True, blank=True)
    comparison_component = models.IntegerField(null=True, blank=True)
    rank = models.PositiveIntegerField(null=True, blank=True)
    flags_json = models.JSONField(default=list)

    class Meta:
        db_table = "result_rows"
        constraints = [
            models.UniqueConstraint(fields=["result_run", "project"], name="unique_result_run_project")
        ]
        ordering = ["cohort_key", "rank", "id"]

    def __str__(self):
        return f"Project {self.project_id} - Rank {self.rank} ({self.cohort_key})"


class Publication(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="publications")
    result_run = models.ForeignKey(ResultRun, on_delete=models.PROTECT, related_name="publications")
    number = models.PositiveIntegerField(default=1)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="published_results"
    )
    published_at = models.DateTimeField(auto_now_add=True)
    public_note_md = models.TextField(blank=True)
    waivers_json = models.JSONField(default=list)
    supersedes = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="superseded_by"
    )

    class Meta:
        db_table = "publications"
        constraints = [
            models.UniqueConstraint(fields=["event", "number"], name="unique_event_publication_number")
        ]
        ordering = ["-number"]

    def __str__(self):
        return f"Publication #{self.number} for {self.event.name}"


class CommunityResultRow(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    result_run = models.ForeignKey(ResultRun, on_delete=models.CASCADE, related_name="community_rows")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="community_result_rows")
    eligible = models.BooleanField(default=True)
    counted_votes = models.PositiveIntegerField(default=0)
    excluded_votes = models.PositiveIntegerField(default=0)
    rank = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "community_result_rows"
        constraints = [
            models.UniqueConstraint(fields=["result_run", "project"], name="unique_community_result_run_project")
        ]
        ordering = ["rank", "id"]

    def __str__(self):
        return f"CommunityResult Project {self.project_id}: {self.counted_votes} votes (Rank {self.rank})"

