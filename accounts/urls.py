from django.urls import path

from . import views


app_name = "accounts"

urlpatterns = [
    path("utilizadores/", views.UserListView.as_view(), name="user_list"),
    path("utilizadores/novo/", views.UserCreateView.as_view(), name="user_create"),
    path("utilizadores/<int:pk>/", views.UserDetailView.as_view(), name="user_detail"),
    path("utilizadores/<int:pk>/editar/", views.UserUpdateView.as_view(), name="user_update"),
    path("utilizadores/<int:pk>/estado/", views.UserToggleStatusView.as_view(), name="user_toggle_status"),
    path("organizacao/sucursais/", views.BranchListView.as_view(), name="branch_list"),
    path("organizacao/sucursais/nova/", views.BranchCreateView.as_view(), name="branch_create"),
    path("organizacao/sucursais/<int:pk>/", views.BranchDetailView.as_view(), name="branch_detail"),
    path("organizacao/sucursais/<int:pk>/editar/", views.BranchUpdateView.as_view(), name="branch_update"),
    path(
        "organizacao/sucursais/<int:pk>/estado/",
        views.BranchToggleStatusView.as_view(),
        name="branch_toggle_status",
    ),
    path("preferencias/", views.SystemPreferenceView.as_view(), name="system_preferences"),
    path("preferencias/idioma/", views.LanguageSwitchView.as_view(), name="switch_language"),
    path("perfis/", views.RoleListView.as_view(), name="role_list"),
    path("perfis/novo/", views.RoleCreateView.as_view(), name="role_create"),
    path("perfis/<int:pk>/", views.RoleDetailView.as_view(), name="role_detail"),
    path("perfis/<int:pk>/editar/", views.RoleUpdateView.as_view(), name="role_update"),
    path("permissoes/", views.PermissionListView.as_view(), name="permission_list"),
    path("permissoes/nova/", views.PermissionCreateView.as_view(), name="permission_create"),
    path("permissoes/<int:pk>/", views.PermissionDetailView.as_view(), name="permission_detail"),
    path("permissoes/<int:pk>/editar/", views.PermissionUpdateView.as_view(), name="permission_update"),
]
