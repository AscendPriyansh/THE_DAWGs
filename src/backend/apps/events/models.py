import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class Event(models.Model):
    class Lifecycle(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        ARCHIVED = "ARCHIVED", "Archived"

    class RankingScope(models.TextChoices):
        EVENT = "EVENT", "Event"
        TRACK = "TRACK", "Track"

    class RankingMethod(models.TextChoices):
        RAW_WEIGHTED_V1 = "RAW_WEIGHTED_V1", "Raw Weighted V1"
        RIDGE_JUDGE_OFFSET_V1 = "RIDGE_JUDGE_OFFSET_V1", "Ridge Judge Offset V1"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=80, unique=True, db_index=True)
    name = models.CharField(max_length=160)
    tagline = models.CharField(max_length=240, blank=True)
    description_md = models.TextField(max_length=100000, blank=True)
    rules_md = models.TextField(max_length=100000, blank=True)
    cover_asset_id = models.UUIDField(null=True, blank=True)
    timezone = models.CharField(max_length=64, default="Australia/Melbourne")
    lifecycle = models.CharField(
        max_length=20, choices=Lifecycle.choices, default=Lifecycle.DRAFT
    )
    registration_opens_at = models.DateTimeField()
    registration_closes_at = models.DateTimeField()
    submissions_opens_at = models.DateTimeField()
    submissions_closes_at = models.DateTimeField()
    judging_opens_at = models.DateTimeField()
    judging_closes_at = models.DateTimeField()
    voting_opens_at = models.DateTimeField(null=True, blank=True)
    voting_closes_at = models.DateTimeField(null=True, blank=True)
    min_team_size = models.PositiveIntegerField(default=1)
    max_team_size = models.PositiveIntegerField(default=4)
    required_reviews = models.PositiveIntegerField(default=3)
    ranking_scope = models.CharField(
        max_length=20, choices=RankingScope.choices, default=RankingScope.EVENT
    )
    ranking_method = models.CharField(
        max_length=30, choices=RankingMethod.choices, default=RankingMethod.RAW_WEIGHTED_V1
    )
    normalisation_lambda = models.FloatField(default=5.0)
    show_public_progress = models.BooleanField(default=False)
    active_publication_id = models.UUIDField(null=True, blank=True)
    judging_frozen_at = models.DateTimeField(null=True, blank=True)
    data_version = models.PositiveIntegerField(default=1)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "events"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(registration_opens_at__lt=models.F("registration_closes_at")),
                name="check_event_reg_dates",
            ),
            models.CheckConstraint(
                condition=models.Q(submissions_opens_at__lt=models.F("submissions_closes_at")),
                name="check_event_sub_dates",
            ),
            models.CheckConstraint(
                condition=models.Q(judging_opens_at__lt=models.F("judging_closes_at")),
                name="check_event_judge_dates",
            ),
            models.CheckConstraint(
                condition=models.Q(min_team_size__gte=1) & models.Q(min_team_size__lte=models.F("max_team_size")),
                name="check_event_team_sizes",
            ),
            models.CheckConstraint(
                condition=models.Q(required_reviews__gte=1),
                name="check_event_required_reviews",
            ),
            models.CheckConstraint(
                condition=models.Q(normalisation_lambda__gt=0),
                name="check_event_norm_lambda_positive",
            ),
        ]

    def __str__(self):
        return self.name

    def is_submission_open(self, now=None):
        if now is None:
            now = timezone.now()
        return (
            self.lifecycle == self.Lifecycle.PUBLISHED
            and self.submissions_opens_at <= now < self.submissions_closes_at
        )

    def current_phase(self, now=None):
        if now is None:
            now = timezone.now()
        if self.lifecycle != self.Lifecycle.PUBLISHED:
            return "DRAFT" if self.lifecycle == self.Lifecycle.DRAFT else "ARCHIVED"
        if now < self.submissions_opens_at:
            return "UPCOMING"
        elif now < self.submissions_closes_at:
            return "SUBMISSIONS_OPEN"
        elif now < self.judging_closes_at:
            return "JUDGING"
        elif self.active_publication_id:
            return "RESULTS_PUBLISHED"
        else:
            return "CLOSED"


class EventMembership(models.Model):
    class Role(models.TextChoices):
        PARTICIPANT = "PARTICIPANT", "Participant"
        JUDGE = "JUDGE", "Judge"
        ORGANISER = "ORGANISER", "Organiser"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="event_memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    joined_at = models.DateTimeField(default=timezone.now)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "event_memberships"
        constraints = [
            models.UniqueConstraint(fields=["event", "user"], name="unique_event_user_membership")
        ]

    def __str__(self):
        return f"{self.user} - {self.role} in {self.event.name}"


class Track(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="tracks")
    slug = models.SlugField(max_length=80)
    name = models.CharField(max_length=100)
    description_md = models.TextField(max_length=10000, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tracks"
        constraints = [
            models.UniqueConstraint(fields=["event", "slug"], name="unique_event_track_slug")
        ]
        ordering = ["display_order", "name"]

    def __str__(self):
        return f"{self.name} ({self.event.slug})"


class Prize(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="prizes")
    track = models.ForeignKey(Track, on_delete=models.SET_NULL, null=True, blank=True, related_name="prizes")
    name = models.CharField(max_length=120)
    description_md = models.TextField(max_length=10000, blank=True)
    amount_minor = models.PositiveIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, null=True, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "prizes"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name
