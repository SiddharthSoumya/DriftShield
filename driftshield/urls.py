from django.urls import path

from governance import views
from infrastructure import views as infra

urlpatterns = [
    path("", views.index, name="index"),
    path("login/", views.WorkspaceLogin.as_view(), name="login"),
    path("logout/", views.sign_out, name="logout"),
    path("api/v1/infrastructure/<int:pk>/observations/", infra.ingest, name="infra_ingest"),
    path("w/<slug:slug>/infra/", infra.overview, name="infra"),
    path("w/<slug:slug>/infra/add/", infra.add, name="infra_add"),
    path("w/<slug:slug>/infra/evidence.csv", infra.evidence, name="infra_evidence"),
    path("w/<slug:slug>/infra/<int:pk>/", infra.detail, name="infra_detail"),
    path("w/<slug:slug>/infra/<int:pk>/action/", infra.action, name="infra_action"),
    path("livez", views.live, name="live"),
    path("healthz", views.health, name="health"),
    path("w/<slug:slug>/", views.overview, name="overview"),
    path("w/<slug:slug>/repos/add/", views.add_public, name="add_public"),
    path("w/<slug:slug>/repos/import/", views.import_repos, name="import_repos"),
    path("w/<slug:slug>/repos/<int:pk>/", views.monitor_detail, name="monitor"),
    path("w/<slug:slug>/repos/<int:pk>/action/", views.monitor_action, name="monitor_action"),
    path("w/<slug:slug>/repos/<int:pk>/remove/", views.remove_monitor, name="remove_monitor"),
    path("w/<slug:slug>/audit/", views.audit_log, name="audit"),
    path("w/<slug:slug>/settings/", views.settings_view, name="settings"),
    path("w/<slug:slug>/evidence.csv", views.evidence, name="evidence"),
    path("w/<slug:slug>/worker.json", views.worker_health, name="worker_health"),
]
