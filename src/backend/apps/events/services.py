from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.dateparse import parse_datetime
from apps.audit.models import AuditEvent
from apps.events.models import Event, EventMembership, Track, Prize


def update_event_settings(organiser_user, event, data):
    membership = EventMembership.objects.filter(
        event=event, user=organiser_user, status=EventMembership.Status.ACTIVE
    ).first()
    if not membership or (membership.role != EventMembership.Role.ORGANISER and not organiser_user.is_staff):
        raise PermissionDenied("Only an organiser can modify event configuration.")

    with transaction.atomic():
        before_state = {
            "name": event.name,
            "tagline": event.tagline,
            "description_md": event.description_md,
            "rules_md": event.rules_md,
            "min_team_size": event.min_team_size,
            "max_team_size": event.max_team_size,
            "submissions_closes_at": event.submissions_closes_at.isoformat(),
        }

        if "name" in data and data["name"].strip():
            event.name = data["name"].strip()
        if "tagline" in data:
            event.tagline = data["tagline"].strip()
        if "description_md" in data:
            event.description_md = data["description_md"]
        if "rules_md" in data:
            event.rules_md = data["rules_md"]
        if "min_team_size" in data:
            event.min_team_size = int(data["min_team_size"])
        if "max_team_size" in data:
            event.max_team_size = int(data["max_team_size"])

        # Check team size invariant
        if event.min_team_size > event.max_team_size:
            raise ValidationError("Minimum team size cannot exceed maximum team size.")

        event.version += 1
        event.data_version += 1
        event.save()

        after_state = {
            "name": event.name,
            "tagline": event.tagline,
            "description_md": event.description_md,
            "rules_md": event.rules_md,
            "min_team_size": event.min_team_size,
            "max_team_size": event.max_team_size,
            "submissions_closes_at": event.submissions_closes_at.isoformat(),
        }

        AuditEvent.objects.create(
            event=event,
            actor_user=organiser_user,
            actor_kind=AuditEvent.ActorKind.USER,
            action="EVENT_CONFIG_UPDATED",
            entity_type="Event",
            entity_id=event.id,
            before_json=before_state,
            after_json=after_state,
            reason="Organiser configuration update",
        )

        from apps.integrations.outbox import publish_domain_event

        publish_domain_event(
            event=event,
            event_type="event.updated",
            entity_id=str(event.id),
            payload={
                "event_id": str(event.id),
                "slug": event.slug,
                "version": event.version,
                "data_version": event.data_version,
            },
        )

        return event
