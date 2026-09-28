import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.events.models import Event, EventMembership, Track
from apps.submissions.models import Project, ProjectRevision


class Rubric(models.Model):
    class State(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        FROZEN = "FROZEN", "Frozen"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="rubrics")
    version_number = models.PositiveIntegerField(default=1)
    name = models.CharField(max_length=100)
    state = models.CharField(max_length=20, choices=State.choices, default=State.DRAFT)
    frozen_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "rubrics"
        constraints = [
            models.UniqueConstraint(fields=["event", "version_number"], name="unique_event_rubric_version")
        ]

    def __str__(self):
        return f"{self.name} (v{self.version_number})"


class Criterion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rubric = models.ForeignKey(Rubric, on_delete=models.CASCADE, related_name="criteria")
    key = models.CharField(max_length=64)
    label = models.CharField(max_length=120)
    description_md = models.TextField(max_length=5000, blank=True)
    weight = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("1.0000"))
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "criteria"
        constraints = [
            models.UniqueConstraint(fields=["rubric", "key"], name="unique_rubric_criterion_key"),
            models.CheckConstraint(condition=models.Q(weight__gt=0), name="check_criterion_weight_positive"),
        ]
        ordering = ["display_order", "id"]

    def __str__(self):
        return f"{self.label} ({self.key})"


class JudgeTrack(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey(
        EventMembership, on_delete=models.CASCADE, related_name="expertise_tracks"
    )
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="expert_judges")

    class Meta:
        db_table = "judge_tracks"
        constraints = [
            models.UniqueConstraint(fields=["membership", "track"], name="unique_judge_track_expertise")
        ]

    def __str__(self):
        return f"{self.membership.user.display_name} expert on {self.track.name}"


class JudgeTrackPermission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    membership = models.ForeignKey(
        EventMembership, on_delete=models.CASCADE, related_name="track_permissions"
    )
    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="permitted_judges")
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "judge_track_permissions"
        constraints = [
            models.UniqueConstraint(fields=["membership", "track"], name="unique_judge_track_permission")
        ]

    def __str__(self):
        return f"{self.membership.user.display_name} permitted on {self.track.name}"


class JudgeAssignment(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        REVOKED = "REVOKED", "Revoked"
        QUARANTINED = "QUARANTINED", "Quarantined"

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        BALANCED = "BALANCED", "Balanced"
        FIXTURE_OBSERVED = "FIXTURE_OBSERVED", "Fixture Observed"
        DEMO_SYNTHETIC = "DEMO_SYNTHETIC", "Demo Synthetic"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="assignments")
    judge_membership = models.ForeignKey(
        EventMembership, on_delete=models.CASCADE, related_name="assignments"
    )
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="judge_assignments")
    project_revision = models.ForeignKey(
        ProjectRevision, on_delete=models.PROTECT, related_name="judge_assignments"
    )
    rubric = models.ForeignKey(Rubric, on_delete=models.PROTECT, related_name="assignments")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    assigned_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "judge_assignments"
        constraints = [
            models.UniqueConstraint(
                fields=["judge_membership", "project"],
                condition=models.Q(status="ACTIVE"),
                name="unique_active_judge_assignment_per_project",
            )
        ]
        indexes = [
            models.Index(fields=["event", "judge_membership", "status"], name="idx_judge_work_queue"),
            models.Index(fields=["event", "project", "status"], name="idx_org_coverage"),
        ]

    def __str__(self):
        return f"Assignment {self.judge_membership.user.display_name} -> {self.project} [{self.status}]"


class Review(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.OneToOneField(JudgeAssignment, on_delete=models.CASCADE, related_name="review")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    comment = models.TextField(max_length=10000, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "reviews"

    def __str__(self):
        return f"Review ({self.status}) for {self.assignment_id}"


class ReviewScore(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="scores")
    criterion = models.ForeignKey(Criterion, on_delete=models.PROTECT, related_name="scores")
    value = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "review_scores"
        constraints = [
            models.UniqueConstraint(fields=["review", "criterion"], name="unique_review_criterion_score"),
            models.CheckConstraint(
                condition=models.Q(value__gte=1) & models.Q(value__lte=5),
                name="check_review_score_range_1_to_5",
            ),
        ]

    def __str__(self):
        return f"{self.criterion.key}={self.value}"


class ReviewRevision(models.Model):
    class Source(models.TextChoices):
        USER = "USER", "User"
        FIXTURE_IMPORT = "FIXTURE_IMPORT", "Fixture Import"
        PORTABLE_IMPORT = "PORTABLE_IMPORT", "Portable Import"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    status = models.CharField(max_length=20)
    scores_snapshot = models.JSONField()
    comment_snapshot = models.TextField(blank=True)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    reason = models.TextField(blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.USER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "review_revisions"
        constraints = [
            models.UniqueConstraint(fields=["review", "number"], name="unique_review_revision_number")
        ]

    def __str__(self):
        return f"Rev v{self.number} of review {self.review_id}"
