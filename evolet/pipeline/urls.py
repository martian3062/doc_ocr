"""Pipeline URL configuration."""
from django.urls import path
from . import views

app_name = "pipeline"

urlpatterns = [
    # Dashboard
    path("", views.dashboard, name="dashboard"),

    # Upload & Import
    path("upload/", views.upload, name="upload"),
    path("folder/", views.folder_browser, name="folder_browser"),
    path("folder/import/", views.import_folder, name="import_folder"),

    # Patients
    path("patients/", views.patient_list, name="patient_list"),
    path("patients/<int:patient_id>/", views.patient_detail, name="patient_detail"),

    # Pipeline Runs
    path("runs/", views.run_list, name="run_list"),
    path("runs/<uuid:run_id>/", views.run_detail, name="run_detail"),
    path("runs/start/", views.start_run, name="start_run"),

    # QC
    path("qc/", views.qc_summary, name="qc_summary"),

    # Downloads
    path("download/patient/<int:patient_id>/json/", views.download_patient_json, name="download_patient_json"),
    path("download/run/<uuid:run_id>/zip/", views.download_run_zip, name="download_run_zip"),

    # HTMX partials
    path("api/progress/<uuid:run_id>/", views.run_progress, name="run_progress"),
    path("api/gpu/", views.api_gpu_status, name="api_gpu_status"),
]
