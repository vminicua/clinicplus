from django.urls import path

from . import views


app_name = "accounts"

urlpatterns = [
    path("utilizadores/", views.UserListView.as_view(), name="user_list"),
    path("utilizadores/novo/", views.UserCreateView.as_view(), name="user_create"),
    path("utilizadores/<int:pk>/", views.UserDetailView.as_view(), name="user_detail"),
    path("utilizadores/<int:pk>/editar/", views.UserUpdateView.as_view(), name="user_update"),
    path("utilizadores/<int:pk>/eliminar/", views.UserDeleteView.as_view(), name="user_delete"),
    path("perfis/", views.RoleListView.as_view(), name="role_list"),
    path("perfis/novo/", views.RoleCreateView.as_view(), name="role_create"),
    path("perfis/<int:pk>/", views.RoleDetailView.as_view(), name="role_detail"),
    path("perfis/<int:pk>/editar/", views.RoleUpdateView.as_view(), name="role_update"),
    path("perfis/<int:pk>/eliminar/", views.RoleDeleteView.as_view(), name="role_delete"),
    path("permissoes/", views.PermissionListView.as_view(), name="permission_list"),
    path("permissoes/nova/", views.PermissionCreateView.as_view(), name="permission_create"),
    path("permissoes/<int:pk>/", views.PermissionDetailView.as_view(), name="permission_detail"),
    path("permissoes/<int:pk>/editar/", views.PermissionUpdateView.as_view(), name="permission_update"),
    path("permissoes/<int:pk>/eliminar/", views.PermissionDeleteView.as_view(), name="permission_delete"),
]

