from django.contrib import admin
from django.urls import path, re_path
from django.views.generic import TemplateView
from apps.accounts.views import login_view, logout_view, me_view
from apps.events.views import (
    event_overview_page,
    gallery_page,
    project_detail_page,
    submit_project_route,
    judge_scores_route,
    csv_export_route,
    api_event_list,
    api_event_detail,
    api_gallery_list,
    api_project_detail,
    api_next_action,
)

from apps.submissions.views import (
    participant_workspace_page,
    api_save_draft,
    api_submit_project,
    api_create_team,
    api_create_team_invitation,
    accept_team_invite_page,
)
from apps.events.organiser_views import (
    organiser_manage_page,
    api_update_event_settings,
)
from apps.judging.views import (
    judge_workspace_page,
    organiser_judging_page,
    api_judge_assignments,
    api_assignment_review,
    api_submit_review,
    api_judge_scores,
    api_organiser_progress,
)
from apps.results.views import (
    results_page,
    api_preview_results,
    api_publish_results,
    api_get_results,
    results_csv_export,
)
from apps.voting.views import (
    api_project_comments,
    api_comment_detail,
    api_moderate_comment,
    api_report_moderation,
    api_moderation_inbox,
    api_resolve_moderation_case,
    api_dismiss_moderation_case,
)
from apps.integrations.views import (
    api_event_api_keys,
    api_revoke_api_key,
    api_openapi_schema,
    api_docs_ui,
    api_event_webhooks,
    api_webhook_endpoint_detail,
    api_webhook_deliveries,
    api_webhook_delivery_detail,
    api_webhook_delivery_replay,
    api_background_jobs,
    api_background_job_detail,
    organiser_webhooks_page,
)

from apps.credentials.views import (
    api_certificate_templates,
    api_award_decisions,
    api_eligible_recipients,
    api_issue_credentials,
    api_event_credentials,
    api_credential_detail,
    api_revoke_credential,
    api_credential_pdf,
    api_verify_credential,
    api_issuer_keys,
    api_public_consent,
    verify_credential_page,
    credentials_page,
)

from apps.events.embed_views import (
    embed_gallery_view,
    api_embed_config,
)
from apps.imports.views import (
    api_export_portable_archive,
    api_import_preview,
    api_import_apply,
    api_import_plan_detail,
)

urlpatterns = [
    path("admin/", admin.site.urls),

    # Public HTML Web Pages
    path("", event_overview_page, name="home"),
    path("events/<slug:slug>/", event_overview_page, name="event_overview"),
    path("projects", gallery_page, name="gallery_checker"),
    path("projects/", gallery_page, name="gallery"),
    path("events/<slug:slug>/projects/", gallery_page, name="event_gallery"),
    path("projects/<uuid:pk>/", project_detail_page, name="project_detail"),
    path("events/<slug:slug>/projects/<uuid:pk>/", project_detail_page, name="event_project_detail"),
    path("login/", TemplateView.as_view(template_name="portal/login.html"), name="login_page"),

    # Participant Workspace (M02)
    path("workspace", participant_workspace_page, name="workspace_no_slash"),
    path("workspace/", participant_workspace_page, name="workspace"),
    path("events/<slug:slug>/workspace/", participant_workspace_page, name="event_workspace"),
    path("join/team/<str:token>/", accept_team_invite_page, name="accept_team_invite"),

    # Organiser Management (M02)
    path("events/<slug:slug>/manage/", organiser_manage_page, name="organiser_manage"),

    # Checker / Critical Workflow Routes
    path("projects/new", submit_project_route, name="submit_project_checker"),
    path("projects/new/", submit_project_route, name="submit_project"),
    path("api/judge/scores", judge_scores_route, name="judge_scores"),
    path("api/export.csv", csv_export_route, name="csv_export"),

    # Authentication REST APIs
    path("api/v1/auth/login/", login_view, name="api_login"),
    path("api/v1/auth/logout/", logout_view, name="api_logout"),
    path("api/v1/auth/me/", me_view, name="api_me"),

    # Participant Workspace REST APIs (M02)
    path("api/v1/workspace/save-draft/", api_save_draft, name="api_save_draft"),
    path("api/v1/workspace/submit/", api_submit_project, name="api_submit_project"),
    path("api/v1/workspace/create-team/", api_create_team, name="api_create_team"),
    path("api/v1/workspace/invite/", api_create_team_invitation, name="api_create_team_invitation"),

    # Organiser REST APIs (M02)
    path("api/v1/events/<slug:slug>/update-settings/", api_update_event_settings, name="api_update_event_settings"),

    # Event & Project REST APIs
    path("api/v1/events/", api_event_list, name="api_event_list"),
    path("api/v1/events/<slug:slug>/", api_event_detail, name="api_event_detail"),
    path("api/v1/events/<slug:slug>/projects/", api_gallery_list, name="api_gallery_list"),
    path("api/v1/events/<slug:slug>/me/next-action/", api_next_action, name="api_next_action"),
    path("api/v1/projects/<uuid:pk>/", api_project_detail, name="api_project_detail"),

    # Judging Workspace & Coverage UI (M03)
    path("judging", judge_workspace_page, name="judge_workspace_no_slash"),
    path("judging/", judge_workspace_page, name="judge_workspace"),
    path("events/<slug:slug>/judging/", judge_workspace_page, name="event_judge_workspace"),
    path("events/<slug:slug>/judging/manage/", organiser_judging_page, name="organiser_judging"),

    # Judging REST APIs (M03)
    path("api/v1/events/<slug:slug>/judging/assignments/", api_judge_assignments, name="api_judge_assignments"),
    path("api/v1/assignments/<uuid:pk>/review/", api_assignment_review, name="api_assignment_review"),
    path("api/v1/assignments/<uuid:pk>/review/submit/", api_submit_review, name="api_submit_review"),
    path("api/v1/events/<slug:slug>/judges/<uuid:judge_user_id>/scores/", api_judge_scores, name="api_judge_scores_rest"),
    path("api/v1/events/<slug:slug>/organiser/progress/", api_organiser_progress, name="api_organiser_progress"),

    # Results & Leaderboard (M04)
    path("results", results_page, name="results_no_slash"),
    path("results/", results_page, name="results"),
    path("events/<slug:slug>/results/", results_page, name="event_results"),
    path("api/v1/events/<slug:slug>/results/preview/", api_preview_results, name="api_preview_results"),
    path("api/v1/events/<slug:slug>/results/publish/", api_publish_results, name="api_publish_results"),
    path("api/v1/events/<slug:slug>/results/", api_get_results, name="api_get_results"),
    path("api/v1/events/<slug:slug>/exports/results.csv", results_csv_export, name="event_results_csv"),
    
    # Community & Moderation (M06)
    path("api/v1/events/<slug:slug>/projects/<uuid:project_id>/comments/", api_project_comments, name="api_project_comments"),
    path("api/v1/events/<slug:slug>/comments/<uuid:comment_id>/", api_comment_detail, name="api_comment_detail"),
    path("api/v1/events/<slug:slug>/comments/<uuid:comment_id>/moderate/", api_moderate_comment, name="api_moderate_comment"),
    path("api/v1/events/<slug:slug>/moderation/report/", api_report_moderation, name="api_report_moderation"),
    path("api/v1/events/<slug:slug>/moderation/inbox/", api_moderation_inbox, name="api_moderation_inbox"),
    path("api/v1/events/<slug:slug>/moderation/cases/<uuid:case_id>/resolve/", api_resolve_moderation_case, name="api_resolve_moderation_case"),
    path("api/v1/events/<slug:slug>/moderation/cases/<uuid:case_id>/dismiss/", api_dismiss_moderation_case, name="api_dismiss_moderation_case"),

    # Scoped API Keys & Integrations (M07)
    path("api/v1/events/<slug:slug>/api-keys/", api_event_api_keys, name="api_event_api_keys"),
    path("api/v1/events/<slug:slug>/api-keys/<uuid:key_id>/", api_revoke_api_key, name="api_revoke_api_key"),

    # OpenAPI Schema & Local Documentation (M07)
    path("api/v1/schema.json", api_openapi_schema, name="api_openapi_schema"),
    path("api/docs", api_docs_ui, name="api_docs_ui_no_slash"),
    path("api/docs/", api_docs_ui, name="api_docs_ui"),

    # Webhooks & Outbox UI & REST APIs (M08)
    path("events/<slug:slug>/manage/webhooks/", organiser_webhooks_page, name="organiser_webhooks"),
    path("api/v1/events/<slug:slug>/webhooks/", api_event_webhooks, name="api_event_webhooks"),
    path("api/v1/events/<slug:slug>/webhooks/<uuid:endpoint_id>/", api_webhook_endpoint_detail, name="api_webhook_endpoint_detail"),
    path("api/v1/events/<slug:slug>/webhooks/<uuid:endpoint_id>/deliveries/", api_webhook_deliveries, name="api_webhook_deliveries"),
    path("api/v1/events/<slug:slug>/webhooks/deliveries/<uuid:delivery_id>/", api_webhook_delivery_detail, name="api_webhook_delivery_detail"),
    path("api/v1/events/<slug:slug>/webhooks/deliveries/<uuid:delivery_id>/replay/", api_webhook_delivery_replay, name="api_webhook_delivery_replay"),

    # Background Jobs REST APIs (M08)
    path("api/v1/events/<slug:slug>/jobs/", api_background_jobs, name="api_background_jobs"),
    path("api/v1/events/<slug:slug>/jobs/<uuid:job_id>/", api_background_job_detail, name="api_background_job_detail"),

    # Credentials & Certificates (M09)
    path("events/<slug:slug>/credentials/", credentials_page, name="event_credentials"),
    path("api/v1/events/<slug:slug>/credentials/templates/", api_certificate_templates, name="api_certificate_templates"),
    path("api/v1/events/<slug:slug>/credentials/awards/", api_award_decisions, name="api_award_decisions"),
    path("api/v1/events/<slug:slug>/credentials/eligible/", api_eligible_recipients, name="api_eligible_recipients"),
    path("api/v1/events/<slug:slug>/credentials/issue/", api_issue_credentials, name="api_issue_credentials"),
    path("api/v1/events/<slug:slug>/credentials/", api_event_credentials, name="api_event_credentials"),
    path("api/v1/events/<slug:slug>/credentials/<uuid:credential_id>/", api_credential_detail, name="api_credential_detail"),
    path("api/v1/events/<slug:slug>/credentials/<uuid:credential_id>/revoke/", api_revoke_credential, name="api_revoke_credential"),
    path("api/v1/events/<slug:slug>/credentials/<uuid:credential_id>/pdf/", api_credential_pdf, name="api_credential_pdf"),
    path("api/v1/events/<slug:slug>/consent/", api_public_consent, name="api_public_consent"),

    # Public verification (M09)
    path("verify/<uuid:credential_id>", verify_credential_page, name="verify_credential"),
    path("verify/<uuid:credential_id>/", verify_credential_page, name="verify_credential_slash"),
    path("api/v1/verify/<uuid:credential_id>/", api_verify_credential, name="api_verify_credential"),
    path("api/v1/issuer/keys/", api_issuer_keys, name="api_issuer_keys"),

    # Embeddable Public Gallery (M10)
    path("embed/events/<slug:slug>/", embed_gallery_view, name="embed_gallery"),
    path("embed/events/<slug:slug>", embed_gallery_view, name="embed_gallery_no_slash"),
    path("api/v1/events/<slug:slug>/embed-config/", api_embed_config, name="api_embed_config"),

    # Portable Bulk Import & Export (M10)
    path("api/v1/events/<slug:slug>/exports/portable/", api_export_portable_archive, name="api_export_portable_archive"),
    path("api/v1/events/import/preview/", api_import_preview, name="api_import_preview"),
    path("api/v1/events/import/apply/", api_import_apply, name="api_import_apply"),
    path("api/v1/events/import/plans/<uuid:plan_id>/", api_import_plan_detail, name="api_import_plan_detail"),
]


