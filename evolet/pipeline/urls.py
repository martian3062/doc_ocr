"""Pipeline URL configuration."""
from django.urls import path
from . import views, api_views

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

    # Delete
    path("patients/<int:patient_id>/delete/", views.delete_patient, name="delete_patient"),
    path("documents/<uuid:doc_id>/delete/", views.delete_document, name="delete_document"),

    # Pipeline Runs
    path("runs/", views.run_list, name="run_list"),
    path("runs/<uuid:run_id>/", views.run_detail, name="run_detail"),
    path("runs/start/", views.start_run, name="start_run"),
    path("runs/start-one/", views.start_run_one, name="start_run_one"),
    path("runs/<uuid:run_id>/cancel/", views.cancel_run, name="cancel_run"),

    # QC
    path("qc/", views.qc_summary, name="qc_summary"),

    # Downloads
    path("download/patient/<int:patient_id>/json/", views.download_patient_json, name="download_patient_json"),
    path("download/run/<uuid:run_id>/zip/", views.download_run_zip, name="download_run_zip"),

    # HTMX partials / API
    path("api/recent-runs/", views.recent_runs_partial, name="recent_runs_partial"),
    path("api/progress/<uuid:run_id>/", views.run_progress, name="run_progress"),
    path("api/gpu/", views.api_gpu_status, name="api_gpu_status"),
    path("api/system/", views.api_system_info, name="api_system_info"),

    # REST API Endpoints (Next.js)
    path("api/v1/dashboard", api_views.api_dashboard, name="api_dashboard_noslash"),
    path("api/v1/dashboard/", api_views.api_dashboard, name="api_dashboard"),
    path("api/v1/documents", api_views.api_document_list, name="api_documents_noslash"),
    path("api/v1/documents/", api_views.api_document_list, name="api_documents"),
    path("api/v1/documents/<uuid:doc_id>/pdf", api_views.api_document_pdf, name="api_document_pdf_noslash"),
    path("api/v1/documents/<uuid:doc_id>/pdf/", api_views.api_document_pdf, name="api_document_pdf"),
    path("api/v1/patients", api_views.api_patient_list, name="api_patients_noslash"),
    path("api/v1/patients/", api_views.api_patient_list, name="api_patients"),
    path("api/v1/patients/<int:patient_id>", api_views.api_patient_detail, name="api_patient_detail_noslash"),
    path("api/v1/patients/<int:patient_id>/", api_views.api_patient_detail, name="api_patient_detail"),
    path("api/v1/patients/<int:patient_id>/knowledge-map", api_views.api_knowledge_map, name="api_knowledge_map_noslash"),
    path("api/v1/patients/<int:patient_id>/knowledge-map/", api_views.api_knowledge_map, name="api_knowledge_map"),
    path("api/v1/runs", api_views.api_run_list, name="api_runs_noslash"),
    path("api/v1/runs/", api_views.api_run_list, name="api_runs"),
    path("api/v1/runs/<uuid:run_id>", api_views.api_run_detail, name="api_run_detail_noslash"),
    path("api/v1/runs/<uuid:run_id>/", api_views.api_run_detail, name="api_run_detail"),
]
