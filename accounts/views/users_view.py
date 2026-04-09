from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.forms import UserForm
from accounts.ui import filter_users_for_branch, ui_text
from accounts.utils import visible_users_queryset
from clinic.models import Medico

from .base_view import AppPermissionMixin, ClinicPageMixin, ModalDetailMixin, ModalFormMixin


User = get_user_model()


class UserListView(AppPermissionMixin, ClinicPageMixin, ListView):
    template_name = "accounts/users/list.html"
    context_object_name = "users"
    permission_required = "auth.view_user"
    segment = "users"
    def get_page_title(self) -> str:
        return ui_text(self.request, "Utilizadores", "Users")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Gestão completa de contas, perfis de acesso e idioma preferido.",
            "Complete management of accounts, access roles, and preferred language.",
        )

    def get_queryset(self):
        return (
            filter_users_for_branch(visible_users_queryset(), self.request)
            .select_related(
                "profile",
                "profile__default_branch",
                "medico",
                "medico__especialidade",
                "medico__departamento",
                "medico__departamento__branch",
            )
            .prefetch_related("profile__assigned_branches")
            .prefetch_related("groups")
            .order_by("first_name", "last_name", "username")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = filter_users_for_branch(visible_users_queryset(), self.request)
        context["total_users"] = base_queryset.count()
        context["active_users"] = base_queryset.filter(is_active=True).count()
        context["staff_users"] = base_queryset.filter(is_staff=True).count()
        context["users_with_roles"] = base_queryset.filter(groups__isnull=False).distinct().count()
        return context


class UserDetailView(AppPermissionMixin, ModalDetailMixin, ClinicPageMixin, DetailView):
    template_name = "accounts/users/detail.html"
    permission_required = "auth.view_user"
    segment = "users"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Detalhes do utilizador", "User details")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Resumo do acesso e perfis atribuídos.",
            "Summary of access and assigned roles.",
        )

    def get_queryset(self):
        return (
            filter_users_for_branch(visible_users_queryset(), self.request)
            .select_related(
                "profile",
                "profile__default_branch",
                "medico",
                "medico__especialidade",
                "medico__departamento",
                "medico__departamento__branch",
            )
            .prefetch_related("profile__assigned_branches", "groups")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context["clinical_profile"] = self.object.medico
        except Medico.DoesNotExist:
            context["clinical_profile"] = None
        context["detail_partial"] = "accounts/users/includes/detail_content.html"
        context["modal_heading"] = self.object.get_full_name() or self.object.username
        context["modal_description"] = ui_text(
            self.request,
            "Resumo do acesso, idioma, sucursais e perfis atribuídos.",
            "Summary of access, language, branches, and assigned roles.",
        )
        return context


class UserCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = User
    form_class = UserForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:user_list")
    permission_required = "auth.add_user"
    segment = "users"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo utilizador", "New user")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Crie a conta, defina o idioma, os perfis e as sucursais do utilizador.",
            "Create the account and define the user's language, roles, and branches.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar utilizador", "Create user")
        context["form_description"] = ui_text(
            self.request,
            "Use esta área para registar uma nova conta de acesso ao sistema.",
            "Use this area to register a new access account in the system.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar utilizador", "Save user")
        context["cancel_url"] = reverse("accounts:user_list")
        context["form_mode"] = "user"
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Utilizador criado com sucesso.", "User created successfully.")


class UserUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    form_class = UserForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:user_list")
    permission_required = "auth.change_user"
    segment = "users"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar utilizador", "Edit user")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize dados, perfis e sucursais da conta seleccionada.",
            "Update data, roles, and branches for the selected account.",
        )

    def get_queryset(self):
        return (
            filter_users_for_branch(visible_users_queryset(), self.request)
            .select_related(
                "profile",
                "profile__default_branch",
                "medico",
                "medico__especialidade",
                "medico__departamento",
                "medico__departamento__branch",
            )
            .prefetch_related("profile__assigned_branches", "groups")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar utilizador", "Edit user")
        context["form_description"] = ui_text(
            self.request,
            "A palavra-passe só será alterada se preencher os dois campos de confirmação.",
            "The password will only change if you fill both confirmation fields.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar utilizador", "Update user")
        context["cancel_url"] = reverse("accounts:user_detail", args=[self.object.pk])
        context["form_mode"] = "user"
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Utilizador actualizado com sucesso.", "User updated successfully.")


class UserToggleStatusView(AppPermissionMixin, View):
    permission_required = "auth.change_user"
    login_url = "clinic:login"

    def post(self, request, pk):
        user = get_object_or_404(filter_users_for_branch(visible_users_queryset(), request), pk=pk)

        if user == request.user and user.is_active:
            return JsonResponse(
                {
                    "success": False,
                    "message": ui_text(
                        request,
                        "Não pode desactivar a sua própria conta enquanto a sessão estiver activa.",
                        "You cannot deactivate your own account while the session is active.",
                    ),
                },
                status=400,
            )

        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])

        action_label = ui_text(request, "activado", "activated") if user.is_active else ui_text(request, "desactivado", "deactivated")
        return JsonResponse(
            {
                "success": True,
                "message": ui_text(
                    request,
                    f"Utilizador {action_label} com sucesso.",
                    f"User {action_label} successfully.",
                ),
                "redirect_url": reverse("accounts:user_list"),
            }
        )
