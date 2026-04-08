from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.models import Group, Permission
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .forms import PermissionForm, RoleForm, UserForm
from .utils import is_system_permission, visible_users_queryset


User = get_user_model()


def is_modal_request(request) -> bool:
    return (
        request.GET.get("modal") == "1"
        or request.POST.get("modal") == "1"
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
    )


class AppPermissionMixin(LoginRequiredMixin, PermissionRequiredMixin):
    login_url = "clinic:login"
    raise_exception = False

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(
                self.request,
                "Não tem permissões suficientes para aceder a esta área de gestão.",
            )
            return redirect("clinic:index")
        return super().handle_no_permission()


class ClinicPageMixin(LoginRequiredMixin):
    login_url = "clinic:login"
    page_title = ""
    page_subtitle = ""
    segment = ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        greeting_name = (
            self.request.user.get_short_name()
            or self.request.user.first_name
            or self.request.user.username
        )

        context.setdefault("segment", self.segment)
        context.setdefault("page_title", self.page_title)
        context.setdefault("page_subtitle", self.page_subtitle)
        context.setdefault("meta_title", f"Clinic Plus | {self.page_title}")
        context.setdefault("current_date", timezone.localdate())
        context.setdefault("greeting_name", greeting_name)
        context.setdefault("is_modal_request", is_modal_request(self.request))
        return context


class SearchableListMixin(ClinicPageMixin):
    search_placeholder = "Pesquisar"

    def get_search_query(self) -> str:
        return (self.request.GET.get("q") or "").strip()

    def get_pagination_query(self) -> str:
        params = self.request.GET.copy()
        params.pop("page", None)
        encoded = params.urlencode()
        return f"&{encoded}" if encoded else ""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["search_enabled"] = True
        context["search_query"] = self.get_search_query()
        context["search_action"] = self.request.path
        context["nav_search_placeholder"] = self.search_placeholder
        context["pagination_query"] = self.get_pagination_query()
        return context


class ModalResponseMixin:
    modal_template_name = ""
    success_message = ""
    modal_size = "modal-lg"

    def is_modal(self) -> bool:
        return is_modal_request(self.request)

    def get_template_names(self):
        if self.is_modal() and self.modal_template_name:
            return [self.modal_template_name]
        return [self.template_name]

    def get_success_message(self) -> str:
        return self.success_message

    def get_modal_title(self) -> str:
        return self.page_title

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("modal_title", self.get_modal_title())
        context.setdefault("modal_size", self.modal_size)
        return context


class ModalFormMixin(ModalResponseMixin):
    error_message = "Revise os campos destacados e tente novamente."

    def form_valid(self, form):
        self.object = form.save()
        if self.is_modal():
            return JsonResponse(
                {
                    "success": True,
                    "message": self.get_success_message(),
                    "reload": True,
                }
            )

        messages.success(self.request, self.get_success_message())
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        if self.is_modal():
            return self.render_to_response(self.get_context_data(form=form), status=422)

        messages.error(self.request, self.error_message)
        return self.render_to_response(self.get_context_data(form=form), status=422)


class ModalDeleteMixin(ModalResponseMixin):
    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        success_url = self.get_success_url()
        self.object.delete()

        if self.is_modal():
            return JsonResponse(
                {
                    "success": True,
                    "message": self.get_success_message(),
                    "reload": True,
                }
            )

        messages.success(self.request, self.get_success_message())
        return redirect(success_url)


class UserListView(AppPermissionMixin, SearchableListMixin, ListView):
    model = User
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 10
    permission_required = "auth.view_user"
    segment = "users"
    page_title = "Utilizadores"
    page_subtitle = "Gestão completa de contas, perfis de acesso e idioma preferido."
    search_placeholder = "Pesquisar por nome, utilizador ou email"

    def get_queryset(self):
        query = self.get_search_query()
        queryset = (
            visible_users_queryset()
            .select_related("profile")
            .prefetch_related("groups")
            .order_by("first_name", "last_name", "username")
        )

        if query:
            queryset = queryset.filter(
                Q(username__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = visible_users_queryset()
        context["total_users"] = base_queryset.count()
        context["active_users"] = base_queryset.filter(is_active=True).count()
        context["staff_users"] = base_queryset.filter(is_staff=True).count()
        context["users_with_roles"] = base_queryset.filter(groups__isnull=False).distinct().count()
        context["primary_action_modal_url"] = reverse("accounts:user_create")
        context["primary_action_label"] = "Novo utilizador"
        context["primary_action_icon"] = "person_add"
        context["primary_action_modal_size"] = "modal-lg"
        return context


class UserDetailView(AppPermissionMixin, ClinicPageMixin, DetailView):
    queryset = visible_users_queryset().select_related("profile").prefetch_related("groups")
    template_name = "accounts/user_detail.html"
    permission_required = "auth.view_user"
    segment = "users"
    page_title = "Detalhes do utilizador"
    page_subtitle = "Resumo do acesso e perfis atribuídos."


class UserCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = User
    form_class = UserForm
    template_name = "accounts/form.html"
    modal_template_name = "accounts/modal_form.html"
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
    template_name = "accounts/form.html"
    modal_template_name = "accounts/modal_form.html"
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


class UserDeleteView(AppPermissionMixin, ModalDeleteMixin, ClinicPageMixin, DeleteView):
    queryset = visible_users_queryset().select_related("profile").prefetch_related("groups")
    template_name = "accounts/confirm_delete.html"
    modal_template_name = "accounts/modal_confirm_delete.html"
    success_url = reverse_lazy("accounts:user_list")
    success_message = "Utilizador eliminado com sucesso."
    permission_required = "auth.delete_user"
    segment = "users"
    page_title = "Eliminar utilizador"
    page_subtitle = "Confirme a remoção apenas quando tiver a certeza."

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()

        if self.object == request.user:
            messages.error(
                request,
                "Não pode eliminar a sua própria conta enquanto a sessão estiver activa.",
            )
            return redirect("accounts:user_detail", pk=self.object.pk)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["delete_title"] = "Eliminar utilizador"
        context["delete_description"] = "A conta será removida juntamente com o perfil associado."
        context["delete_object_name"] = self.object.get_full_name() or self.object.username
        context["cancel_url"] = reverse("accounts:user_detail", args=[self.object.pk])
        context["impact_lines"] = [
            f"Nome de utilizador: {self.object.username}",
            f"Perfis atribuídos: {self.object.groups.count()}",
            f"Estado actual: {'Activo' if self.object.is_active else 'Inactivo'}",
        ]
        return context


class RoleListView(AppPermissionMixin, SearchableListMixin, ListView):
    model = Group
    template_name = "accounts/role_list.html"
    context_object_name = "roles"
    paginate_by = 10
    permission_required = "auth.view_group"
    segment = "roles"
    page_title = "Perfis e roles"
    page_subtitle = "Agrupe permissões por função operacional da clínica."
    search_placeholder = "Pesquisar por nome do perfil"

    def get_queryset(self):
        query = self.get_search_query()
        visible_users = visible_users_queryset().order_by("first_name", "last_name", "username")
        queryset = Group.objects.prefetch_related(
            "permissions",
            Prefetch("user_set", queryset=visible_users),
        ).order_by("name")

        if query:
            queryset = queryset.filter(name__icontains=query)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = Group.objects.all()
        context["total_roles"] = base_queryset.count()
        context["roles_in_use"] = base_queryset.filter(user__is_superuser=False).distinct().count()
        context["roles_with_permissions"] = base_queryset.filter(
            permissions__isnull=False
        ).distinct().count()
        context["linked_users"] = visible_users_queryset().filter(groups__isnull=False).distinct().count()
        context["primary_action_modal_url"] = reverse("accounts:role_create")
        context["primary_action_label"] = "Novo perfil"
        context["primary_action_icon"] = "badge"
        context["primary_action_modal_size"] = "modal-xl"
        return context


class RoleDetailView(AppPermissionMixin, ClinicPageMixin, DetailView):
    queryset = Group.objects.prefetch_related(
        "permissions",
        Prefetch("user_set", queryset=visible_users_queryset().order_by("first_name", "last_name", "username")),
    )
    template_name = "accounts/role_detail.html"
    permission_required = "auth.view_group"
    segment = "roles"
    page_title = "Detalhes do perfil"
    page_subtitle = "Veja o conjunto de permissões e os utilizadores ligados a este perfil."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["ordered_permissions"] = self.object.permissions.select_related(
            "content_type"
        ).order_by("content_type__app_label", "content_type__model", "name")
        context["assigned_users"] = self.object.user_set.all()
        return context


class RoleCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Group
    form_class = RoleForm
    template_name = "accounts/form.html"
    modal_template_name = "accounts/modal_form.html"
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
    template_name = "accounts/form.html"
    modal_template_name = "accounts/modal_form.html"
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


class RoleDeleteView(AppPermissionMixin, ModalDeleteMixin, ClinicPageMixin, DeleteView):
    queryset = Group.objects.prefetch_related(
        Prefetch("user_set", queryset=visible_users_queryset().order_by("first_name", "last_name", "username"))
    )
    template_name = "accounts/confirm_delete.html"
    modal_template_name = "accounts/modal_confirm_delete.html"
    success_url = reverse_lazy("accounts:role_list")
    success_message = "Perfil eliminado com sucesso."
    permission_required = "auth.delete_group"
    segment = "roles"
    page_title = "Eliminar perfil"
    page_subtitle = "Confirme a remoção do perfil seleccionado."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["delete_title"] = "Eliminar perfil"
        context["delete_description"] = "Os utilizadores deixam de herdar as permissões deste perfil."
        context["delete_object_name"] = self.object.name
        context["cancel_url"] = reverse("accounts:role_detail", args=[self.object.pk])
        context["impact_lines"] = [
            f"Utilizadores associados: {self.object.user_set.count()}",
            f"Permissões incluídas: {self.object.permissions.count()}",
        ]
        return context


class EditablePermissionMixin:
    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()

        if is_system_permission(self.object):
            messages.warning(
                request,
                "As permissões base do sistema estão protegidas. Pode consultá-las, mas não editá-las nem eliminá-las.",
            )
            return redirect("accounts:permission_detail", pk=self.object.pk)

        return super().dispatch(request, *args, **kwargs)


class PermissionListView(AppPermissionMixin, SearchableListMixin, ListView):
    model = Permission
    template_name = "accounts/permission_list.html"
    context_object_name = "permissions"
    paginate_by = 15
    permission_required = "auth.view_permission"
    segment = "permissions"
    page_title = "Permissões"
    page_subtitle = "Consulte permissões do sistema e crie permissões personalizadas quando necessário."
    search_placeholder = "Pesquisar por nome, código ou módulo"

    def get_kind_filter(self) -> str:
        return (self.request.GET.get("kind") or "all").strip().lower()

    def get_queryset(self):
        query = self.get_search_query()
        queryset = Permission.objects.select_related("content_type").order_by(
            "content_type__app_label",
            "content_type__model",
            "name",
        )

        if query:
            queryset = queryset.filter(
                Q(name__icontains=query)
                | Q(codename__icontains=query)
                | Q(content_type__app_label__icontains=query)
                | Q(content_type__model__icontains=query)
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
        context["primary_action_modal_url"] = reverse("accounts:permission_create")
        context["primary_action_label"] = "Nova permissão"
        context["primary_action_icon"] = "verified_user"
        context["primary_action_modal_size"] = "modal-lg"
        return context


class PermissionDetailView(AppPermissionMixin, ClinicPageMixin, DetailView):
    queryset = Permission.objects.select_related("content_type").prefetch_related("group_set")
    template_name = "accounts/permission_detail.html"
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
    template_name = "accounts/form.html"
    modal_template_name = "accounts/modal_form.html"
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
    template_name = "accounts/form.html"
    modal_template_name = "accounts/modal_form.html"
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


class PermissionDeleteView(
    EditablePermissionMixin,
    AppPermissionMixin,
    ModalDeleteMixin,
    ClinicPageMixin,
    DeleteView,
):
    queryset = Permission.objects.select_related("content_type").prefetch_related("group_set")
    template_name = "accounts/confirm_delete.html"
    modal_template_name = "accounts/modal_confirm_delete.html"
    success_url = reverse_lazy("accounts:permission_list")
    success_message = "Permissão eliminada com sucesso."
    permission_required = "auth.delete_permission"
    segment = "permissions"
    page_title = "Eliminar permissão"
    page_subtitle = "Remova apenas permissões personalizadas que já não sejam necessárias."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["delete_title"] = "Eliminar permissão"
        context["delete_description"] = "Os perfis deixam de herdar este acesso assim que a remoção for concluída."
        context["delete_object_name"] = self.object.name
        context["cancel_url"] = reverse("accounts:permission_detail", args=[self.object.pk])
        context["impact_lines"] = [
            f"Código interno: {self.object.codename}",
            f"Perfis ligados: {self.object.group_set.count()}",
        ]
        return context
