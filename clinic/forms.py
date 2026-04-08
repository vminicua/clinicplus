import re

from django import forms
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from accounts.models import Branch
from accounts.forms import StyledFormMixin
from accounts.i18n import translate_pair

from .models import Paciente


User = get_user_model()
tr = translate_pair


class PatientForm(StyledFormMixin, forms.ModelForm):
    first_name = forms.CharField(label=tr("Nome", "First name"), max_length=150)
    last_name = forms.CharField(label=tr("Apelido", "Last name"), max_length=150, required=False)
    email = forms.EmailField(label="Email", required=False)

    class Meta:
        model = Paciente
        fields = [
            "branch",
            "cpf",
            "date_of_birth",
            "gender",
            "phone",
            "address",
            "city",
            "country",
            "state",
            "zip_code",
            "emergency_contact",
            "emergency_phone",
            "allergies",
            "medical_history",
        ]
        labels = {
            "branch": tr("Clínica", "Clinic"),
            "cpf": tr("BI / Passaporte", "ID / Passport"),
            "date_of_birth": tr("Data de nascimento", "Date of birth"),
            "gender": tr("Género", "Gender"),
            "phone": tr("Telefone", "Phone"),
            "address": tr("Endereço", "Address"),
            "city": tr("Cidade", "City"),
            "country": tr("País", "Country"),
            "state": tr("Província", "Province"),
            "zip_code": tr("Código postal", "Postal code"),
            "emergency_contact": tr("Contacto de emergência", "Emergency contact"),
            "emergency_phone": tr("Telefone de emergência", "Emergency phone"),
            "allergies": tr("Alergias", "Allergies"),
            "medical_history": tr("Histórico clínico base", "Baseline medical history"),
        }
        help_texts = {
            "branch": tr(
                "Opcional. Associe o paciente a uma das suas sucursais clínicas.",
                "Optional. Link the patient to one of your clinic branches.",
            ),
            "cpf": tr(
                "Use o número de BI ou Passaporte. Guardamos apenas letras e números.",
                "Use the ID or passport number. We keep only letters and numbers.",
            ),
            "date_of_birth": tr(
                "Use a data real do paciente para calcular a idade automaticamente.",
                "Use the patient's real birth date to calculate age automatically.",
            ),
            "phone": tr("Contacto principal do paciente.", "Primary patient contact number."),
            "address": tr("Morada actual ou referência de localização.", "Current address or location reference."),
            "country": tr(
                "País de residência ou nacionalidade declarada. O padrão inicial é Moçambique.",
                "Country of residence or declared nationality. Mozambique is the default.",
            ),
            "state": tr("Província de residência do paciente.", "Patient's province of residence."),
            "zip_code": tr(
                "Opcional. Útil quando o paciente tem código postal aplicável.",
                "Optional. Useful when the patient has an applicable postal code.",
            ),
            "emergency_contact": tr(
                "Pessoa a contactar em caso de urgência.",
                "Person to contact in case of emergency.",
            ),
            "emergency_phone": tr(
                "Opcional. Número directo do contacto de emergência.",
                "Optional. Direct number for the emergency contact.",
            ),
            "allergies": tr(
                "Liste alergias conhecidas, uma por linha ou separadas por vírgula.",
                "List known allergies, one per line or separated by commas.",
            ),
            "medical_history": tr(
                "Resumo inicial de doenças prévias, medicação contínua e observações clínicas.",
                "Initial summary of previous conditions, ongoing medication, and clinical notes.",
            ),
        }
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "allergies": forms.Textarea(attrs={"rows": 4}),
            "medical_history": forms.Textarea(attrs={"rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        self.fields["branch"].required = False
        self.fields["branch"].queryset = Branch.objects.order_by("name")
        self.fields["zip_code"].required = False
        self.fields["emergency_phone"].required = False

        if self.instance and self.instance.pk:
            self.fields["first_name"].initial = self.instance.user.first_name
            self.fields["last_name"].initial = self.instance.user.last_name
            self.fields["email"].initial = self.instance.user.email
        elif self.request is not None and getattr(self.request, "clinic_current_branch", None):
            self.fields["branch"].initial = self.request.clinic_current_branch

        autocomplete_map = {
            "first_name": "given-name",
            "last_name": "family-name",
            "email": "email",
            "cpf": "off",
            "date_of_birth": "bday",
            "phone": "tel",
            "address": "street-address",
            "city": "address-level2",
            "country": "country-name",
            "state": "address-level1",
            "zip_code": "postal-code",
            "emergency_contact": "name",
            "emergency_phone": "tel-national",
            "allergies": "off",
            "medical_history": "off",
        }

        for field_name, autocomplete_value in autocomplete_map.items():
            if field_name not in self.fields:
                continue
            self.fields[field_name].widget.attrs["autocomplete"] = autocomplete_value
            self.fields[field_name].widget.attrs["data-lpignore"] = "true"
            self.fields[field_name].widget.attrs["data-1p-ignore"] = "true"

    def clean_cpf(self):
        document = re.sub(r"[^0-9A-Za-z]", "", (self.cleaned_data.get("cpf") or "")).upper()
        if len(document) < 5:
            raise forms.ValidationError(
                tr(
                    "Informe um documento com pelo menos 5 caracteres.",
                    "Provide a document number with at least 5 characters.",
                )
            )
        if len(document) > 14:
            raise forms.ValidationError(
                tr(
                    "O documento não pode ter mais de 14 caracteres.",
                    "The document number cannot be longer than 14 characters.",
                )
            )
        queryset = Paciente.objects.exclude(pk=self.instance.pk) if self.instance and self.instance.pk else Paciente.objects.all()
        if queryset.filter(cpf=document).exists():
            raise forms.ValidationError(
                tr(
                    "Já existe um paciente registado com este documento.",
                    "A patient with this document number already exists.",
                )
            )
        return document

    def clean_date_of_birth(self):
        date_of_birth = self.cleaned_data["date_of_birth"]
        if date_of_birth > timezone.localdate():
            raise forms.ValidationError(
                tr(
                    "A data de nascimento não pode estar no futuro.",
                    "Date of birth cannot be in the future.",
                )
            )
        return date_of_birth

    def _generate_unique_username(self, document: str) -> str:
        base = f"pac_{document.lower()}"
        base = re.sub(r"[^a-z0-9_]", "", base) or "paciente"
        base = base[:150]
        queryset = User.objects.all()
        if self.instance and self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.user_id)

        candidate = base
        counter = 1
        while queryset.filter(username=candidate).exists():
            suffix = f"_{counter}"
            candidate = f"{base[:150 - len(suffix)]}{suffix}"
            counter += 1
        return candidate

    @transaction.atomic
    def save(self, commit=True):
        patient = super().save(commit=False)

        if self.instance and self.instance.pk:
            user = self.instance.user
        else:
            user = User()
            user.username = self._generate_unique_username(self.cleaned_data["cpf"])
            user.set_unusable_password()

        user.first_name = self.cleaned_data["first_name"].strip()
        user.last_name = self.cleaned_data["last_name"].strip()
        user.email = self.cleaned_data.get("email", "").strip()
        user.is_staff = False
        user.is_superuser = False
        user.is_active = patient.is_active

        if commit:
            user.save()
            patient.user = user
            patient.hospital = None
            patient.save()
            self.save_m2m()

        return patient
