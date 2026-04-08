from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.forms import UserForm
from accounts.utils import visible_users_queryset

from .base_view import AppPermissionMixin, ClinicPageMixin, ModalDetailMixin, ModalFormMixin


User = get_user_model()


class UserListView(AppPermissionMixin, ClinicPageMixin, ListView):
    template_name = "accounts/users/list.html"
    context_object_name = "users"
    permission_required = "auth.view_user"
    segment = "users"
    page_title = "Utilizadores"
    page_subtitle = "Gestão completa de contas, perfis de acesso e idioma preferido."

    def get_queryset(self):
        return (
            visible_users_queryset()
            .select_related("profile")
            .prefetch_related("groups")
            .order_by("first_name", "last_name", "username")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = visible_users_queryset()
        context["total_users"] = base_queryset.count()
        context["active_users"] = base_queryset.filter(is_active=True).count()
        context["staff_users"] = base_queryset.filter(is_staff=True).count()
        context["users_with_roles"] = base_queryset.filter(groups__isnull=False).distinct().count()
        return context


class UserDetailView(AppPermissionMixin, ModalDetailMixin, ClinicPageMixin, DetailView):
    queryset = visible_users_queryset().select_related("profile").prefetch_related("groups")
    template_name = "accounts/users/detail.html"
    permission_required = "auth.view_user"
    segment = "users"
    page_title = "Detalhes do utilizador"
    page_subtitle = "Resumo do acesso e perfis atribuídos."
    modal_size = "modal-xl"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["detail_partial"] = "accounts/users/includes/detail_content.html"
        context["modal_heading"] = self.object.get_full_name() or self.object.username
        context["modal_description"] = "Resumo do acesso, idioma e perfis atribuídos."
        return context


class UserCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = User
    form_class = UserForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:user_list")
    success_message = "Utilizador criado com sucesso."
    permission_required = "auth.add_user"
    segment = "users"
    page_title = "Novo utilizador"
    page_subtitle = "Crie a conta, defina o idioma e associe perfis de acesso."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Criar utilizador"
        context["form_description"] = "Use esta área para registar uma nova conta de acesso ao sistema."
        context["submit_label"] = "Guardar utilizador"
        context["cancel_url"] = reverse("accounts:user_list")
        context["form_mode"] = "user"
        return context


class UserUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = visible_users_queryset().select_related("profile").prefetch_related("groups")
    form_class = UserForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:user_list")
    success_message = "Utilizador actualizado com sucesso."
    permission_required = "auth.change_user"
    segment = "users"
    page_title = "Editar utilizador"
    page_subtitle = "Actualize dados e perfis da conta seleccionada."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = "Editar utilizador"
        context["form_description"] = "A palavra-passe só será alterada se preencher os dois campos de confirmação."
        context["submit_label"] = "Actualizar utilizador"
        context["cancel_url"] = reverse("accounts:user_detail", args=[self.object.pk])
        context["form_mode"] = "user"
        return context


class UserToggleStatusView(AppPermissionMixin, View):
    permission_required = "auth.change_user"
    login_url = "clinic:login"

    def post(self, request, pk):
        user = get_object_or_404(visible_users_queryset(), pk=pk)

        if user == request.user and user.is_active:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Não pode desactivar a sua própria conta enquanto a sessão estiver activa.",
                },
                status=400,
            )

        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])

        action_label = "activado" if user.is_active else "desactivado"
        return JsonResponse(
            {
                "success": True,
                "message": f"Utilizador {action_label} com sucesso.",
                "redirect_url": reverse("accounts:user_list"),
            }
        )
