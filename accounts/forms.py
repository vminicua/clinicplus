import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from .i18n import translate_pair
from .models import Branch, SystemPreference, UserProfile
from .utils import build_permission_matrix, describe_permission_scope, visible_users_queryset


User = get_user_model()
tr = translate_pair


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
        label=tr("Palavra-passe", "Password"),
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text=tr(
            "Obrigatória ao criar. Na edição, preencha apenas se quiser alterar.",
            "Required when creating. On edit, fill it only if you want to change it.",
        ),
    )
    password2 = forms.CharField(
        label=tr("Confirmar palavra-passe", "Confirm password"),
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by("name"),
        required=False,
        label=tr("Perfis (roles)", "Roles"),
        help_text=tr(
            "Perfis que definem o acesso principal deste utilizador.",
            "Roles that define this user's main access.",
        ),
        widget=forms.CheckboxSelectMultiple,
    )
    preferred_language = forms.ChoiceField(
        choices=UserProfile.LANGUAGE_CHOICES,
        label=tr("Idioma preferido", "Preferred language"),
    )
    assigned_branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.order_by("name"),
        required=False,
        label=tr("Sucursais atribuídas", "Assigned branches"),
        help_text=tr(
            "Defina em que sucursais este utilizador pode operar.",
            "Choose which branches this user can operate in.",
        ),
        widget=forms.CheckboxSelectMultiple,
    )
    default_branch = forms.ModelChoiceField(
        queryset=Branch.objects.order_by("name"),
        required=False,
        label=tr("Sucursal principal", "Primary branch"),
        help_text=tr(
            "Opcional. Deve fazer parte das sucursais atribuídas.",
            "Optional. It must be one of the assigned branches.",
        ),
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
            "username": tr("Nome de utilizador", "Username"),
            "first_name": tr("Nome", "First name"),
            "last_name": tr("Apelido", "Last name"),
            "email": "Email",
            "is_active": tr("Activo", "Active"),
            "is_staff": tr("Acesso técnico", "Technical access"),
        }
        help_texts = {
            "username": tr("Identificador usado para entrar no sistema.", "Identifier used to sign in."),
            "email": tr("Contacto principal do utilizador.", "Main contact for this user."),
            "is_active": tr(
                "Desactive para bloquear o acesso sem apagar o registo.",
                "Disable to block access without deleting the record.",
            ),
            "is_staff": tr(
                "Use apenas quando este utilizador precisar de acesso técnico reservado.",
                "Use only when this user needs reserved technical access.",
            ),
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
            self.add_error(
                "password1",
                tr(
                    "A palavra-passe é obrigatória ao criar um utilizador.",
                    "Password is required when creating a user.",
                ),
            )

        if password1 or password2:
            if password1 != password2:
                self.add_error(
                    "password2",
                    tr(
                        "A confirmação da palavra-passe não coincide.",
                        "The password confirmation does not match.",
                    ),
                )

        if default_branch and assigned_branches is not None and default_branch not in assigned_branches:
            self.add_error(
                "default_branch",
                tr(
                    "Seleccione a sucursal principal a partir das sucursais atribuídas.",
                    "Select the primary branch from the assigned branches.",
                ),
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
        label=tr("Utilizadores alocados", "Allocated users"),
        help_text=tr(
            "Escolha quem pode operar nesta sucursal.",
            "Choose who can operate in this branch.",
        ),
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
            "name": tr("Nome da sucursal", "Branch name"),
            "code": tr("Código", "Code"),
            "city": tr("Cidade", "City"),
            "address": tr("Endereço", "Address"),
            "phone": tr("Telefone", "Phone"),
            "email": "Email",
            "is_active": tr("Activa", "Active"),
        }
        help_texts = {
            "code": tr(
                "Use um código curto para identificar a sucursal internamente.",
                "Use a short code to identify the branch internally.",
            ),
            "city": tr("Cidade principal desta unidade.", "Main city for this branch."),
            "address": tr(
                "Morada física ou referência da unidade.",
                "Physical address or a location reference for this branch.",
            ),
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
            "default_language": tr("Idioma do sistema", "System language"),
            "default_currency": tr("Moeda base", "Base currency"),
        }
        help_texts = {
            "default_language": tr(
                "Usado como idioma inicial quando o utilizador ainda não escolheu um idioma.",
                "Used as the initial language when the user has not chosen one yet.",
            ),
            "default_currency": tr(
                "Moeda usada por defeito no sistema. O valor inicial é Metical.",
                "Default currency used by the system. The initial value is Metical.",
            ),
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
        label=tr("Permissões atribuídas", "Assigned permissions"),
        help_text=tr(
            "Seleccione as permissões que compõem este perfil.",
            "Select the permissions that make up this role.",
        ),
        widget=forms.MultipleHiddenInput,
    )

    class Meta:
        model = Group
        fields = ["name", "permissions"]
        labels = {
            "name": tr("Nome do perfil", "Role name"),
        }
        help_texts = {
            "name": tr(
                "Ex.: Administrador do Sistema, Recepcionista, Médico.",
                "Example: System Administrator, Receptionist, Doctor.",
            ),
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
        label=tr("Módulo / entidade", "Module / entity"),
    )

    class Meta:
        model = Permission
        fields = ["name", "codename", "content_type"]
        labels = {
            "name": tr("Nome visível", "Visible name"),
            "codename": tr("Código interno", "Internal code"),
        }
        help_texts = {
            "name": tr(
                "Nome que será mostrado nas listas de permissões.",
                "Name that will be shown in permission lists.",
            ),
            "codename": tr(
                "Use letras minúsculas, números e underscore.",
                "Use lowercase letters, numbers, and underscore.",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()

    def clean_codename(self):
        codename = (self.cleaned_data.get("codename") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_]+", codename):
            raise forms.ValidationError(
                tr(
                    "Use apenas letras minúsculas, números e underscore no código interno.",
                    "Use only lowercase letters, numbers, and underscore in the internal code.",
                )
            )
        return codename
