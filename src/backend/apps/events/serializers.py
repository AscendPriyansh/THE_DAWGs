from rest_framework import serializers
from apps.events.models import Event, Track, Prize, EventMembership
from apps.submissions.models import Project, ProjectRevision


class TrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = Track
        fields = ["id", "slug", "name", "description_md", "display_order"]


class PrizeSerializer(serializers.ModelSerializer):
    track_slug = serializers.CharField(source="track.slug", read_only=True)

    class Meta:
        model = Prize
        fields = ["id", "name", "description_md", "amount_minor", "currency", "track_slug", "display_order"]


class EventSerializer(serializers.ModelSerializer):
    phase = serializers.SerializerMethodField()
    is_submission_open = serializers.SerializerMethodField()
    tracks = TrackSerializer(many=True, read_only=True)
    prizes = PrizeSerializer(many=True, read_only=True)

    class Meta:
        model = Event
        fields = [
            "id", "slug", "name", "tagline", "description_md", "rules_md",
            "timezone", "lifecycle", "registration_opens_at", "registration_closes_at",
            "submissions_opens_at", "submissions_closes_at", "judging_opens_at",
            "judging_closes_at", "phase", "is_submission_open", "min_team_size",
            "max_team_size", "tracks", "prizes",
        ]

    def get_phase(self, obj):
        return obj.current_phase()

    def get_is_submission_open(self, obj):
        return obj.is_submission_open()


class ProjectRevisionSerializer(serializers.ModelSerializer):
    track_name = serializers.CharField(source="track.name", read_only=True)
    track_slug = serializers.CharField(source="track.slug", read_only=True)

    class Meta:
        model = ProjectRevision
        fields = [
            "id", "number", "track_slug", "track_name", "title", "summary",
            "description_md", "repo_url", "demo_url", "roster_snapshot", "created_at",
        ]


class ProjectCardSerializer(serializers.ModelSerializer):
    title = serializers.CharField(source="submitted_revision.title", read_only=True)
    summary = serializers.CharField(source="submitted_revision.summary", read_only=True)
    track_name = serializers.CharField(source="submitted_revision.track.name", read_only=True)
    track_slug = serializers.CharField(source="submitted_revision.track.slug", read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True)

    class Meta:
        model = Project
        fields = [
            "id", "state", "first_submitted_at", "last_submitted_at",
            "title", "summary", "track_name", "track_slug", "team_name",
        ]


class ProjectDetailSerializer(serializers.ModelSerializer):
    revision = ProjectRevisionSerializer(source="submitted_revision", read_only=True)
    team_name = serializers.CharField(source="team.name", read_only=True)

    class Meta:
        model = Project
        fields = [
            "id", "state", "first_submitted_at", "last_submitted_at",
            "team_name", "revision",
        ]
