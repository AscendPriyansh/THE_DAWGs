# API-COVERAGE — Comprehensive Business Action Inventory

Status: **Canonical and Implemented (Milestone M07)**  
Updated: 28 September 2026  
Specification: OpenAPI 3.1.0 at `/api/v1/schema.json`  
Interactive Docs: Local self-contained reference at `/api/docs`  

---

## 1. Overview & Architectural Contracts

All business actions in the Dogfood 2026 Hackathon Portal are implemented as first-class REST endpoints. No business logic or state mutation is confined strictly to HTML views, template context handlers, or the Django admin interface.

### Authentication & Authorization Layers
1. **Dual Authentication Support**:
   - **Session Authentication**: Cookie-backed sessions (`session` cookie) requiring standard CSRF tokens for browser navigation and form submissions.
   - **Bearer Authentication**: Scoped bearer API keys (`Authorization: Bearer dg_live_...`) issued via `ApiCredential`. Bearer requests require no CSRF.
   - **Mixed Identity Rejection**: Requests presenting both a conflicting session cookie and a Bearer token are rejected with `401 Unauthorized` / `403 Forbidden` to prevent identity ambiguity.
2. **Role & Scope Intersection**:
   - An API key's effective permission is **always the intersection** of its configured scopes, the owner's active `EventMembership` role, and object permissions.
   - A key with scope `results:publish` held by a `PARTICIPANT` cannot publish results (`403 Forbidden: Organiser permissions required`). A scope can never elevate user role authority.
3. **Idempotency Contract (`IdempotencyRecord`)**:
   - Mutation endpoints accept optional or required `Idempotency-Key` headers.
   - Replaying the same key with identical payload replays the cached status code and JSON payload without side effects.
   - Reusing a key with a different payload returns `409 Conflict`.
   - Concurrent in-flight operations return retryable `409 Conflict`.

---

## 2. Business Action Inventory

| Domain | Business Action | UI Location / Trigger | REST Route & Method | Permitted Role(s) | Required API Scope | Domain Service | Permission / Integration Test |
|---|---|---|---|---|---|---|---|
| **Auth** | User Sign In | `/login/` | `POST /api/v1/auth/login/` | Public / Anonymous | N/A | `authenticate()`, `login()` | `test_session_login_and_logout` |
| **Auth** | User Sign Out | User menu | `POST /api/v1/auth/logout/` | Authenticated | N/A | `logout()` | `test_session_login_and_logout` |
| **Auth** | Profile Read | Top navigation | `GET /api/v1/auth/me/` | Authenticated | `event:read` | Profile resolver | `test_authenticated_me_endpoint` |
| **Events** | List Events | Home page `/` | `GET /api/v1/events/` | Public | None | `Event.objects.filter()` | `test_gallery_public_access` |
| **Events** | Event Details | `/events/<slug>/` | `GET /api/v1/events/<slug>/` | Public | None | Event resolver | `test_gallery_public_access` |
| **Events** | Update Settings | `/events/<slug>/manage/` | `POST /api/v1/events/<slug>/update-settings/` | Organiser | `event:manage` | `update_event_settings()` | `test_event_scoped_role_denial` |
| **Events** | Next Action | Workspace / Dashboard | `GET /api/v1/events/<slug>/me/next-action/` | Authenticated | `event:read` | `api_next_action()` | `test_community_api_endpoints_and_next_action` |
| **Submissions** | Public Gallery | `/projects/` | `GET /api/v1/events/<slug>/projects/` | Public | None | Gallery filter | `test_gallery_filter_and_search` |
| **Submissions** | Project Detail | `/projects/<uuid>/` | `GET /api/v1/projects/<id>/` | Public | None | Project detail | `test_project_detail_view` |
| **Submissions** | Form Team | `/workspace` | `POST /api/v1/workspace/create-team/` | Participant | `team:manage` | `create_team()` | `test_participant_workflow` |
| **Submissions** | Invite Teammate | `/workspace` | `POST /api/v1/workspace/invite/` | Captain | `team:manage` | `create_team_invitation()` | `test_participant_workflow` |
| **Submissions** | Save Draft | `/workspace` editor | `POST /api/v1/workspace/save-draft/` | Participant | `submission:write` | `save_draft_submission()` | `test_optimistic_locking_conflict` |
| **Submissions** | Submit Project | `/workspace` submit | `POST /api/v1/workspace/submit/` | Captain | `submission:write` | `submit_project()` | `test_captain_receipt_generation` |
| **Judging** | Assigned Queue | `/judging/` | `GET /api/v1/events/<slug>/judging/assignments/` | Judge | `judging:write` | Queue filter | `test_judge_sees_own_scores` |
| **Judging** | Review Draft | `/judging/` evaluate | `POST /api/v1/assignments/<id>/review/` | Judge | `judging:write` | `save_draft_review()` | `test_draft_evaluation_update` |
| **Judging** | Review Submit | `/judging/` submit | `POST /api/v1/assignments/<id>/review/submit/` | Judge | `judging:write` | `submit_review()` | `test_submit_review_workflow` |
| **Judging** | Organiser Progress | `/events/<slug>/judging/manage/` | `GET /api/v1/events/<slug>/organiser/progress/` | Organiser | `event:manage` | Coverage resolver | `test_organiser_coverage_dashboard` |
| **Results** | Calculate Preview | Leaderboard admin | `POST /api/v1/events/<slug>/results/preview/` | Organiser | `results:publish` | `calculate_result_run()` | `test_preview_results_confidentiality` |
| **Results** | Publish Leaderboard | Leaderboard admin | `POST /api/v1/events/<slug>/results/publish/` | Organiser | `results:publish` | `publish_results()` | `test_publish_results_window_gates` |
| **Results** | Public Results | `/results/` | `GET /api/v1/events/<slug>/results/` | Public | None | Publication reader | `test_results_no_leak_before_publication` |
| **Results** | CSV Export | `/api/export.csv` | `GET /api/v1/events/<slug>/exports/results.csv` | Organiser | `data:export` | `results_csv_export()` | `test_csv_export_permissions_and_sanitization` |
| **Community** | List Comments | Project discussion | `GET /api/v1/events/<slug>/projects/<id>/comments/` | Public | None | Comment filter | `test_comment_lifecycle_and_scrubbing` |
| **Community** | Create Comment | Project discussion | `POST /api/v1/events/<slug>/projects/<id>/comments/` | Authenticated | `event:read` | `create_comment()` | `test_comment_window_and_policy_enforcement` |
| **Community** | Edit Comment | Project discussion | `PATCH /api/v1/events/<slug>/comments/<id>/` | Author | `event:read` | `edit_comment()` | `test_comment_lifecycle_and_scrubbing` |
| **Community** | Delete Comment | Project discussion | `DELETE /api/v1/events/<slug>/comments/<id>/` | Author / Organiser | `event:read` | `delete_comment()` | `test_comment_lifecycle_and_scrubbing` |
| **Community** | Moderate Comment | Moderation inbox | `POST /api/v1/events/<slug>/comments/<id>/moderate/` | Organiser | `community:moderate` | `moderate_comment()` | `test_moderation_cases_and_decisions` |
| **Community** | Report Abuse | Comment / Project | `POST /api/v1/events/<slug>/moderation/report/` | Authenticated | `event:read` | `report_moderation_case()` | `test_moderation_cases_and_decisions` |
| **Community** | Moderation Inbox | Moderation admin | `GET /api/v1/events/<slug>/moderation/inbox/` | Organiser | `community:moderate` | Case resolver | `test_moderation_cases_and_decisions` |
| **Community** | Resolve Case | Moderation admin | `POST /api/v1/events/<slug>/moderation/cases/<id>/resolve/` | Organiser | `community:moderate` | `resolve_moderation_case()` | `test_moderation_cases_and_decisions` |
| **Community** | Dismiss Case | Moderation admin | `POST /api/v1/events/<slug>/moderation/cases/<id>/dismiss/` | Organiser | `community:moderate` | `dismiss_moderation_case()` | `test_moderation_cases_and_decisions` |
| **Integrations** | Issue API Key | Settings / API Keys | `POST /api/v1/events/<slug>/api-keys/` | Authenticated | `integrations:manage` | `issue_api_credential()` | `test_scoped_api_key_issuance_and_revocation` |
| **Integrations** | List API Keys | Settings / API Keys | `GET /api/v1/events/<slug>/api-keys/` | Authenticated | `integrations:manage` | Key resolver | `test_scoped_api_key_issuance_and_revocation` |
| **Integrations** | Revoke API Key | Settings / API Keys | `DELETE /api/v1/events/<slug>/api-keys/<id>/` | Owner / Organiser | `integrations:manage` | `revoke_api_credential()` | `test_scoped_api_key_issuance_and_revocation` |
| **Webhooks** | List Webhooks | `/events/<slug>/manage/webhooks/` | `GET /api/v1/events/<slug>/webhooks/` | Organiser | `integrations:manage` | Endpoint reader | `test_webhook_endpoint_crud_and_ssrf` |
| **Webhooks** | Create Webhook | `/events/<slug>/manage/webhooks/` | `POST /api/v1/events/<slug>/webhooks/` | Organiser | `integrations:manage` | Endpoint creator | `test_webhook_endpoint_crud_and_ssrf` |
| **Webhooks** | Update Webhook | `/events/<slug>/manage/webhooks/` | `PATCH /api/v1/events/<slug>/webhooks/<id>/` | Organiser | `integrations:manage` | Endpoint editor | `test_webhook_endpoint_crud_and_ssrf` |
| **Webhooks** | Delete Webhook | `/events/<slug>/manage/webhooks/` | `DELETE /api/v1/events/<slug>/webhooks/<id>/` | Organiser | `integrations:manage` | Endpoint deleter | `test_webhook_endpoint_crud_and_ssrf` |
| **Webhooks** | List Deliveries | Delivery diagnostics | `GET /api/v1/events/<slug>/webhooks/<id>/deliveries/` | Organiser | `integrations:manage` | Delivery reader | `test_webhook_delivery_state_machine` |
| **Webhooks** | Delivery Detail | Delivery diagnostics | `GET /api/v1/events/<slug>/webhooks/deliveries/<id>/` | Organiser | `integrations:manage` | Delivery reader | `test_webhook_delivery_state_machine` |
| **Webhooks** | Replay Delivery | Delivery diagnostics | `POST /api/v1/events/<slug>/webhooks/deliveries/<id>/replay/` | Organiser | `integrations:manage` | `replay_webhook_delivery()` | `test_webhook_delivery_replay_generation` |
| **Jobs** | List Jobs | Background jobs console | `GET /api/v1/events/<slug>/jobs/` | Authenticated | `integrations:manage` | Job reader | `test_background_job_execution_and_authority` |
| **Jobs** | Enqueue Job | Background jobs console | `POST /api/v1/events/<slug>/jobs/` | Organiser | `data:export` / `credentials:issue` | `submit_background_job()` | `test_background_job_execution_and_authority` |
| **Jobs** | Job Details | Background jobs console | `GET /api/v1/events/<slug>/jobs/<id>/` | Requester / Organiser | `integrations:manage` | Job reader | `test_background_job_execution_and_authority` |
| **Docs** | OpenAPI JSON | Direct API call | `GET /api/v1/schema.json` | Public | None | `api_openapi_schema()` | `test_openapi_schema_and_docs_endpoints` |
| **Docs** | Interactive Docs | `/api/docs` | `GET /api/docs` | Public | None | `api_docs_ui()` | `test_openapi_schema_and_docs_endpoints` |

---

## 3. Pure Navigation vs. Business Actions

Navigation routes (render only without state changes):
- `/login/` -> Authentication modal/page.
- `/workspace/` -> Participant workspace dashboard.
- `/judging/` -> Judge workspace dashboard.
- `/results/` -> Leaderboard visualization page.
- `/api/docs` -> Local offline OpenAPI documentation viewer.
