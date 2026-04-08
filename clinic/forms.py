import re

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import Branch
from accounts.forms import StyledFormMixin
from accounts.i18n import translate_pair
from accounts.utils import visible_users_queryset

from .models import HorarioTrabalho, Paciente


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


class StaffUserChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        full_name = obj.get_full_name() or obj.username
        return f"{full_name} ({obj.username})"


WORK_SCHEDULE_WEEKDAY_OPTIONS = [
    (HorarioTrabalho.WeekdayChoices.MONDAY, tr("Segunda-feira", "Monday")),
    (HorarioTrabalho.WeekdayChoices.TUESDAY, tr("Terça-feira", "Tuesday")),
    (HorarioTrabalho.WeekdayChoices.WEDNESDAY, tr("Quarta-feira", "Wednesday")),
    (HorarioTrabalho.WeekdayChoices.THURSDAY, tr("Quinta-feira", "Thursday")),
    (HorarioTrabalho.WeekdayChoices.FRIDAY, tr("Sexta-feira", "Friday")),
    (HorarioTrabalho.WeekdayChoices.SATURDAY, tr("Sábado", "Saturday")),
    (HorarioTrabalho.WeekdayChoices.SUNDAY, tr("Domingo", "Sunday")),
]

WORK_SCHEDULE_WEEKDAY_OVERRIDES = [
    (weekday, slug, label_pt, label_en)
    for weekday, slug, label_pt, label_en in [
        (HorarioTrabalho.WeekdayChoices.MONDAY, "monday", "Segunda-feira", "Monday"),
        (HorarioTrabalho.WeekdayChoices.TUESDAY, "tuesday", "Terça-feira", "Tuesday"),
        (HorarioTrabalho.WeekdayChoices.WEDNESDAY, "wednesday", "Quarta-feira", "Wednesday"),
        (HorarioTrabalho.WeekdayChoices.THURSDAY, "thursday", "Quinta-feira", "Thursday"),
        (HorarioTrabalho.WeekdayChoices.FRIDAY, "friday", "Sexta-feira", "Friday"),
        (HorarioTrabalho.WeekdayChoices.SATURDAY, "saturday", "Sábado", "Saturday"),
        (HorarioTrabalho.WeekdayChoices.SUNDAY, "sunday", "Domingo", "Sunday"),
    ]
]


class WorkScheduleBatchCreateForm(StyledFormMixin, forms.Form):
    user = StaffUserChoiceField(
        queryset=User.objects.none(),
        label=tr("Profissional", "Professional"),
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        label=tr("Clínica / sucursal", "Clinic / branch"),
    )
    role = forms.ChoiceField(
        choices=HorarioTrabalho.RoleChoices.choices,
        label=tr("Função", "Role"),
    )
    shift_name = forms.CharField(
        required=False,
        max_length=120,
        label=tr("Nome do turno", "Shift name"),
        help_text=tr(
            "Opcional. Ex.: Manhã, Tarde, Urgência, Triagem.",
            "Optional. Example: Morning, Afternoon, Emergency, Triage.",
        ),
    )
    weekdays = forms.MultipleChoiceField(
        choices=[(str(value), label) for value, label in WORK_SCHEDULE_WEEKDAY_OPTIONS],
        widget=forms.CheckboxSelectMultiple,
        label=tr("Dias da semana", "Weekdays"),
        help_text=tr(
            "Marque um ou mais dias. Pode combinar dias úteis, alternados ou apenas um dia específico.",
            "Select one or more days. You can combine business days, alternating days, or only one specific day.",
        ),
    )
    start_time = forms.TimeField(
        label=tr("Início base", "Base start time"),
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    end_time = forms.TimeField(
        label=tr("Fim base", "Base end time"),
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    break_start = forms.TimeField(
        required=False,
        label=tr("Pausa base inicia", "Base break starts"),
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    break_end = forms.TimeField(
        required=False,
        label=tr("Pausa base termina", "Base break ends"),
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    customize_day_hours = forms.BooleanField(
        required=False,
        label=tr("Ajustar horas por dia", "Adjust hours by day"),
        help_text=tr(
            "Active para personalizar apenas os dias que precisarem de horas diferentes. Os restantes usam o horário base.",
            "Enable to customize only the days that need different hours. The remaining ones use the base schedule.",
        ),
    )
    valid_from = forms.DateField(
        label=tr("Válido desde", "Valid from"),
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,
    )
    valid_until = forms.DateField(
        required=False,
        label=tr("Válido até", "Valid until"),
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text=tr(
            "Opcional. Deixe vazio para manter o horário sem data final definida.",
            "Optional. Leave blank to keep the schedule open-ended.",
        ),
    )
    accepts_appointments = forms.BooleanField(
        required=False,
        label=tr("Aceita marcações", "Accepts appointments"),
        help_text=tr(
            "Liga este horário à agenda clínica. Hoje, a ocupação automática aparece quando o utilizador também está registado como médico.",
            "Links this schedule to the clinical calendar. Automatic occupancy is currently shown when the user is also registered as a doctor.",
        ),
    )
    notes = forms.CharField(
        required=False,
        label=tr("Observações internas", "Internal notes"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=tr(
            "Espaço para regras do turno, cobertura, salas ou observações da equipa.",
            "Space for shift rules, coverage, rooms, or team notes.",
        ),
    )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        kwargs.pop("instance", None)
        super().__init__(*args, **kwargs)

        for _, slug, label_pt, label_en in WORK_SCHEDULE_WEEKDAY_OVERRIDES:
            self.fields[f"{slug}_start_time"] = forms.TimeField(
                required=False,
                label=tr(f"{label_pt} começa", f"{label_en} starts"),
                widget=forms.TimeInput(attrs={"type": "time"}),
            )
            self.fields[f"{slug}_end_time"] = forms.TimeField(
                required=False,
                label=tr(f"{label_pt} termina", f"{label_en} ends"),
                widget=forms.TimeInput(attrs={"type": "time"}),
            )
            self.fields[f"{slug}_break_start"] = forms.TimeField(
                required=False,
                label=tr(f"Pausa {label_pt} inicia", f"{label_en} break starts"),
                widget=forms.TimeInput(attrs={"type": "time"}),
            )
            self.fields[f"{slug}_break_end"] = forms.TimeField(
                required=False,
                label=tr(f"Pausa {label_pt} termina", f"{label_en} break ends"),
                widget=forms.TimeInput(attrs={"type": "time"}),
            )

        self.apply_widget_classes()
        self.fields["user"].queryset = visible_users_queryset().order_by("first_name", "last_name", "username")
        self.fields["branch"].queryset = Branch.objects.order_by("name")

        current_branch = getattr(self.request, "clinic_current_branch", None) if self.request else None
        if current_branch and not self.initial.get("branch"):
            self.fields["branch"].initial = current_branch

        autocomplete_map = {
            "user": "off",
            "shift_name": "organization-title",
            "notes": "off",
        }
        for _, slug, _, _ in WORK_SCHEDULE_WEEKDAY_OVERRIDES:
            autocomplete_map[f"{slug}_start_time"] = "off"
            autocomplete_map[f"{slug}_end_time"] = "off"
            autocomplete_map[f"{slug}_break_start"] = "off"
            autocomplete_map[f"{slug}_break_end"] = "off"

        for field_name, autocomplete_value in autocomplete_map.items():
            if field_name not in self.fields:
                continue
            self.fields[field_name].widget.attrs["autocomplete"] = autocomplete_value
            self.fields[field_name].widget.attrs["data-lpignore"] = "true"
            self.fields[field_name].widget.attrs["data-1p-ignore"] = "true"

    def get_weekday_override_groups(self):
        groups = []
        for weekday, slug, label_pt, label_en in WORK_SCHEDULE_WEEKDAY_OVERRIDES:
            groups.append(
                {
                    "value": str(weekday),
                    "slug": slug,
                    "label": tr(label_pt, label_en),
                    "start_field": self[f"{slug}_start_time"],
                    "end_field": self[f"{slug}_end_time"],
                    "break_start_field": self[f"{slug}_break_start"],
                    "break_end_field": self[f"{slug}_break_end"],
                }
            )
        return groups

    def clean_shift_name(self):
        return HorarioTrabalho.normalize_text((self.cleaned_data.get("shift_name") or "").strip())

    def clean_notes(self):
        return HorarioTrabalho.normalize_text((self.cleaned_data.get("notes") or "").strip())

    def clean_weekdays(self):
        values = self.cleaned_data.get("weekdays") or []
        if not values:
            raise forms.ValidationError(tr("Seleccione pelo menos um dia da semana.", "Select at least one weekday."))
        return sorted({int(value) for value in values})

    def _validate_time_window(self, *, start_time, end_time, break_start, break_end, start_field, end_field, break_start_field, break_end_field):
        is_valid = True
        if end_time and start_time and end_time <= start_time:
            self.add_error(end_field, tr("A hora de fim deve ser posterior à hora de início.", "End time must be after start time."))
            is_valid = False

        break_pair = (break_start, break_end)
        if any(break_pair) and not all(break_pair):
            message = tr(
                "Preencha o início e o fim da pausa, ou deixe ambos vazios.",
                "Fill in both break start and break end, or leave both empty.",
            )
            self.add_error(break_start_field, message)
            self.add_error(break_end_field, message)
            return False

        if break_start and break_end:
            if break_end <= break_start:
                self.add_error(break_end_field, tr("O fim da pausa deve ser posterior ao início da pausa.", "Break end must be after break start."))
                is_valid = False
            if start_time and break_start <= start_time:
                self.add_error(break_start_field, tr("A pausa deve começar depois do início do turno.", "Break must start after the shift starts."))
                is_valid = False
            if end_time and break_end >= end_time:
                self.add_error(break_end_field, tr("A pausa deve terminar antes do fim do turno.", "Break must end before the shift ends."))
                is_valid = False
        return is_valid

    def clean(self):
        cleaned_data = super().clean()
        weekdays = cleaned_data.get("weekdays") or []
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        break_start = cleaned_data.get("break_start")
        break_end = cleaned_data.get("break_end")
        valid_from = cleaned_data.get("valid_from")
        valid_until = cleaned_data.get("valid_until")
        has_day_overrides = any(
            cleaned_data.get(f"{slug}_start_time")
            or cleaned_data.get(f"{slug}_end_time")
            or cleaned_data.get(f"{slug}_break_start")
            or cleaned_data.get(f"{slug}_break_end")
            for _, slug, _, _ in WORK_SCHEDULE_WEEKDAY_OVERRIDES
        )
        cleaned_data["customize_day_hours"] = cleaned_data.get("customize_day_hours") or has_day_overrides

        if valid_until and valid_from and valid_until < valid_from:
            self.add_error("valid_until", tr("A data final não pode ser anterior à data inicial.", "End date cannot be earlier than start date."))

        base_times_valid = self._validate_time_window(
            start_time=start_time,
            end_time=end_time,
            break_start=break_start,
            break_end=break_end,
            start_field="start_time",
            end_field="end_time",
            break_start_field="break_start",
            break_end_field="break_end",
        )

        if self.errors:
            return cleaned_data

        day_plans = []
        for weekday, slug, label_pt, label_en in WORK_SCHEDULE_WEEKDAY_OVERRIDES:
            if weekday not in weekdays:
                continue

            day_start = cleaned_data.get(f"{slug}_start_time") or start_time
            day_end = cleaned_data.get(f"{slug}_end_time") or end_time
            day_break_start = cleaned_data.get(f"{slug}_break_start")
            day_break_end = cleaned_data.get(f"{slug}_break_end")

            has_specific_break = bool(day_break_start or day_break_end)
            if not has_specific_break:
                day_break_start = break_start
                day_break_end = break_end

            specific_time_used = bool(
                cleaned_data.get(f"{slug}_start_time")
                or cleaned_data.get(f"{slug}_end_time")
                or has_specific_break
            )

            day_valid = self._validate_time_window(
                start_time=day_start,
                end_time=day_end,
                break_start=day_break_start,
                break_end=day_break_end,
                start_field=f"{slug}_start_time" if specific_time_used else "start_time",
                end_field=f"{slug}_end_time" if specific_time_used else "end_time",
                break_start_field=f"{slug}_break_start" if has_specific_break else "break_start",
                break_end_field=f"{slug}_break_end" if has_specific_break else "break_end",
            )
            if not day_valid or not base_times_valid:
                continue

            candidate = HorarioTrabalho(
                user=cleaned_data.get("user"),
                branch=cleaned_data.get("branch"),
                role=cleaned_data.get("role"),
                shift_name=cleaned_data.get("shift_name", ""),
                weekday=weekday,
                start_time=day_start,
                end_time=day_end,
                break_start=day_break_start,
                break_end=day_break_end,
                slot_minutes=30,
                valid_from=valid_from,
                valid_until=valid_until,
                accepts_appointments=cleaned_data.get("accepts_appointments", False),
                is_active=True,
                notes=cleaned_data.get("notes", ""),
            )
            try:
                candidate.full_clean()
            except ValidationError as exc:
                for field_name, messages in exc.message_dict.items():
                    target_field = field_name
                    if field_name in {"start_time", "end_time"} and specific_time_used:
                        target_field = f"{slug}_{field_name}"
                    elif field_name in {"break_start", "break_end"} and has_specific_break:
                        target_field = f"{slug}_{field_name}"
                    for message in messages:
                        self.add_error(target_field, f"{label_pt}: {message}")
                continue

            day_plans.append(
                {
                    "weekday": weekday,
                    "label": tr(label_pt, label_en),
                    "start_time": day_start,
                    "end_time": day_end,
                    "break_start": day_break_start,
                    "break_end": day_break_end,
                }
            )

        cleaned_data["day_plans"] = day_plans
        if weekdays and not day_plans and not self.errors:
            self.add_error("weekdays", tr("Não foi possível montar os blocos para os dias seleccionados.", "Could not build schedule blocks for the selected days."))
        return cleaned_data

    @transaction.atomic
    def save(self):
        created_schedules = []
        for plan in self.cleaned_data["day_plans"]:
            schedule = HorarioTrabalho.objects.create(
                user=self.cleaned_data["user"],
                branch=self.cleaned_data["branch"],
                role=self.cleaned_data["role"],
                shift_name=self.cleaned_data.get("shift_name", ""),
                weekday=plan["weekday"],
                start_time=plan["start_time"],
                end_time=plan["end_time"],
                break_start=plan["break_start"],
                break_end=plan["break_end"],
                slot_minutes=30,
                valid_from=self.cleaned_data["valid_from"],
                valid_until=self.cleaned_data.get("valid_until"),
                accepts_appointments=self.cleaned_data.get("accepts_appointments", False),
                is_active=True,
                notes=self.cleaned_data.get("notes", ""),
            )
            created_schedules.append(schedule)
        return created_schedules


class WorkScheduleForm(StyledFormMixin, forms.ModelForm):
    user = StaffUserChoiceField(
        queryset=User.objects.none(),
        label=tr("Profissional", "Professional"),
    )

    class Meta:
        model = HorarioTrabalho
        fields = [
            "user",
            "branch",
            "role",
            "shift_name",
            "weekday",
            "start_time",
            "end_time",
            "break_start",
            "break_end",
            "valid_from",
            "valid_until",
            "accepts_appointments",
            "notes",
        ]
        labels = {
            "branch": tr("Clínica / sucursal", "Clinic / branch"),
            "role": tr("Função", "Role"),
            "shift_name": tr("Nome do turno", "Shift name"),
            "weekday": tr("Dia da semana", "Weekday"),
            "start_time": tr("Início", "Start time"),
            "end_time": tr("Fim", "End time"),
            "break_start": tr("Pausa inicia", "Break starts"),
            "break_end": tr("Pausa termina", "Break ends"),
            "valid_from": tr("Válido desde", "Valid from"),
            "valid_until": tr("Válido até", "Valid until"),
            "accepts_appointments": tr("Aceita marcações", "Accepts appointments"),
            "notes": tr("Observações internas", "Internal notes"),
        }
        help_texts = {
            "branch": tr(
                "Associe o turno à sucursal onde este profissional vai operar.",
                "Link the shift to the branch where this professional will operate.",
            ),
            "role": tr(
                "Use a função operacional principal deste horário.",
                "Use the main operational role for this schedule.",
            ),
            "shift_name": tr(
                "Opcional. Ex.: Manhã, Tarde, Urgência, Triagem.",
                "Optional. Example: Morning, Afternoon, Emergency, Triage.",
            ),
            "valid_from": tr(
                "Data em que este padrão semanal começa a valer.",
                "Date when this weekly pattern starts applying.",
            ),
            "valid_until": tr(
                "Opcional. Deixe vazio para manter o horário sem data final definida.",
                "Optional. Leave blank to keep the schedule open-ended.",
            ),
            "accepts_appointments": tr(
                "Liga este horário à agenda clínica. Hoje, a ocupação automática aparece quando o utilizador também está registado como médico.",
                "Links this schedule to the clinical calendar. Automatic occupancy is currently shown when the user is also registered as a doctor.",
            ),
            "notes": tr(
                "Espaço para regras do turno, cobertura, salas ou observações da equipa.",
                "Space for shift rules, coverage, rooms, or team notes.",
            ),
        }
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "break_start": forms.TimeInput(attrs={"type": "time"}),
            "break_end": forms.TimeInput(attrs={"type": "time"}),
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_until": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        self.fields["user"].queryset = visible_users_queryset().order_by("first_name", "last_name", "username")
        self.fields["branch"].queryset = Branch.objects.order_by("name")
        self.fields["valid_until"].required = False
        self.fields["shift_name"].required = False
        self.fields["break_start"].required = False
        self.fields["break_end"].required = False
        self.fields["notes"].required = False

        current_branch = getattr(self.request, "clinic_current_branch", None) if self.request else None
        if current_branch and not self.instance.pk and not self.initial.get("branch"):
            self.fields["branch"].initial = current_branch

        autocomplete_map = {
            "user": "off",
            "shift_name": "organization-title",
            "notes": "off",
        }

        for field_name, autocomplete_value in autocomplete_map.items():
            if field_name not in self.fields:
                continue
            self.fields[field_name].widget.attrs["autocomplete"] = autocomplete_value
            self.fields[field_name].widget.attrs["data-lpignore"] = "true"
            self.fields[field_name].widget.attrs["data-1p-ignore"] = "true"

        self.initial["shift_name"] = HorarioTrabalho.normalize_text(self.initial.get("shift_name") or self.instance.shift_name)
        self.initial["notes"] = HorarioTrabalho.normalize_text(self.initial.get("notes") or self.instance.notes)

    def clean_shift_name(self):
        return HorarioTrabalho.normalize_text((self.cleaned_data.get("shift_name") or "").strip())

    def clean_notes(self):
        return HorarioTrabalho.normalize_text((self.cleaned_data.get("notes") or "").strip())
