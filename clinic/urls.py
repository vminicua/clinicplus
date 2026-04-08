from django.urls import path
from . import views

app_name = 'clinic'

urlpatterns = [
    path('login/', views.custom_login, name='login'),
    path('logout/', views.custom_logout, name='logout'),
    path('', views.dashboard, name='index'),
]
