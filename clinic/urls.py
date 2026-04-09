from django.urls import path
from . import views

app_name = 'clinic'

urlpatterns = [
    path('login/', views.custom_login, name='login'),
    path('logout/', views.custom_logout, name='logout'),
    path('marcacoes/', views.AppointmentListView.as_view(), name='appointment_list'),
    path('marcacoes/nova/', views.AppointmentCreateView.as_view(), name='appointment_create'),
    path('marcacoes/<int:appointment_pk>/consulta/', views.AppointmentConsultationView.as_view(), name='appointment_consultation'),
    path('agenda/', views.AppointmentAgendaView.as_view(), name='appointment_agenda'),
    path('horarios/', views.WorkScheduleListView.as_view(), name='work_schedule_list'),
    path('horarios/novo/', views.WorkScheduleCreateView.as_view(), name='work_schedule_create'),
    path('horarios/<int:pk>/', views.WorkScheduleDetailView.as_view(), name='work_schedule_detail'),
    path('horarios/<int:pk>/editar/', views.WorkScheduleUpdateView.as_view(), name='work_schedule_update'),
    path('horarios/<int:pk>/estado/', views.WorkScheduleToggleStatusView.as_view(), name='work_schedule_toggle_status'),
    path('pacientes/', views.PatientListView.as_view(), name='patient_list'),
    path('pacientes/novo/', views.PatientCreateView.as_view(), name='patient_create'),
    path('pacientes/historico/', views.PatientHistoryListView.as_view(), name='patient_history_list'),
    path('pacientes/<int:pk>/', views.PatientDetailView.as_view(), name='patient_detail'),
    path('pacientes/<int:pk>/editar/', views.PatientUpdateView.as_view(), name='patient_update'),
    path('pacientes/<int:pk>/ficha-pdf/', views.PatientPdfDownloadView.as_view(), name='patient_pdf'),
    path('pacientes/<int:pk>/estado/', views.PatientToggleStatusView.as_view(), name='patient_toggle_status'),
    path('pacientes/<int:pk>/historico/', views.PatientHistoryDetailView.as_view(), name='patient_history_detail'),
    path('', views.dashboard, name='index'),
]
