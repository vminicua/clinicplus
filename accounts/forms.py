import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q

from clinic.models import Departamento, Especialidade, Hospital, Medico

from .i18n import translate_pair
from .models import Branch, Clinic, MeasurementUnit, SystemPreference, UserProfile
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
                widget.attrs["data-searchable-select"] = "1"
                widget.attrs.setdefault("size", 12)
                continue

            if isinstance(widget, forms.Select):
                widget.attrs["class"] = self.select_class
                widget.attrs["data-searchable-select"] = "1"
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


class ClinicalSpecialtyChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: Especialidade) -> str:
        return obj.name


class ClinicalDepartmentChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: Departamento) -> str:
        unit_name = obj.unit_name or tr("Sem unidade", "No unit")
        return f"{obj.name} · {unit_name}"


def doctor_group():
    return Group.objects.filter(name="Médico").first() or Group.objects.filter(pk=4).first()


def has_doctor_role(groups) -> bool:
    doctor_role = doctor_group()
    if doctor_role is None or groups is None:
        return False
    group_ids = {group.pk for group in groups}
    return doctor_role.pk in group_ids


def generate_medico_crm(user: User) -> str:
    base_value = f"AUTOCRM{user.pk or 'NEW'}"
    candidate = base_value
    counter = 1
    while Medico.objects.filter(crm=candidate).exclude(user=user).exists():
        candidate = f"{base_value}_{counter}"
        counter += 1
    return candidate


def get_default_clinic():
    return Clinic.objects.filter(is_active=True).order_by("name", "id").first() or Clinic.objects.order_by(
        "name",
        "id",
    ).first()


def resolve_legacy_hospital_for_branch(branch: Branch | None):
    if branch is not None and getattr(branch, "clinic_id", None):
        matched = Hospital.objects.filter(name__iexact=branch.clinic.name).first()
        if matched:
            return matched

    if branch is None:
        return Hospital.objects.order_by("id").first()

    matched = Hospital.objects.filter(name__iexact=branch.name).first()
    if matched:
        return matched

    existing = Hospital.objects.order_by("id").first()
    if existing and Hospital.objects.count() == 1:
        return existing

    return None


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
    medical_specialty = ClinicalSpecialtyChoiceField(
        queryset=Especialidade.objects.order_by("name"),
        required=False,
        label=tr("Especialidade clínica", "Clinical specialty"),
    )
    medical_department = ClinicalDepartmentChoiceField(
        queryset=Departamento.objects.select_related("branch").order_by("branch__name", "name"),
        required=False,
        label=tr("Departamento clínico", "Clinical department"),
    )
    medical_crm = forms.CharField(
        max_length=20,
        required=False,
        label=tr("Cédula / CRM", "License / CRM"),
    )
    medical_phone = forms.CharField(
        max_length=20,
        required=False,
        label=tr("Telefone clínico", "Clinical phone"),
    )
    medical_bio = forms.CharField(
        required=False,
        label=tr("Perfil clínico", "Clinical profile"),
        widget=forms.Textarea(attrs={"rows": 4}),
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
        if self.instance and self.instance.pk:
            try:
                self.doctor_profile = self.instance.medico
            except Medico.DoesNotExist:
                self.doctor_profile = None
        else:
            self.doctor_profile = None

        self.fields["username"].widget.attrs["autocomplete"] = "username"
        self.fields["email"].widget.attrs["autocomplete"] = "email"
        self.fields["groups"].queryset = Group.objects.order_by("name")
        self.fields["assigned_branches"].queryset = Branch.objects.order_by("name")
        self.fields["default_branch"].queryset = Branch.objects.order_by("name")
        self.fields["medical_specialty"].queryset = Especialidade.objects.order_by("name")
        self.fields["medical_department"].queryset = Departamento.objects.select_related("branch").order_by(
            "branch__name",
            "name",
        )
        self.fields["medical_specialty"].help_text = tr(
            "Designação clínica do médico, por exemplo Ginecologista ou Pediatra.",
            "Doctor's clinical designation, for example Gynecologist or Pediatrician.",
        )
        self.fields["medical_department"].help_text = tr(
            "Serviço clínico ao qual este médico pertence.",
            "Clinical service this doctor belongs to.",
        )
        self.fields["medical_crm"].help_text = tr(
            "Opcional. Se deixar vazio, o sistema gera um identificador provisório.",
            "Optional. If left empty, the system generates a provisional identifier.",
        )

        if self.instance and self.instance.pk:
            profile, _ = UserProfile.objects.get_or_create(user=self.instance)
            self.fields["preferred_language"].initial = profile.preferred_language
            self.fields["assigned_branches"].initial = profile.assigned_branches.all()
            self.fields["default_branch"].initial = profile.default_branch
            if self.doctor_profile is not None:
                self.fields["medical_specialty"].initial = self.doctor_profile.especialidade
                self.fields["medical_department"].initial = self.doctor_profile.departamento
                self.fields["medical_crm"].initial = self.doctor_profile.crm
                self.fields["medical_phone"].initial = self.doctor_profile.phone
                self.fields["medical_bio"].initial = self.doctor_profile.bio
        else:
            self.fields["preferred_language"].initial = UserProfile.LANGUAGE_PORTUGUESE

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1", "")
        password2 = cleaned_data.get("password2", "")
        assigned_branches = cleaned_data.get("assigned_branches")
        default_branch = cleaned_data.get("default_branch")
        groups = cleaned_data.get("groups")
        medical_specialty = cleaned_data.get("medical_specialty")
        medical_department = cleaned_data.get("medical_department")
        medical_crm = (cleaned_data.get("medical_crm") or "").strip()
        medical_phone = (cleaned_data.get("medical_phone") or "").strip()
        medical_bio = (cleaned_data.get("medical_bio") or "").strip()
        wants_clinical_profile = any(
            value
            for value in (
                medical_specialty,
                medical_department,
                medical_crm,
                medical_phone,
                medical_bio,
            )
        )
        has_medical_role = has_doctor_role(groups)

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

        if wants_clinical_profile and not has_medical_role:
            self.add_error(
                "groups",
                tr(
                    "Para preencher o perfil clínico, atribua primeiro o perfil Médico a este utilizador.",
                    "To fill in the clinical profile, assign the Doctor role to this user first.",
                ),
            )

        if medical_department and medical_department.branch_id:
            allowed_branches = set()
            if assigned_branches is not None:
                allowed_branches.update(branch.pk for branch in assigned_branches)
            if default_branch is not None:
                allowed_branches.add(default_branch.pk)
            if allowed_branches and medical_department.branch_id not in allowed_branches:
                self.add_error(
                    "medical_department",
                    tr(
                        "Seleccione um departamento pertencente a uma das sucursais atribuídas ao utilizador.",
                        "Select a department that belongs to one of the branches assigned to the user.",
                    ),
                )

        if medical_crm:
            duplicate_qs = Medico.objects.filter(crm=medical_crm)
            if self.instance and self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(user=self.instance)
            if duplicate_qs.exists():
                self.add_error(
                    "medical_crm",
                    tr(
                        "Já existe outro médico com esta cédula/CRM.",
                        "There is already another doctor with this license/CRM.",
                    ),
                )

        cleaned_data["medical_crm"] = medical_crm
        cleaned_data["medical_phone"] = medical_phone
        cleaned_data["medical_bio"] = medical_bio
        cleaned_data["has_medical_role"] = has_medical_role
        cleaned_data["wants_clinical_profile"] = wants_clinical_profile
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

        should_persist_clinical_profile = (
            self.cleaned_data.get("has_medical_role")
            or self.cleaned_data.get("wants_clinical_profile")
            or self.doctor_profile is not None
        )
        if should_persist_clinical_profile:
            specialty = self.cleaned_data.get("medical_specialty")
            department = self.cleaned_data.get("medical_department")
            preferred_branch = (
                department.branch
                if department and department.branch_id
                else self.cleaned_data.get("default_branch")
            )
            if preferred_branch is None:
                assigned = list(self.cleaned_data.get("assigned_branches") or [])
                preferred_branch = assigned[0] if assigned else None

            doctor_profile, _ = Medico.objects.get_or_create(
                user=user,
                defaults={
                    "hospital": resolve_legacy_hospital_for_branch(preferred_branch),
                    "crm": self.cleaned_data.get("medical_crm") or generate_medico_crm(user),
                    "phone": self.cleaned_data.get("medical_phone") or "",
                    "bio": self.cleaned_data.get("medical_bio") or "",
                },
            )
            doctor_profile.especialidade = specialty
            doctor_profile.departamento = department
            doctor_profile.phone = self.cleaned_data.get("medical_phone") or ""
            doctor_profile.bio = self.cleaned_data.get("medical_bio") or ""
            doctor_profile.crm = self.cleaned_data.get("medical_crm") or doctor_profile.crm or generate_medico_crm(user)
            resolved_hospital = resolve_legacy_hospital_for_branch(preferred_branch)
            if resolved_hospital is not None or doctor_profile.hospital_id is None:
                doctor_profile.hospital = resolved_hospital
            doctor_profile.save()

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
            "clinic",
            "name",
            "code",
            "legal_name",
            "nuit",
            "logo",
            "favicon",
            "city",
            "province",
            "country",
            "postal_code",
            "address",
            "phone",
            "email",
            "website",
            "manager_name",
            "manager_phone",
            "manager_email",
            "description",
            "is_active",
        ]
        labels = {
            "clinic": tr("Clínica-mãe", "Parent clinic"),
            "name": tr("Nome da sucursal", "Branch name"),
            "code": tr("Código", "Code"),
            "legal_name": tr("Nome legal", "Legal name"),
            "nuit": tr("NUIT", "Tax ID"),
            "logo": tr("Logotipo", "Logo"),
            "favicon": tr("Favicon", "Favicon"),
            "city": tr("Cidade", "City"),
            "province": tr("Província / estado", "Province / state"),
            "country": tr("País", "Country"),
            "postal_code": tr("Código postal", "Postal code"),
            "address": tr("Endereço", "Address"),
            "phone": tr("Telefone", "Phone"),
            "email": "Email",
            "website": tr("Website", "Website"),
            "manager_name": tr("Responsável", "Manager"),
            "manager_phone": tr("Telefone do responsável", "Manager phone"),
            "manager_email": tr("Email do responsável", "Manager email"),
            "description": tr("Descrição", "Description"),
            "is_active": tr("Activa", "Active"),
        }
        help_texts = {
            "clinic": tr(
                "Clínica à qual esta sucursal pertence.",
                "Clinic this branch belongs to.",
            ),
            "code": tr(
                "Use um código curto para identificar a sucursal internamente.",
                "Use a short code to identify the branch internally.",
            ),
            "legal_name": tr(
                "Opcional. Preencha se a razão social for diferente do nome público da sucursal.",
                "Optional. Fill this when the legal entity name differs from the branch display name.",
            ),
            "nuit": tr(
                "Identificador fiscal da unidade. Guardamos apenas números.",
                "Fiscal identifier for this branch. We store digits only.",
            ),
            "logo": tr(
                "Imagem principal usada na identidade visual desta sucursal.",
                "Primary image used in this branch's brand identity.",
            ),
            "favicon": tr(
                "Ícone pequeno para separadores do navegador e atalhos.",
                "Small icon for browser tabs and shortcuts.",
            ),
            "city": tr("Cidade principal desta unidade.", "Main city for this branch."),
            "province": tr(
                "Província, estado ou região administrativa da unidade.",
                "Province, state, or administrative region for this branch.",
            ),
            "country": tr(
                "País onde esta sucursal opera. O padrão sugerido é Moçambique.",
                "Country where this branch operates. Mozambique is the suggested default.",
            ),
            "postal_code": tr(
                "Opcional. Útil para documentação, facturação e logística.",
                "Optional. Useful for documentation, billing, and logistics.",
            ),
            "address": tr(
                "Morada física ou referência da unidade.",
                "Physical address or a location reference for this branch.",
            ),
            "website": tr(
                "Link público desta unidade, se existir.",
                "Public website for this branch, if one exists.",
            ),
            "manager_name": tr(
                "Pessoa de contacto principal para esta sucursal.",
                "Main point of contact for this branch.",
            ),
            "manager_phone": tr(
                "Contacto directo do responsável local.",
                "Direct contact number for the local manager.",
            ),
            "manager_email": tr(
                "Email institucional do responsável local.",
                "Institutional email for the local manager.",
            ),
            "description": tr(
                "Use este campo para observações operacionais, horário, especialização ou notas internas.",
                "Use this field for operational notes, opening hours, specialization, or internal remarks.",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        clinic_queryset = Clinic.objects.filter(is_active=True)
        if self.instance and self.instance.pk and self.instance.clinic_id:
            clinic_queryset = Clinic.objects.filter(Q(is_active=True) | Q(pk=self.instance.clinic_id))
        self.fields["clinic"].queryset = clinic_queryset.distinct().order_by("name")
        self.fields["clinic"].required = True
        self.fields["assigned_users"].queryset = visible_users_queryset().order_by(
            "first_name",
            "last_name",
            "username",
        )

        if not self.instance.pk and not self.initial.get("clinic"):
            default_clinic = get_default_clinic()
            if default_clinic is not None:
                self.fields["clinic"].initial = default_clinic

        if self.instance and self.instance.pk:
            self.fields["assigned_users"].initial = visible_users_queryset().filter(
                profile__assigned_branches=self.instance
            )

        autocomplete_map = {
            "clinic": "section-branch organization",
            "name": "section-branch organization",
            "code": "off",
            "legal_name": "section-branch organization",
            "nuit": "off",
            "city": "section-branch address-level2",
            "province": "section-branch address-level1",
            "country": "section-branch country-name",
            "postal_code": "section-branch postal-code",
            "address": "section-branch street-address",
            "phone": "section-branch tel",
            "email": "section-branch email",
            "website": "section-branch url",
            "manager_name": "section-branch name",
            "manager_phone": "section-branch tel-national",
            "manager_email": "section-branch email",
            "description": "off",
        }

        for field_name, autocomplete_value in autocomplete_map.items():
            if field_name not in self.fields:
                continue
            self.fields[field_name].widget.attrs["autocomplete"] = autocomplete_value
            self.fields[field_name].widget.attrs["data-lpignore"] = "true"
            self.fields[field_name].widget.attrs["data-1p-ignore"] = "true"

        for image_field in ("logo", "favicon"):
            self.fields[image_field].widget.attrs["accept"] = "image/*"

    def clean_nuit(self):
        nuit = re.sub(r"\D", "", (self.cleaned_data.get("nuit") or ""))
        if not nuit:
            return None
        if len(nuit) != 9:
            raise forms.ValidationError(
                tr(
                    "O NUIT deve ter 9 dígitos.",
                    "The tax ID must have 9 digits.",
                )
            )
        return nuit

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


class ClinicForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Clinic
        fields = [
            "name",
            "legal_name",
            "nuit",
            "logo",
            "favicon",
            "city",
            "province",
            "country",
            "postal_code",
            "address",
            "phone",
            "email",
            "website",
            "description",
            "is_active",
        ]
        labels = {
            "name": tr("Nome da clínica", "Clinic name"),
            "legal_name": tr("Nome legal", "Legal name"),
            "nuit": tr("NUIT", "Tax ID"),
            "logo": tr("Logotipo", "Logo"),
            "favicon": tr("Favicon", "Favicon"),
            "city": tr("Cidade", "City"),
            "province": tr("Província / estado", "Province / state"),
            "country": tr("País", "Country"),
            "postal_code": tr("Código postal", "Postal code"),
            "address": tr("Endereço", "Address"),
            "phone": tr("Telefone", "Phone"),
            "email": "Email",
            "website": tr("Website", "Website"),
            "description": tr("Descrição", "Description"),
            "is_active": tr("Activa", "Active"),
        }
        help_texts = {
            "name": tr(
                "Nome principal da clínica mãe.",
                "Main name of the parent clinic.",
            ),
            "legal_name": tr(
                "Opcional. Use quando a razão social for diferente do nome público.",
                "Optional. Use when the legal entity name differs from the public name.",
            ),
            "nuit": tr(
                "Identificador fiscal da clínica. Guardamos apenas números.",
                "Clinic tax identifier. We store digits only.",
            ),
            "address": tr(
                "Endereço principal ou sede da clínica.",
                "Main address or headquarters of the clinic.",
            ),
            "description": tr(
                "Resumo institucional da clínica e do seu posicionamento.",
                "Institutional summary of the clinic and its positioning.",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()

        autocomplete_map = {
            "name": "section-clinic organization",
            "legal_name": "section-clinic organization",
            "nuit": "off",
            "city": "section-clinic address-level2",
            "province": "section-clinic address-level1",
            "country": "section-clinic country-name",
            "postal_code": "section-clinic postal-code",
            "address": "section-clinic street-address",
            "phone": "section-clinic tel",
            "email": "section-clinic email",
            "website": "section-clinic url",
            "description": "off",
        }
        for field_name, autocomplete_value in autocomplete_map.items():
            self.fields[field_name].widget.attrs["autocomplete"] = autocomplete_value
            self.fields[field_name].widget.attrs["data-lpignore"] = "true"
            self.fields[field_name].widget.attrs["data-1p-ignore"] = "true"

        for image_field in ("logo", "favicon"):
            self.fields[image_field].widget.attrs["accept"] = "image/*"

    def clean_nuit(self):
        nuit = re.sub(r"\D", "", (self.cleaned_data.get("nuit") or ""))
        if not nuit:
            return None
        if len(nuit) != 9:
            raise forms.ValidationError(
                tr(
                    "O NUIT deve ter 9 dígitos.",
                    "The tax ID must have 9 digits.",
                )
            )
        return nuit


class SystemPreferenceForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SystemPreference
        fields = ["default_language", "default_currency", "patient_code_prefix"]
        labels = {
            "default_language": tr("Idioma do sistema", "System language"),
            "default_currency": tr("Moeda base", "Base currency"),
            "patient_code_prefix": tr("Prefixo do ID do paciente", "Patient ID prefix"),
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
            "patient_code_prefix": tr(
                "Ex.: PCCP000. O código visível ficará no formato prefixo + ID, como PCCP0001.",
                "Example: PCCP000. The visible code will be prefix + ID, such as PCCP0001.",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        self.fields["patient_code_prefix"].widget.attrs["autocomplete"] = "off"

    def clean_patient_code_prefix(self):
        prefix = (self.cleaned_data.get("patient_code_prefix") or "").strip().upper()
        if not prefix:
            raise forms.ValidationError(
                tr(
                    "Defina um prefixo para o ID do paciente.",
                    "Define a patient ID prefix.",
                )
            )
        if not re.fullmatch(r"[A-Z0-9]+", prefix):
            raise forms.ValidationError(
                tr(
                    "Use apenas letras maiúsculas e números no prefixo.",
                    "Use only uppercase letters and numbers in the prefix.",
                )
            )
        return prefix


class MeasurementUnitForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = MeasurementUnit
        fields = ["code", "name", "abbreviation", "description", "sort_order", "is_active"]
        labels = {
            "code": tr("Código", "Code"),
            "name": tr("Unidade", "Unit"),
            "abbreviation": tr("Abreviatura", "Abbreviation"),
            "description": tr("Descrição", "Description"),
            "sort_order": tr("Ordem", "Sort order"),
            "is_active": tr("Activa", "Active"),
        }
        help_texts = {
            "code": tr(
                "Ex.: un, caixa, frasco, tubo. Este código é usado nos itens de inventário.",
                "Example: un, box, bottle, tube. This code is used by inventory items.",
            ),
            "name": tr(
                "Nome completo da unidade mostrado nos formulários.",
                "Full unit name shown in forms.",
            ),
            "abbreviation": tr(
                "Forma curta opcional, por exemplo cx, fr, amp.",
                "Optional short form, for example bx, bt, amp.",
            ),
            "description": tr(
                "Use para explicar onde e quando esta unidade costuma ser usada.",
                "Use this to explain where and when this unit is usually used.",
            ),
            "sort_order": tr(
                "Unidades com menor ordem aparecem primeiro nos selects.",
                "Units with a lower order appear first in selects.",
            ),
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        for field_name in ("code", "name", "abbreviation", "description"):
            self.fields[field_name].widget.attrs["autocomplete"] = "off"
            self.fields[field_name].widget.attrs["data-lpignore"] = "true"
            self.fields[field_name].widget.attrs["data-1p-ignore"] = "true"

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().lower()
        if not code:
            raise forms.ValidationError(
                tr(
                    "Defina um código para a unidade.",
                    "Define a code for the unit.",
                )
            )
        if not re.fullmatch(r"[a-z0-9_-]+", code):
            raise forms.ValidationError(
                tr(
                    "Use apenas letras minúsculas, números, hífen ou underscore no código.",
                    "Use only lowercase letters, numbers, hyphen, or underscore in the code.",
                )
            )
        return code


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
