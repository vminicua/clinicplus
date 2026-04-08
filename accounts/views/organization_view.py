from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.forms import BranchForm
from accounts.models import Branch
from accounts.ui import ui_text
from accounts.utils import visible_users_queryset

from .base_view import AppPermissionMixin, ClinicPageMixin, ModalDetailMixin, ModalFormMixin


class BranchListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = Branch
    template_name = "accounts/organization/branches/list.html"
    context_object_name = "branches"
    permission_required = "accounts.view_branch"
    segment = "branches"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Sucursais", "Branches")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Gerencie unidades, estado operacional e utilizadores alocados a cada sucursal.",
            "Manage branches, operational status, and the users assigned to each branch.",
        )

    def get_queryset(self):
        return Branch.objects.prefetch_related("user_profiles__user").order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["total_branches"] = Branch.objects.count()
        context["active_branches"] = Branch.objects.filter(is_active=True).count()
        context["allocated_users"] = (
            visible_users_queryset().filter(profile__assigned_branches__isnull=False).distinct().count()
        )
        context["branches_with_default_users"] = (
            visible_users_queryset().filter(profile__default_branch__isnull=False).distinct().count()
        )
        return context


class BranchDetailView(AppPermissionMixin, ModalDetailMixin, ClinicPageMixin, DetailView):
    queryset = Branch.objects.prefetch_related("user_profiles__user")
    template_name = "accounts/organization/branches/detail.html"
    permission_required = "accounts.view_branch"
    segment = "branches"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Detalhes da sucursal", "Branch details")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Resumo da unidade, contactos e equipa alocada.",
            "A summary of the branch, contact details, and assigned team members.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assigned_users = visible_users_queryset().filter(profile__assigned_branches=self.object).select_related(
            "profile"
        )
        context["assigned_users"] = assigned_users.order_by("first_name", "last_name", "username")
        context["default_users"] = assigned_users.filter(profile__default_branch=self.object)
        context["detail_partial"] = "accounts/organization/branches/includes/detail_content.html"
        context["modal_heading"] = self.object.name
        context["modal_description"] = ui_text(
            self.request,
            "Dados da sucursal e utilizadores atualmente alocados.",
            "Branch information and users currently assigned to it.",
        )
        return context


class BranchCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Branch
    form_class = BranchForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:branch_list")
    success_message = "Sucursal criada com sucesso."
    permission_required = "accounts.add_branch"
    segment = "branches"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Nova sucursal", "New branch")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe uma nova unidade e defina logo a equipa alocada.",
            "Register a new branch and assign its team right away.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar sucursal", "Create branch")
        context["form_description"] = ui_text(
            self.request,
            "Use esta área para registar uma nova sucursal e indicar quem pode operar nela.",
            "Use this area to register a new branch and define which users can operate there.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar sucursal", "Save branch")
        context["cancel_url"] = reverse("accounts:branch_list")
        context["form_mode"] = "branch"
        context["wide_fields"] = {"address", "description", "assigned_users"}
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Sucursal criada com sucesso.", "Branch created successfully.")


class BranchUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = Branch.objects.prefetch_related("user_profiles__user")
    form_class = BranchForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:branch_list")
    success_message = "Sucursal actualizada com sucesso."
    permission_required = "accounts.change_branch"
    segment = "branches"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar sucursal", "Edit branch")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize dados da unidade e a equipa autorizada para esta sucursal.",
            "Update the branch details and the team authorized for this location.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar sucursal", "Edit branch")
        context["form_description"] = ui_text(
            self.request,
            "As alterações passam a reflectir imediatamente na alocação dos utilizadores.",
            "Changes are immediately reflected in user branch assignments.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar sucursal", "Update branch")
        context["cancel_url"] = reverse("accounts:branch_detail", args=[self.object.pk])
        context["form_mode"] = "branch"
        context["wide_fields"] = {"address", "description", "assigned_users"}
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Sucursal actualizada com sucesso.", "Branch updated successfully.")


class BranchToggleStatusView(AppPermissionMixin, View):
    permission_required = "accounts.change_branch"
    login_url = "clinic:login"

    def post(self, request, pk):
        branch = get_object_or_404(Branch, pk=pk)
        branch.is_active = not branch.is_active
        branch.save(update_fields=["is_active", "updated_at"])

        if branch.is_active:
            message = ui_text(request, "Sucursal activada com sucesso.", "Branch activated successfully.")
        else:
            message = ui_text(
                request,
                "Sucursal desactivada com sucesso.",
                "Branch deactivated successfully.",
            )

        return JsonResponse(
            {
                "success": True,
                "message": message,
                "redirect_url": reverse("accounts:branch_list"),
            }
        )
