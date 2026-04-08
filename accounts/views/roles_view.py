from django.contrib.auth.models import Group
from django.db.models import Prefetch
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.forms import RoleForm
from accounts.ui import filter_users_for_branch, ui_text
from accounts.utils import visible_users_queryset

from .base_view import AppPermissionMixin, ClinicPageMixin, ModalDetailMixin, ModalFormMixin


class RoleListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = Group
    template_name = "accounts/roles/list.html"
    context_object_name = "roles"
    permission_required = "auth.view_group"
    segment = "roles"
    def get_page_title(self) -> str:
        return ui_text(self.request, "Perfis e roles", "Roles")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Agrupe permissões por função operacional da clínica.",
            "Group permissions by the clinic's operational role.",
        )

    def get_queryset(self):
        visible_users = filter_users_for_branch(visible_users_queryset(), self.request).order_by(
            "first_name",
            "last_name",
            "username",
        )
        return Group.objects.prefetch_related(
            "permissions",
            Prefetch("user_set", queryset=visible_users),
        ).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = Group.objects.all()
        visible_users = filter_users_for_branch(visible_users_queryset(), self.request)
        context["total_roles"] = base_queryset.count()
        context["roles_in_use"] = base_queryset.filter(user__in=visible_users).distinct().count()
        context["roles_with_permissions"] = base_queryset.filter(
            permissions__isnull=False
        ).distinct().count()
        context["linked_users"] = visible_users.filter(groups__isnull=False).distinct().count()
        return context


class RoleDetailView(AppPermissionMixin, ModalDetailMixin, ClinicPageMixin, DetailView):
    template_name = "accounts/roles/detail.html"
    permission_required = "auth.view_group"
    segment = "roles"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Detalhes do perfil", "Role details")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Veja o conjunto de permissões e os utilizadores ligados a este perfil.",
            "See the permission set and users linked to this role.",
        )

    def get_queryset(self):
        return Group.objects.prefetch_related(
            "permissions",
            Prefetch(
                "user_set",
                queryset=filter_users_for_branch(visible_users_queryset(), self.request).order_by(
                    "first_name",
                    "last_name",
                    "username",
                ),
            ),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["ordered_permissions"] = self.object.permissions.select_related(
            "content_type"
        ).order_by("content_type__app_label", "content_type__model", "name")
        context["assigned_users"] = self.object.user_set.all()
        context["detail_partial"] = "accounts/roles/includes/detail_content.html"
        context["modal_heading"] = self.object.name
        context["modal_description"] = ui_text(
            self.request,
            "Conjunto de permissões e utilizadores ligados a este perfil.",
            "Permission set and users linked to this role.",
        )
        return context


class RoleCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Group
    form_class = RoleForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:role_list")
    modal_size = "modal-xl"
    permission_required = "auth.add_group"
    segment = "roles"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo perfil", "New role")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Crie um perfil que represente uma função do sistema.",
            "Create a role that represents a system function.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar perfil", "Create role")
        context["form_description"] = ui_text(
            self.request,
            "Use perfis para agrupar permissões por cargo ou responsabilidade.",
            "Use roles to group permissions by position or responsibility.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar perfil", "Save role")
        context["cancel_url"] = reverse("accounts:role_list")
        context["form_mode"] = "role"
        context["permission_matrix"] = context["form"].permission_matrix
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Perfil criado com sucesso.", "Role created successfully.")


class RoleUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = Group.objects.prefetch_related("permissions")
    form_class = RoleForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:role_list")
    modal_size = "modal-xl"
    permission_required = "auth.change_group"
    segment = "roles"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar perfil", "Edit role")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize o nome e as permissões deste perfil.",
            "Update the name and permissions of this role.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar perfil", "Edit role")
        context["form_description"] = ui_text(
            self.request,
            "As alterações reflectem-se em todos os utilizadores ligados a este perfil.",
            "Changes are reflected for every user linked to this role.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar perfil", "Update role")
        context["cancel_url"] = reverse("accounts:role_detail", args=[self.object.pk])
        context["form_mode"] = "role"
        context["permission_matrix"] = context["form"].permission_matrix
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Perfil actualizado com sucesso.", "Role updated successfully.")
