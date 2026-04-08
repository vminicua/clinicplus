import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .models import Branch, SystemPreference, UserProfile
from .utils import build_permission_matrix, describe_permission_scope, visible_users_queryset


User = get_user_model()


class StyledFormMixin:
    text_input_class = "form-control"
    select_class = "form-select"
    multi_select_class = "form-select"
    checkbox_class = "form-check-input"

    def apply_widget_classes(self) -> None:
        for field in self.fields.values():
            widget = field.widget

            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = self.checkbox_class
                continue

            if isinstance(widget, forms.CheckboxSelectMultiple):
                continue

            if isinstance(widget, forms.SelectMultiple):
                widget.attrs["class"] = self.multi_select_class
                widget.attrs.setdefault("size", 12)
                continue

            if isinstance(widget, forms.Select):
                widget.attrs["class"] = self.select_class
                continue

            if isinstance(widget, forms.Textarea):
                widget.attrs["class"] = self.text_input_class
                widget.attrs.setdefault("rows", 4)
                continue

            widget.attrs["class"] = self.text_input_class


class PermissionMultipleChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj: Permission) -> str:
        return f"{obj.name} ({describe_permission_scope(obj)})"


class ContentTypeChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: ContentType) -> str:
        return f"{obj.app_label}.{obj.model}"


class UserForm(StyledFormMixin, forms.ModelForm):
    password1 = forms.CharField(
        label="Palavra-passe",
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Obrigatória ao criar. Na edição, preencha apenas se quiser alterar.",
    )
    password2 = forms.CharField(
        label="Confirmar palavra-passe",
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by("name"),
        required=False,
        label="Perfis (roles)",
        help_text="Perfis que definem o acesso principal deste utilizador.",
        widget=forms.CheckboxSelectMultiple,
    )
    preferred_language = forms.ChoiceField(
        choices=UserProfile.LANGUAGE_CHOICES,
        label="Idioma preferido",
    )
    assigned_branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.order_by("name"),
        required=False,
        label="Sucursais atribuídas",
        help_text="Defina em que sucursais este utilizador pode operar.",
        widget=forms.CheckboxSelectMultiple,
    )
    default_branch = forms.ModelChoiceField(
        queryset=Branch.objects.order_by("name"),
        required=False,
        label="Sucursal principal",
        help_text="Opcional. Deve fazer parte das sucursais atribuídas.",
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "is_staff",
            "groups",
        ]
        labels = {
            "username": "Nome de utilizador",
            "first_name": "Nome",
            "last_name": "Apelido",
            "email": "Email",
            "is_active": "Activo",
            "is_staff": "Acesso técnico",
        }
        help_texts = {
            "username": "Identificador usado para entrar no sistema.",
            "email": "Contacto principal do utilizador.",
            "is_active": "Desactive para bloquear o acesso sem apagar o registo.",
            "is_staff": "Use apenas quando este utilizador precisar de acesso técnico reservado.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_create = not bool(self.instance and self.instance.pk)
        self.apply_widget_classes()

        self.fields["username"].widget.attrs["autocomplete"] = "username"
        self.fields["email"].widget.attrs["autocomplete"] = "email"
        self.fields["groups"].queryset = Group.objects.order_by("name")
        self.fields["assigned_branches"].queryset = Branch.objects.order_by("name")
        self.fields["default_branch"].queryset = Branch.objects.order_by("name")

        if self.instance and self.instance.pk:
            profile, _ = UserProfile.objects.get_or_create(user=self.instance)
            self.fields["preferred_language"].initial = profile.preferred_language
            self.fields["assigned_branches"].initial = profile.assigned_branches.all()
            self.fields["default_branch"].initial = profile.default_branch
        else:
            self.fields["preferred_language"].initial = UserProfile.LANGUAGE_PORTUGUESE

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1", "")
        password2 = cleaned_data.get("password2", "")
        assigned_branches = cleaned_data.get("assigned_branches")
        default_branch = cleaned_data.get("default_branch")

        if self.is_create and not password1:
            self.add_error("password1", "A palavra-passe é obrigatória ao criar um utilizador.")

        if password1 or password2:
            if password1 != password2:
                self.add_error("password2", "A confirmação da palavra-passe não coincide.")

        if default_branch and assigned_branches is not None and default_branch not in assigned_branches:
            self.add_error(
                "default_branch",
                "Seleccione a sucursal principal a partir das sucursais atribuídas.",
            )

        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password1")

        if password:
            user.set_password(password)

        if not commit:
            return user

        user.save()
        self.save_m2m()
        user.user_permissions.clear()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.preferred_language = self.cleaned_data["preferred_language"]
        profile.default_branch = self.cleaned_data.get("default_branch")
        profile.save()
        profile.assigned_branches.set(self.cleaned_data.get("assigned_branches") or [])

        return user


class BranchForm(StyledFormMixin, forms.ModelForm):
    assigned_users = forms.ModelMultipleChoiceField(
        queryset=User.objects.order_by("first_name", "last_name", "username"),
        required=False,
        label="Utilizadores alocados",
        help_text="Escolha quem pode operar nesta sucursal.",
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Branch
        fields = [
            "name",
            "code",
            "city",
            "address",
            "phone",
            "email",
            "is_active",
        ]
        labels = {
            "name": "Nome da sucursal",
            "code": "Código",
            "city": "Cidade",
            "address": "Endereço",
            "phone": "Telefone",
            "email": "Email",
            "is_active": "Activa",
        }
        help_texts = {
            "code": "Use um código curto para identificar a sucursal internamente.",
            "city": "Cidade principal desta unidade.",
            "address": "Morada física ou referência da unidade.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        self.fields["assigned_users"].queryset = visible_users_queryset().order_by(
            "first_name",
            "last_name",
            "username",
        )

        if self.instance and self.instance.pk:
            self.fields["assigned_users"].initial = visible_users_queryset().filter(
                profile__assigned_branches=self.instance
            )

    @transaction.atomic
    def save(self, commit=True):
        branch = super().save(commit=commit)
        if not commit:
            return branch

        selected_users = self.cleaned_data.get("assigned_users") or []
        profiles_to_remove = UserProfile.objects.filter(
            assigned_branches=branch,
            user__is_superuser=False,
        ).exclude(user__in=selected_users)
        profiles_to_remove.update(default_branch=None)
        for profile in profiles_to_remove:
            profile.assigned_branches.remove(branch)

        for user in selected_users:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.assigned_branches.add(branch)
            if profile.default_branch_id is None:
                profile.default_branch = branch
                profile.save(update_fields=["default_branch", "updated_at"])

        return branch


class SystemPreferenceForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SystemPreference
        fields = ["default_language", "default_currency"]
        labels = {
            "default_language": "Idioma do sistema",
            "default_currency": "Moeda base",
        }
        help_texts = {
            "default_language": "Usado como idioma inicial quando o utilizador ainda não escolheu um idioma.",
            "default_currency": "Moeda usada por defeito no sistema. O valor inicial é Metical.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()


class RoleForm(StyledFormMixin, forms.ModelForm):
    permissions = PermissionMultipleChoiceField(
        queryset=Permission.objects.select_related("content_type").order_by(
            "content_type__app_label",
            "content_type__model",
            "name",
        ),
        required=False,
        label="Permissões atribuídas",
        help_text="Seleccione as permissões que compõem este perfil.",
        widget=forms.MultipleHiddenInput,
    )

    class Meta:
        model = Group
        fields = ["name", "permissions"]
        labels = {
            "name": "Nome do perfil",
        }
        help_texts = {
            "name": "Ex.: Administrador do Sistema, Recepcionista, Médico.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        self.permission_matrix = build_permission_matrix(self.get_selected_permission_ids())

    def get_selected_permission_ids(self):
        if self.is_bound:
            values = self.data.getlist("permissions")
        elif self.instance.pk:
            values = self.instance.permissions.values_list("id", flat=True)
        else:
            values = self.initial.get("permissions", [])
        return {int(value) for value in values}


class PermissionForm(StyledFormMixin, forms.ModelForm):
    content_type = ContentTypeChoiceField(
        queryset=ContentType.objects.order_by("app_label", "model"),
        label="Módulo / entidade",
    )

    class Meta:
        model = Permission
        fields = ["name", "codename", "content_type"]
        labels = {
            "name": "Nome visível",
            "codename": "Código interno",
        }
        help_texts = {
            "name": "Nome que será mostrado nas listas de permissões.",
            "codename": "Use letras minúsculas, números e underscore.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()

    def clean_codename(self):
        codename = (self.cleaned_data.get("codename") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_]+", codename):
            raise forms.ValidationError(
                "Use apenas letras minúsculas, números e underscore no código interno."
            )
        return codename
