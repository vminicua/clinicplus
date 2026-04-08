from django.contrib.auth.models import Permission
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.forms import PermissionForm
from accounts.ui import ui_text
from accounts.utils import is_system_permission

from .base_view import AppPermissionMixin, ClinicPageMixin, ModalDetailMixin, ModalFormMixin


class EditablePermissionMixin:
    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()

        if is_system_permission(self.object):
            return redirect("accounts:permission_detail", pk=self.object.pk)

        return super().dispatch(request, *args, **kwargs)


class PermissionListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = Permission
    template_name = "accounts/permissions/list.html"
    context_object_name = "permissions"
    permission_required = "auth.view_permission"
    segment = "permissions"
    def get_page_title(self) -> str:
        return ui_text(self.request, "Permissões", "Permissions")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Consulte permissões do sistema e crie permissões personalizadas quando necessário.",
            "Review system permissions and create custom permissions when needed.",
        )

    def get_kind_filter(self) -> str:
        return (self.request.GET.get("kind") or "all").strip().lower()

    def get_queryset(self):
        queryset = Permission.objects.select_related("content_type").order_by(
            "content_type__app_label",
            "content_type__model",
            "name",
        )

        permissions = list(queryset)
        kind = self.get_kind_filter()

        if kind == "custom":
            return [permission for permission in permissions if not is_system_permission(permission)]
        if kind == "system":
            return [permission for permission in permissions if is_system_permission(permission)]

        return permissions

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_permissions = list(Permission.objects.select_related("content_type").all())
        context["total_permissions"] = len(all_permissions)
        context["system_permissions"] = sum(
            1 for permission in all_permissions if is_system_permission(permission)
        )
        context["custom_permissions"] = context["total_permissions"] - context["system_permissions"]
        context["current_kind"] = self.get_kind_filter()
        return context


class PermissionDetailView(AppPermissionMixin, ModalDetailMixin, ClinicPageMixin, DetailView):
    queryset = Permission.objects.select_related("content_type").prefetch_related("group_set")
    template_name = "accounts/permissions/detail.html"
    permission_required = "auth.view_permission"
    segment = "permissions"
    modal_size = "modal-lg"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Detalhes da permissão", "Permission details")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Veja onde a permissão é usada e se faz parte do sistema base.",
            "See where the permission is used and whether it belongs to the base system.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_system_permission"] = is_system_permission(self.object)
        context["linked_roles"] = self.object.group_set.order_by("name")
        context["detail_partial"] = "accounts/permissions/includes/detail_content.html"
        context["modal_heading"] = self.object.name
        context["modal_description"] = ui_text(
            self.request,
            "Escopo, tipo e perfis que herdam esta permissão.",
            "Scope, type, and roles that inherit this permission.",
        )
        return context


class PermissionCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Permission
    form_class = PermissionForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:permission_list")
    permission_required = "auth.add_permission"
    segment = "permissions"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Nova permissão", "New permission")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Crie permissões personalizadas para fluxos específicos do sistema.",
            "Create custom permissions for system-specific workflows.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar permissão", "Create permission")
        context["form_description"] = ui_text(
            self.request,
            "Permissões personalizadas ajudam a cobrir fluxos que ainda não existem no modelo base.",
            "Custom permissions help cover workflows that do not yet exist in the base model.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar permissão", "Save permission")
        context["cancel_url"] = reverse("accounts:permission_list")
        context["form_mode"] = "permission"
        return context

    def get_success_message(self) -> str:
        return ui_text(
            self.request,
            "Permissão criada com sucesso.",
            "Permission created successfully.",
        )


class PermissionUpdateView(
    EditablePermissionMixin,
    AppPermissionMixin,
    ModalFormMixin,
    ClinicPageMixin,
    UpdateView,
):
    queryset = Permission.objects.select_related("content_type")
    form_class = PermissionForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:permission_list")
    permission_required = "auth.change_permission"
    segment = "permissions"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar permissão", "Edit permission")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize o nome visível, o código interno ou o escopo da permissão.",
            "Update the visible name, internal code, or permission scope.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar permissão", "Edit permission")
        context["form_description"] = ui_text(
            self.request,
            "Ajuste apenas permissões personalizadas. As permissões base do sistema continuam protegidas.",
            "Adjust only custom permissions. Base system permissions remain protected.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar permissão", "Update permission")
        context["cancel_url"] = reverse("accounts:permission_detail", args=[self.object.pk])
        context["form_mode"] = "permission"
        return context

    def get_success_message(self) -> str:
        return ui_text(
            self.request,
            "Permissão actualizada com sucesso.",
            "Permission updated successfully.",
        )
