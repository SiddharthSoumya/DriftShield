from django.urls import path

from governance import views

urlpatterns = [
    path("", views.index, name="index"),
    path("login/", views.WorkspaceLogin.as_view(), name="login"),
    path("logout/", views.sign_out, name="logout"),
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
