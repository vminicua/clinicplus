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
    path('estrutura/especialidades/', views.SpecialtyListView.as_view(), name='specialty_list'),
    path('estrutura/especialidades/nova/', views.SpecialtyCreateView.as_view(), name='specialty_create'),
    path('estrutura/especialidades/<int:pk>/editar/', views.SpecialtyUpdateView.as_view(), name='specialty_update'),
    path('estrutura/departamentos/', views.DepartmentListView.as_view(), name='department_list'),
    path('estrutura/departamentos/novo/', views.DepartmentCreateView.as_view(), name='department_create'),
    path('estrutura/departamentos/<int:pk>/editar/', views.DepartmentUpdateView.as_view(), name='department_update'),
    path('estrutura/medicamentos/', views.MedicationListView.as_view(), name='medication_list'),
    path('estrutura/medicamentos/novo/', views.MedicationCreateView.as_view(), name='medication_create'),
    path('estrutura/medicamentos/<int:pk>/editar/', views.MedicationUpdateView.as_view(), name='medication_update'),
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
