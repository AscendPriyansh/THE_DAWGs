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

    # Checker / Critical Workflow Routes
    path("projects/new", submit_project_route, name="submit_project_checker"),
    path("projects/new/", submit_project_route, name="submit_project"),
    path("api/judge/scores", judge_scores_route, name="judge_scores"),
    path("api/export.csv", csv_export_route, name="csv_export"),

    # Authentication REST APIs
    path("api/v1/auth/login/", login_view, name="api_login"),
    path("api/v1/auth/logout/", logout_view, name="api_logout"),
    path("api/v1/auth/me/", me_view, name="api_me"),

    # Event & Project REST APIs
    path("api/v1/events/", api_event_list, name="api_event_list"),
    path("api/v1/events/<slug:slug>/", api_event_detail, name="api_event_detail"),
    path("api/v1/events/<slug:slug>/projects/", api_gallery_list, name="api_gallery_list"),
    path("api/v1/events/<slug:slug>/me/next-action/", api_next_action, name="api_next_action"),
    path("api/v1/projects/<uuid:pk>/", api_project_detail, name="api_project_detail"),
]
