from django.contrib.auth.models import Group
from django.db.models import Prefetch
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.forms import RoleForm
from accounts.utils import visible_users_queryset

from .base_view import AppPermissionMixin, ClinicPageMixin, ModalDetailMixin, ModalFormMixin


class RoleListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = Group
    template_name = "accounts/roles/list.html"
    context_object_name = "roles"
    permission_required = "auth.view_group"
    segment = "roles"
    page_title = "Perfis e roles"
    page_subtitle = "Agrupe permissões por função operacional da clínica."

    def get_queryset(self):
        visible_users = visible_users_queryset().order_by("first_name", "last_name", "username")
        return Group.objects.prefetch_related(
            "permissions",
            Prefetch("user_set", queryset=visible_users),
        ).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = Group.objects.all()
        context["total_roles"] = base_queryset.count()
        context["roles_in_use"] = base_queryset.filter(user__is_superuser=False).distinct().count()
        context["roles_with_permissions"] = base_queryset.filter(
            permissions__isnull=False
        ).distinct().count()
        context["linked_users"] = visible_users_queryset().filter(groups__isnull=False).distinct().count()
        return context


class RoleDetailView(AppPermissionMixin, ModalDetailMixin, ClinicPageMixin, DetailView):
    queryset = Group.objects.prefetch_related(
        "permissions",
        Prefetch("user_set", queryset=visible_users_queryset().order_by("first_name", "last_name", "username")),
    )
    template_name = "accounts/roles/detail.html"
    permission_required = "auth.view_group"
    segment = "roles"
    page_title = "Detalhes do perfil"
    page_subtitle = "Veja o conjunto de permissões e os utilizadores ligados a este perfil."
    modal_size = "modal-xl"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["ordered_permissions"] = self.object.permissions.select_related(
            "content_type"
        ).order_by("content_type__app_label", "content_type__model", "name")
        context["assigned_users"] = self.object.user_set.all()
        context["detail_partial"] = "accounts/roles/includes/detail_content.html"
        context["modal_heading"] = self.object.name
        context["modal_description"] = "Conjunto de permissões e utilizadores ligados a este perfil."
        return context


class RoleCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Group
    form_class = RoleForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:role_list")
    success_message = "Perfil criado com sucesso."
    modal_size = "modal-xl"
    permission_required = "auth.add_group"
    segment = "roles"
    page_title = "Novo perfil"
    page_subtitle = "Crie um perfil que represente uma função do sistema."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Criar perfil"
        context["form_description"] = "Use perfis para agrupar permissões por cargo ou responsabilidade."
        context["submit_label"] = "Guardar perfil"
        context["cancel_url"] = reverse("accounts:role_list")
        context["form_mode"] = "role"
        context["permission_matrix"] = context["form"].permission_matrix
        return context


class RoleUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = Group.objects.prefetch_related("permissions")
    form_class = RoleForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:role_list")
    success_message = "Perfil actualizado com sucesso."
    modal_size = "modal-xl"
    permission_required = "auth.change_group"
    segment = "roles"
    page_title = "Editar perfil"
    page_subtitle = "Actualize o nome e as permissões deste perfil."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Editar perfil"
        context["form_description"] = "As alterações reflectem-se em todos os utilizadores ligados a este perfil."
        context["submit_label"] = "Actualizar perfil"
        context["cancel_url"] = reverse("accounts:role_detail", args=[self.object.pk])
        context["form_mode"] = "role"
        context["permission_matrix"] = context["form"].permission_matrix
        return context
