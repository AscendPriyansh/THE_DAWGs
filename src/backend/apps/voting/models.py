import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.events.models import Event
from apps.submissions.models import Project


class VotingPolicy(models.Model):
    class Mode(models.TextChoices):
        AUTHENTICATED = "AUTHENTICATED", "Authenticated"
        INVITE_LINK = "INVITE_LINK", "Invite Link"
        EMAIL_VERIFIED = "EMAIL_VERIFIED", "Email Verified"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name="voting_policy")
    mode = models.CharField(max_length=20, choices=Mode.choices, default=Mode.AUTHENTICATED)
    minimum_account_age_seconds = models.PositiveIntegerField(default=0)
    comments_enabled = models.BooleanField(default=False)
    frozen_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "voting_policies"

    def __str__(self):
        return f"Voting Policy for {self.event.name}"


class VotingLinkGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="voting_link_grants")
    token_digest = models.CharField(max_length=64, unique=True)
    recipient_label_encrypted = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    redeemed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_voting_link_grants"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "voting_link_grants"

    def __str__(self):
        return f"Link Grant for {self.event.name}"


class VoterIdentity(models.Model):
    class Kind(models.TextChoices):
        USER = "USER", "User"
        EMAIL = "EMAIL", "Email"
        LINK = "LINK", "Link"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="voter_identities")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="voter_identities"
    )
    email_digest = models.CharField(max_length=64, null=True, blank=True)
    link_grant = models.ForeignKey(
        VotingLinkGrant, on_delete=models.PROTECT, null=True, blank=True, related_name="voter_identities"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "voter_identities"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "user"],
                condition=models.Q(user__isnull=False),
                name="unique_voter_identity_user",
            ),
            models.UniqueConstraint(
                fields=["event", "email_digest"],
                condition=models.Q(email_digest__isnull=False),
                name="unique_voter_identity_email",
            ),
            models.UniqueConstraint(
                fields=["event", "link_grant"],
                condition=models.Q(link_grant__isnull=False),
                name="unique_voter_identity_link",
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(kind="USER") & models.Q(user__isnull=False) & models.Q(email_digest__isnull=True) & models.Q(link_grant__isnull=True)) |
                    (models.Q(kind="EMAIL") & models.Q(email_digest__isnull=False) & models.Q(user__isnull=True) & models.Q(link_grant__isnull=True)) |
                    (models.Q(kind="LINK") & models.Q(link_grant__isnull=False) & models.Q(user__isnull=True) & models.Q(email_digest__isnull=True))
                ),
                name="check_voter_identity_principal",
            )
        ]

    def __str__(self):
        return f"{self.kind} Identity in {self.event.name}"


class EmailChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="email_challenges")
    delivery_email_encrypted = models.TextField(blank=True)
    email_digest = models.CharField(max_length=64)
    token_digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "email_challenges"

    def __str__(self):
        return f"Challenge for {self.event.name}"


class Vote(models.Model):
    class State(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        VOID = "VOID", "Void"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="votes")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="votes")
    voter_identity = models.ForeignKey(VoterIdentity, on_delete=models.CASCADE, related_name="votes")
    state = models.CharField(max_length=20, choices=State.choices, default=State.ACTIVE)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "votes"
        constraints = [
            models.UniqueConstraint(fields=["event", "project", "voter_identity"], name="unique_vote_event_project_identity")
        ]

    def __str__(self):
        return f"Vote by {self.voter_identity.id} on {self.project.id}"


class BallotSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="ballot_sessions")
    voter_identity = models.ForeignKey(VoterIdentity, on_delete=models.CASCADE, related_name="ballot_sessions")
    ordering_seed = models.CharField(max_length=128)
    eligible_project_ids = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ballot_sessions"
        constraints = [
            models.UniqueConstraint(fields=["event", "voter_identity"], name="unique_ballot_session_event_identity")
        ]

    def __str__(self):
        return f"Ballot for {self.voter_identity.id} in {self.event.name}"


class Comment(models.Model):
    class State(models.TextChoices):
        VISIBLE = "VISIBLE", "Visible"
        HIDDEN = "HIDDEN", "Hidden"
        DELETED = "DELETED", "Deleted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="comments")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="comments")
    author_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="comments"
    )
    body = models.TextField(max_length=5000, blank=True)
    state = models.CharField(max_length=20, choices=State.choices, default=State.VISIBLE)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "comments"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=["VISIBLE", "HIDDEN", "DELETED"]),
                name="check_comment_state",
            )
        ]

    def __str__(self):
        return f"Comment {self.id} on Project {self.project_id} ({self.state})"


class AbuseSignal(models.Model):
    class Kind(models.TextChoices):
        RAPID_ACCOUNT_VOTES = "RAPID_ACCOUNT_VOTES", "Rapid Account Votes"
        EXCESSIVE_FAILED_TOKENS = "EXCESSIVE_FAILED_TOKENS", "Excessive Failed Tokens"
        REPEATED_CONTENT = "REPEATED_CONTENT", "Repeated Content"
        REPEATED_REPO_SUBMISSION = "REPEATED_REPO_SUBMISSION", "Repeated Repository Submission"
        VOTING_BURST = "VOTING_BURST", "Voting Burst"
        SUSPICIOUS_NETWORK = "SUSPICIOUS_NETWORK", "Suspicious Network Activity"
        OTHER = "OTHER", "Other"

    class State(models.TextChoices):
        OPEN = "OPEN", "Open"
        REVIEWED = "REVIEWED", "Reviewed"
        DISMISSED = "DISMISSED", "Dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="abuse_signals")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="abuse_signals"
    )
    kind = models.CharField(max_length=64, choices=Kind.choices, default=Kind.OTHER)
    subject_id = models.CharField(max_length=128, null=True, blank=True)
    network_key = models.CharField(max_length=64, null=True, blank=True)
    details_json = models.JSONField(default=dict)
    state = models.CharField(max_length=20, choices=State.choices, default=State.OPEN)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_abuse_signals"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "abuse_signals"
        ordering = ["-created_at"]

    def __str__(self):
        return f"AbuseSignal {self.kind} ({self.state}) for {self.event.name}"


class ModerationCase(models.Model):
    class TargetType(models.TextChoices):
        COMMENT = "COMMENT", "Comment"
        VOTE = "VOTE", "Vote"
        VOTER_IDENTITY = "VOTER_IDENTITY", "Voter Identity"
        ABUSE_SIGNAL = "ABUSE_SIGNAL", "Abuse Signal"
        PROJECT = "PROJECT", "Project"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        RESOLVED = "RESOLVED", "Resolved"
        DISMISSED = "DISMISSED", "Dismissed"

    class Decision(models.TextChoices):
        HIDE_COMMENT = "HIDE_COMMENT", "Hide Comment"
        RESTORE_COMMENT = "RESTORE_COMMENT", "Restore Comment"
        SUSPEND_IDENTITY = "SUSPEND_IDENTITY", "Suspend Voter Identity"
        VOID_VOTES = "VOID_VOTES", "Void Votes"
        DISMISS_SIGNAL = "DISMISS_SIGNAL", "Dismiss Signal"
        NO_ACTION = "NO_ACTION", "No Action"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="moderation_cases")
    target_type = models.CharField(max_length=32, choices=TargetType.choices)
    target_id = models.UUIDField()
    reason_code = models.CharField(max_length=64)
    reporter_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reported_moderation_cases"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_moderation_cases"
    )
    decision = models.CharField(max_length=64, choices=Decision.choices, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="decided_moderation_cases"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "moderation_cases"
        ordering = ["-created_at"]

    def __str__(self):
        return f"ModerationCase {self.target_type}:{self.target_id} ({self.status}) for {self.event.name}"

