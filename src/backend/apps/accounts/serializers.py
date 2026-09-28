from rest_framework import serializers
from apps.accounts.models import User
from apps.events.models import EventMembership


class EventMembershipSerializer(serializers.ModelSerializer):
    event_slug = serializers.CharField(source="event.slug", read_only=True)
    event_name = serializers.CharField(source="event.name", read_only=True)

    class Meta:
        model = EventMembership
        fields = ["id", "event_slug", "event_name", "role", "status", "joined_at"]


class UserSerializer(serializers.ModelSerializer):
    memberships = EventMembershipSerializer(source="event_memberships", many=True, read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "display_name", "is_staff", "is_superuser", "memberships"]


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
