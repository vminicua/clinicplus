from django.contrib.auth.models import Permission
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.forms import PermissionForm
from accounts.utils import is_system_permission

from .base_view import AppPermissionMixin, ClinicPageMixin, ModalFormMixin


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
    page_title = "Permissões"
    page_subtitle = "Consulte permissões do sistema e crie permissões personalizadas quando necessário."

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


class PermissionDetailView(AppPermissionMixin, ClinicPageMixin, DetailView):
    queryset = Permission.objects.select_related("content_type").prefetch_related("group_set")
    template_name = "accounts/permissions/detail.html"
    permission_required = "auth.view_permission"
    segment = "permissions"
    page_title = "Detalhes da permissão"
    page_subtitle = "Veja onde a permissão é usada e se faz parte do sistema base."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["is_system_permission"] = is_system_permission(self.object)
        context["linked_roles"] = self.object.group_set.order_by("name")
        return context


class PermissionCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Permission
    form_class = PermissionForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:permission_list")
    success_message = "Permissão criada com sucesso."
    permission_required = "auth.add_permission"
    segment = "permissions"
    page_title = "Nova permissão"
    page_subtitle = "Crie permissões personalizadas para fluxos específicos do sistema."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Criar permissão"
        context["form_description"] = "Permissões personalizadas ajudam a cobrir fluxos que ainda não existem no modelo base."
        context["submit_label"] = "Guardar permissão"
        context["cancel_url"] = reverse("accounts:permission_list")
        context["form_mode"] = "permission"
        return context


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
    success_message = "Permissão actualizada com sucesso."
    permission_required = "auth.change_permission"
    segment = "permissions"
    page_title = "Editar permissão"
    page_subtitle = "Actualize o nome visível, o código interno ou o escopo da permissão."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Editar permissão"
        context["form_description"] = "Ajuste apenas permissões personalizadas. As permissões base do sistema continuam protegidas."
        context["submit_label"] = "Actualizar permissão"
        context["cancel_url"] = reverse("accounts:permission_detail", args=[self.object.pk])
        context["form_mode"] = "permission"
        return context
