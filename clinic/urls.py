from django.urls import path
from . import views

app_name = 'clinic'

urlpatterns = [
    path('login/', views.custom_login, name='login'),
    path('logout/', views.custom_logout, name='logout'),
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
