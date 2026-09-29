import logging
from typing import Dict, Any, Optional
from django.db import transaction
from django.utils import timezone
from apps.events.models import Event
from apps.integrations.models import DomainEvent, WebhookEndpoint, WebhookDelivery

logger = logging.getLogger(__name__)


def publish_domain_event(
    event: Event,
    event_type: str,
    entity_id: Any,
    payload: Dict[str, Any],
) -> DomainEvent:
    """
    Inserts a DomainEvent into the transactional outbox and creates WebhookDelivery
    records for all active endpoints subscribed to this event type.
    
    Must be called within an active database transaction. If the transaction rolls back,
    no DomainEvent or WebhookDelivery records will persist.
    """
    # Create the immutable DomainEvent record
    domain_event = DomainEvent.objects.create(
        event=event,
        type=event_type,
        schema_version="1.0",
        entity_id=str(entity_id),
        payload=payload,
    )

    # Find active endpoints for this event
    active_endpoints = WebhookEndpoint.objects.filter(
        event=event,
        status=WebhookEndpoint.Status.ACTIVE,
    )

    now = timezone.now()
    deliveries_to_create = []

    for endpoint in active_endpoints:
        allowed = endpoint.allowed_event_types or []
        if "*" in allowed or event_type in allowed:
            deliveries_to_create.append(
                WebhookDelivery(
                    domain_event=domain_event,
                    endpoint=endpoint,
                    state=WebhookDelivery.State.PENDING,
                    next_attempt_at=now,
                )
            )

    if deliveries_to_create:
        WebhookDelivery.objects.bulk_create(deliveries_to_create)
        logger.info(
            "Created %d webhook deliveries for event %s [%s]",
            len(deliveries_to_create),
            domain_event.id,
            event_type,
        )

    return domain_event
