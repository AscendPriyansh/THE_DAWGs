import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.events.models import Event


class Team(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=100)
    captain_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="captained_teams"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "teams"

    def __str__(self):
        return f"{self.name} ({self.event.name})"


class TeamMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="team_memberships")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="team_memberships"
    )
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "team_members"
        constraints = [
            models.UniqueConstraint(fields=["event", "user"], name="unique_event_team_member"),
            models.UniqueConstraint(fields=["team", "user"], name="unique_team_user_member"),
        ]

    def __str__(self):
        return f"{self.user} in {self.team.name}"
