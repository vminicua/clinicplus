import re
from collections import defaultdict
from datetime import datetime, timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import Branch, MeasurementUnit
from accounts.forms import StyledFormMixin
from accounts.i18n import translate_pair
from accounts.ui import available_branches_for_user
from accounts.utils import visible_users_queryset

from .models import (
    Agendamento,
    Armazem,
    Consulta,
    Consumivel,
    Departamento,
    Especialidade,
    EstoqueConsumivel,
    EstoqueMedicamento,
    HorarioTrabalho,
    Hospital,
    Medicamento,
    Medico,
    MovimentoInventario,
    Paciente,
)


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


class PatientChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        document = obj.cpf or tr("Sem documento", "No document")
        return f"{obj.full_name} · {document}"


class DoctorChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        full_name = obj.get_full_name() or obj.username
        return full_name


class DepartmentChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        unit_name = obj.unit_name or tr("Sem unidade", "No unit")
        return f"{obj.name} · {unit_name}"


class WarehouseChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.display_name} · {obj.branch.name}"


class MedicationChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        dosage = f" · {obj.dosagem}" if obj.dosagem else ""
        return f"{obj.display_name}{dosage}"


class ConsumableChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        unit = f" · {obj.unidade_medida}" if obj.unidade_medida else ""
        return f"{obj.display_name}{unit}"


def appointment_doctor_user_queryset():
    doctor_group = Group.objects.filter(name="Médico").first() or Group.objects.filter(pk=4).first()
    queryset = visible_users_queryset().filter(is_active=True)
    filters = Q(medico__isnull=False)
    if doctor_group is not None:
        filters |= Q(groups=doctor_group)
    filters |= Q(
        horarios_trabalho__role=HorarioTrabalho.RoleChoices.MEDICO,
        horarios_trabalho__is_active=True,
    )
    return queryset.filter(filters).distinct().order_by("first_name", "last_name", "username")


def appointment_branch_queryset(request=None):
    queryset = Branch.objects.filter(is_active=True).order_by("name")
    if request is None or not getattr(request, "user", None) or not request.user.is_authenticated:
        return queryset

    scoped_queryset = available_branches_for_user(request.user)
    return scoped_queryset if scoped_queryset.exists() else queryset


def measurement_unit_queryset(*codes):
    requested_codes = [str(code).strip().lower() for code in codes if code]
    queryset = MeasurementUnit.objects.filter(is_active=True)
    if requested_codes:
        queryset = MeasurementUnit.objects.filter(Q(is_active=True) | Q(code__in=requested_codes))
    return queryset.order_by("sort_order", "name", "code")


def measurement_unit_choices(*codes):
    choices = [(unit.code, unit.select_label) for unit in measurement_unit_queryset(*codes)]
    seen_codes = {code for code, _label in choices}
    for code in codes:
        normalized = str(code).strip().lower()
        if normalized and normalized not in seen_codes:
            choices.append((normalized, normalized))
            seen_codes.add(normalized)
    if choices:
        return choices
    return [("un", tr("un · Unidade", "un · Unit"))]


def generate_doctor_crm(user):
    base_value = f"AUTOCRM{user.pk}"
    candidate = base_value
    counter = 1
    while Medico.objects.filter(crm=candidate).exclude(user=user).exists():
        candidate = f"{base_value}_{counter}"
        counter += 1
    return candidate


def get_doctor_profile(user):
    return Medico.objects.filter(user=user).select_related("hospital").first()


def resolve_legacy_hospital(branch=None, patient=None, doctor_user=None):
    if doctor_user is not None:
        current_medico = get_doctor_profile(doctor_user)
        if current_medico and current_medico.hospital_id:
            return current_medico.hospital

    candidate_branches = []
    if branch is not None:
        candidate_branches.append(branch)
    if patient is not None and getattr(patient, "branch_id", None):
        candidate_branches.append(patient.branch)

    for candidate_branch in candidate_branches:
        if getattr(candidate_branch, "clinic_id", None):
            matched_hospital = Hospital.objects.filter(name__iexact=candidate_branch.clinic.name).first()
            if matched_hospital:
                return matched_hospital
        matched_hospital = Hospital.objects.filter(name__iexact=candidate_branch.name).first()
        if matched_hospital:
            return matched_hospital

    if patient is not None and getattr(patient, "hospital_id", None):
        return patient.hospital

    if Hospital.objects.count() == 1:
        return Hospital.objects.order_by("id").first()

    return None


def appointment_time_matches_schedule(schedule, appointment_time):
    if appointment_time < schedule.start_time or appointment_time >= schedule.end_time:
        return False

    if schedule.break_start and schedule.break_end and schedule.break_start <= appointment_time < schedule.break_end:
        return False

    if not schedule.slot_minutes:
        return True

    base_dt = datetime.combine(datetime.today(), schedule.start_time)
    appointment_dt = datetime.combine(datetime.today(), appointment_time)
    end_dt = datetime.combine(datetime.today(), schedule.end_time)
    delta_minutes = int((appointment_dt - base_dt).total_seconds() // 60)

    if delta_minutes < 0 or delta_minutes % schedule.slot_minutes != 0:
        return False

    return appointment_dt + timedelta(minutes=schedule.slot_minutes) <= end_dt


def appointment_schedule_queryset(user, branch):
    return (
        HorarioTrabalho.objects.filter(
            user=user,
            branch=branch,
            is_active=True,
            accepts_appointments=True,
        )
        .select_related("branch")
        .order_by("weekday", "start_time")
    )


def iter_schedule_slot_times(schedule):
    slot_minutes = schedule.slot_minutes or 30
    if slot_minutes <= 0:
        slot_minutes = 30

    current_dt = datetime.combine(datetime.today(), schedule.start_time)
    end_dt = datetime.combine(datetime.today(), schedule.end_time)
    slot_delta = timedelta(minutes=slot_minutes)

    while current_dt + slot_delta <= end_dt:
        slot_time = current_dt.time()
        if appointment_time_matches_schedule(schedule, slot_time):
            yield slot_time
        current_dt += slot_delta


def find_next_available_appointment_slot(
    user,
    branch,
    *,
    reference_date=None,
    reference_time=None,
    exclude_appointment=None,
    search_days=120,
):
    schedules = list(appointment_schedule_queryset(user, branch))
    if not schedules:
        return None

    today = timezone.localdate()
    start_date = max(reference_date or today, today)
    current_local_time = timezone.localtime().replace(second=0, microsecond=0).time()
    occupied_until = start_date + timedelta(days=search_days)

    occupied_queryset = Agendamento.objects.filter(
        medico__user=user,
        data__range=(start_date, occupied_until),
    )
    if exclude_appointment and getattr(exclude_appointment, "pk", None):
        occupied_queryset = occupied_queryset.exclude(pk=exclude_appointment.pk)

    occupied_slots = defaultdict(set)
    for booked_date, booked_time in occupied_queryset.values_list("data", "hora"):
        occupied_slots[booked_date].add(booked_time)

    for offset in range(search_days + 1):
        target_date = start_date + timedelta(days=offset)
        day_schedules = [
            schedule
            for schedule in schedules
            if schedule.applies_to_date(target_date)
        ]
        if not day_schedules:
            continue

        minimum_time = None
        if target_date == start_date:
            time_candidates = []
            if reference_time is not None:
                time_candidates.append(reference_time)
            if target_date == today:
                time_candidates.append(current_local_time)
            if time_candidates:
                minimum_time = max(time_candidates)

        for schedule in sorted(day_schedules, key=lambda item: (item.start_time, item.end_time)):
            for slot_time in iter_schedule_slot_times(schedule):
                if minimum_time is not None and slot_time <= minimum_time:
                    continue
                if slot_time in occupied_slots[target_date]:
                    continue
                return {
                    "date": target_date,
                    "time": slot_time,
                    "schedule": schedule,
                }

    return None


def next_availability_message(slot):
    if not slot:
        return ""
    return tr(
        " Próxima disponibilidade: %(date)s às %(time)s.",
        " Next availability: %(date)s at %(time)s.",
    ) % {
        "date": slot["date"].strftime("%d/%m/%Y"),
        "time": slot["time"].strftime("%H:%M"),
    }


def has_valid_appointment_schedule(user, branch, appointment_date, appointment_time):
    schedules = [
        schedule
        for schedule in appointment_schedule_queryset(user, branch)
        if schedule.applies_to_date(appointment_date)
    ]
    if not schedules:
        return False
    return any(appointment_time_matches_schedule(schedule, appointment_time) for schedule in schedules)


def ensure_doctor_profile(user, branch=None, patient=None):
    doctor_profile = Medico.objects.filter(user=user).select_related("hospital").first()
    if doctor_profile:
        if doctor_profile.hospital_id is None:
            legacy_hospital = resolve_legacy_hospital(branch=branch, patient=patient, doctor_user=user)
            if legacy_hospital is not None:
                doctor_profile.hospital = legacy_hospital
                doctor_profile.save(update_fields=["hospital"])
        return doctor_profile

    return Medico.objects.create(
        user=user,
        hospital=resolve_legacy_hospital(branch=branch, patient=patient, doctor_user=user),
        especialidade=None,
        crm=generate_doctor_crm(user),
        phone="",
        bio="",
    )


class AppointmentForm(StyledFormMixin, forms.ModelForm):
    paciente = PatientChoiceField(
        queryset=Paciente.objects.none(),
        label=tr("Paciente", "Patient"),
    )
    doctor_user = DoctorChoiceField(
        queryset=User.objects.none(),
        label=tr("Profissional clínico", "Clinical professional"),
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        label=tr("Sucursal da consulta", "Consultation branch"),
    )

    class Meta:
        model = Agendamento
        fields = [
            "paciente",
            "doctor_user",
            "branch",
            "data",
            "hora",
            "motivo",
            "status",
            "notas",
        ]
        labels = {
            "data": tr("Data", "Date"),
            "hora": tr("Hora", "Time"),
            "motivo": tr("Motivo da marcação", "Booking reason"),
            "status": tr("Estado", "Status"),
            "notas": tr("Notas internas", "Internal notes"),
        }
        help_texts = {
            "data": tr(
                "Data pretendida para a marcação.",
                "Requested booking date.",
            ),
            "branch": tr(
                "Sucursal onde esta consulta será atendida.",
                "Branch where this consultation will take place.",
            ),
            "hora": tr(
                "Hora exacta reservada para este paciente.",
                "Exact time reserved for this patient.",
            ),
            "motivo": tr(
                "Resumo curto do motivo da consulta ou procedimento.",
                "Short summary of the reason for the visit or procedure.",
            ),
            "status": tr(
                "Estado operacional actual da marcação.",
                "Current operational status of the booking.",
            ),
            "notas": tr(
                "Observações internas para recepção, enfermagem ou clínico.",
                "Internal notes for reception, nursing, or clinician.",
            ),
        }
        widgets = {
            "data": forms.DateInput(attrs={"type": "date"}),
            "hora": forms.TimeInput(attrs={"type": "time"}),
            "motivo": forms.Textarea(attrs={"rows": 3}),
            "notas": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        self.fields["paciente"].queryset = (
            Paciente.objects.select_related("user", "branch")
            .filter(is_active=True)
            .order_by("user__first_name", "user__last_name", "cpf")
        )
        self.fields["doctor_user"].queryset = appointment_doctor_user_queryset()
        self.fields["branch"].queryset = appointment_branch_queryset(self.request)
        self.fields["doctor_user"].help_text = tr(
            "Utilizadores do perfil Médico com agenda activa ou horário clínico configurado.",
            "Users in the Doctor role with an active agenda or configured clinical schedule.",
        )
        self.fields["notas"].required = False
        self.fields["status"].initial = self.initial.get("status") or Agendamento.STATUS_CHOICES[0][0]

        if not self.instance.pk and not self.initial.get("branch"):
            current_branch = getattr(self.request, "clinic_current_branch", None) if self.request else None
            if current_branch and self.fields["branch"].queryset.filter(pk=current_branch.pk).exists():
                self.fields["branch"].initial = current_branch

        if self.instance.pk and self.instance.branch_id:
            self.fields["branch"].initial = self.instance.branch

        for field_name in ("paciente", "doctor_user", "branch", "motivo", "notas"):
            self.fields[field_name].widget.attrs["autocomplete"] = "off"
            self.fields[field_name].widget.attrs["data-lpignore"] = "true"
            self.fields[field_name].widget.attrs["data-1p-ignore"] = "true"

    def clean(self):
        cleaned_data = super().clean()
        doctor_user = cleaned_data.get("doctor_user")
        branch = cleaned_data.get("branch")
        appointment_date = cleaned_data.get("data")
        appointment_time = cleaned_data.get("hora")
        patient = cleaned_data.get("paciente")

        if doctor_user and branch and not appointment_schedule_queryset(doctor_user, branch).exists():
            self.add_error(
                "branch",
                tr(
                    "Este profissional não tem agenda activa para marcações nesta sucursal.",
                    "This professional does not have an active appointment schedule in this branch.",
                ),
            )

        if doctor_user and branch and appointment_date and appointment_time:
            suggested_slot = find_next_available_appointment_slot(
                doctor_user,
                branch,
                reference_date=appointment_date,
                reference_time=appointment_time,
                exclude_appointment=self.instance,
            )
            if not has_valid_appointment_schedule(doctor_user, branch, appointment_date, appointment_time):
                self.add_error(
                    "hora",
                    tr(
                        "A hora escolhida está fora dos blocos activos deste profissional nesta sucursal.",
                        "The selected time is outside this professional's active schedule blocks in this branch.",
                    )
                    + next_availability_message(suggested_slot),
                )

            doctor = get_doctor_profile(doctor_user)
            if doctor is not None:
                cleaned_data["resolved_doctor_profile"] = doctor
                duplicate_queryset = Agendamento.objects.filter(
                    medico=doctor,
                    data=appointment_date,
                    hora=appointment_time,
                )
                if self.instance and self.instance.pk:
                    duplicate_queryset = duplicate_queryset.exclude(pk=self.instance.pk)
                if duplicate_queryset.exists():
                    self.add_error(
                        "hora",
                        tr(
                            "Já existe uma marcação para este profissional nesta data e hora.",
                            "There is already a booking for this professional at this date and time.",
                        )
                        + next_availability_message(suggested_slot),
                    )

        return cleaned_data

    def save(self, commit=True):
        appointment = super().save(commit=False)
        doctor = self.cleaned_data.get("resolved_doctor_profile") or ensure_doctor_profile(
            self.cleaned_data["doctor_user"],
            branch=self.cleaned_data.get("branch"),
            patient=self.cleaned_data.get("paciente"),
        )
        appointment.medico = doctor
        appointment.branch = self.cleaned_data.get("branch")
        appointment.hospital = resolve_legacy_hospital(
            branch=appointment.branch,
            patient=self.cleaned_data.get("paciente"),
            doctor_user=self.cleaned_data["doctor_user"],
        )

        if commit:
            appointment.save()
            self.save_m2m()

        return appointment


class ConsultationForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Consulta
        fields = [
            "diagnostico",
            "prescricao",
            "notas_medico",
        ]
        labels = {
            "diagnostico": tr("Diagnóstico", "Diagnosis"),
            "prescricao": tr("Prescrição", "Prescription"),
            "notas_medico": tr("Notas clínicas", "Clinical notes"),
        }
        help_texts = {
            "diagnostico": tr(
                "Registe a avaliação clínica principal desta consulta.",
                "Record the main clinical assessment for this consultation.",
            ),
            "prescricao": tr(
                "Indique medicação, exames, orientações ou plano terapêutico.",
                "Document medication, exams, guidance, or therapeutic plan.",
            ),
            "notas_medico": tr(
                "Campo opcional para observações adicionais do profissional.",
                "Optional field for additional clinician notes.",
            ),
        }
        widgets = {
            "diagnostico": forms.Textarea(attrs={"rows": 4}),
            "prescricao": forms.Textarea(attrs={"rows": 4}),
            "notas_medico": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()


class SpecialtyForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Especialidade
        fields = ["name", "description", "icon"]
        labels = {
            "name": tr("Especialidade", "Specialty"),
            "description": tr("Descrição", "Description"),
            "icon": tr("Ícone", "Icon"),
        }
        help_texts = {
            "name": tr(
                "Use a designação clínica que aparecerá no perfil do médico, por exemplo Ginecologista.",
                "Use the clinical designation that will appear in the doctor's profile, for example Gynecologist.",
            ),
            "description": tr(
                "Explique rapidamente o âmbito clínico desta especialidade.",
                "Quickly explain the clinical scope of this specialty.",
            ),
            "icon": tr(
                "Opcional. Nome do ícone usado em interfaces e dashboards.",
                "Optional. Icon name used in interfaces and dashboards.",
            ),
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()


class DepartmentForm(StyledFormMixin, forms.ModelForm):
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        label=tr("Sucursal", "Branch"),
    )
    responsavel_user = DoctorChoiceField(
        queryset=User.objects.none(),
        required=False,
        label=tr("Responsável clínico", "Clinical lead"),
    )

    class Meta:
        model = Departamento
        fields = ["name", "branch", "responsavel_user", "descricao"]
        labels = {
            "name": tr("Departamento", "Department"),
            "descricao": tr("Descrição", "Description"),
        }
        help_texts = {
            "name": tr(
                "Use o nome do serviço ou área clínica, por exemplo Ginecologia.",
                "Use the service or clinical area name, for example Gynecology.",
            ),
            "branch": tr(
                "Sucursal onde este departamento opera.",
                "Branch where this department operates.",
            ),
            "responsavel_user": tr(
                "Opcional. Médico responsável por este serviço.",
                "Optional. Doctor responsible for this service.",
            ),
            "descricao": tr(
                "Resumo do âmbito operacional e clínico do departamento.",
                "Summary of the operational and clinical scope of the department.",
            ),
        }
        widgets = {
            "descricao": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        self.fields["branch"].queryset = appointment_branch_queryset(self.request)
        self.fields["responsavel_user"].queryset = appointment_doctor_user_queryset()

        if self.instance.pk and self.instance.branch_id:
            self.fields["branch"].initial = self.instance.branch
        elif not self.initial.get("branch"):
            current_branch = getattr(self.request, "clinic_current_branch", None) if self.request else None
            if current_branch and self.fields["branch"].queryset.filter(pk=current_branch.pk).exists():
                self.fields["branch"].initial = current_branch

        if self.instance.pk and self.instance.responsavel_id:
            self.fields["responsavel_user"].initial = self.instance.responsavel.user

    def clean(self):
        cleaned_data = super().clean()
        branch = cleaned_data.get("branch")
        responsavel_user = cleaned_data.get("responsavel_user")

        if responsavel_user and branch:
            has_clinical_schedule = appointment_schedule_queryset(responsavel_user, branch).exists()
            if not has_clinical_schedule:
                self.add_error(
                    "responsavel_user",
                    tr(
                        "Seleccione um médico com agenda activa nesta sucursal para assumir este departamento.",
                        "Select a doctor with an active agenda in this branch to lead this department.",
                    ),
                )

        return cleaned_data

    def save(self, commit=True):
        department = super().save(commit=False)
        branch = self.cleaned_data.get("branch")
        responsavel_user = self.cleaned_data.get("responsavel_user")
        department.branch = branch
        department.hospital = resolve_legacy_hospital(branch=branch, doctor_user=responsavel_user)
        department.responsavel = (
            ensure_doctor_profile(responsavel_user, branch=branch)
            if responsavel_user is not None
            else None
        )

        if commit:
            department.save()
            self.save_m2m()

        return department


class MedicationForm(StyledFormMixin, forms.ModelForm):
    unidade_medida = forms.ChoiceField(
        label=tr("Unidade de medida", "Unit of measure"),
        choices=(),
    )

    class Meta:
        model = Medicamento
        fields = ["name", "sku", "principio_ativo", "dosagem", "unidade_medida", "preco", "descricao", "is_active"]
        labels = {
            "name": tr("Medicamento", "Medication"),
            "sku": tr("SKU / Código", "SKU / Code"),
            "principio_ativo": tr("Princípio activo", "Active ingredient"),
            "dosagem": tr("Dosagem", "Dosage"),
            "unidade_medida": tr("Unidade de medida", "Unit of measure"),
            "preco": tr("Preço", "Price"),
            "descricao": tr("Descrição", "Description"),
            "is_active": tr("Activo", "Active"),
        }
        help_texts = {
            "name": tr(
                "Nome comercial ou interno do medicamento.",
                "Commercial or internal medication name.",
            ),
            "sku": tr(
                "Opcional. Código interno usado pelo inventário e compras.",
                "Optional. Internal code used by inventory and purchasing.",
            ),
            "principio_ativo": tr(
                "Substância principal usada para catalogar e pesquisar o medicamento.",
                "Main substance used to catalog and search the medication.",
            ),
            "dosagem": tr(
                "Ex.: 500 mg, 10 ml, 1 g/5 ml.",
                "Example: 500 mg, 10 ml, 1 g/5 ml.",
            ),
            "unidade_medida": tr(
                "Escolha uma unidade registada em Preferências > Unidades.",
                "Choose a unit registered under Preferences > Units.",
            ),
            "preco": tr(
                "Preço unitário de referência do catálogo.",
                "Reference unit price.",
            ),
            "descricao": tr(
                "Observações adicionais para uso clínico, apresentação ou compras.",
                "Additional notes for clinical use, presentation, or stock.",
            ),
        }
        widgets = {
            "descricao": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        current_unit = self.instance.unidade_medida if self.instance and self.instance.pk else None
        bound_unit = self.data.get("unidade_medida") if self.is_bound else None
        self.fields["unidade_medida"].choices = measurement_unit_choices(current_unit, bound_unit)
        if not self.is_bound and not current_unit:
            self.fields["unidade_medida"].initial = self.fields["unidade_medida"].choices[0][0]
        for field_name in ("name", "sku", "principio_ativo", "dosagem", "unidade_medida", "descricao"):
            self.fields[field_name].widget.attrs["autocomplete"] = "off"
            self.fields[field_name].widget.attrs["data-lpignore"] = "true"
            self.fields[field_name].widget.attrs["data-1p-ignore"] = "true"

    def clean_unidade_medida(self):
        return (self.cleaned_data.get("unidade_medida") or "").strip().lower()


class ConsumableForm(StyledFormMixin, forms.ModelForm):
    unidade_medida = forms.ChoiceField(
        label=tr("Unidade de medida", "Unit of measure"),
        choices=(),
    )

    class Meta:
        model = Consumivel
        fields = ["name", "sku", "unidade_medida", "preco_referencia", "descricao", "is_active"]
        labels = {
            "name": tr("Consumível", "Consumable"),
            "sku": tr("SKU / Código", "SKU / Code"),
            "unidade_medida": tr("Unidade de medida", "Unit of measure"),
            "preco_referencia": tr("Preço de referência", "Reference price"),
            "descricao": tr("Descrição", "Description"),
            "is_active": tr("Activo", "Active"),
        }
        help_texts = {
            "name": tr(
                "Nome do consumível usado em clínica, enfermagem ou laboratório.",
                "Consumable name used in clinic, nursing, or laboratory.",
            ),
            "sku": tr(
                "Opcional. Código interno de inventário ou compras.",
                "Optional. Internal inventory or purchasing code.",
            ),
            "unidade_medida": tr(
                "Escolha uma unidade registada em Preferências > Unidades.",
                "Choose a unit registered under Preferences > Units.",
            ),
            "preco_referencia": tr(
                "Preço unitário de referência para reposição.",
                "Reference unit price for replenishment.",
            ),
            "descricao": tr(
                "Observações adicionais sobre o uso ou apresentação do consumível.",
                "Additional notes about the use or presentation of the consumable.",
            ),
        }
        widgets = {
            "descricao": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        current_unit = self.instance.unidade_medida if self.instance and self.instance.pk else None
        bound_unit = self.data.get("unidade_medida") if self.is_bound else None
        self.fields["unidade_medida"].choices = measurement_unit_choices(current_unit, bound_unit)
        if not self.is_bound and not current_unit:
            self.fields["unidade_medida"].initial = self.fields["unidade_medida"].choices[0][0]
        for field_name in ("name", "sku", "unidade_medida", "descricao"):
            self.fields[field_name].widget.attrs["autocomplete"] = "off"
            self.fields[field_name].widget.attrs["data-lpignore"] = "true"
            self.fields[field_name].widget.attrs["data-1p-ignore"] = "true"

    def clean_unidade_medida(self):
        return (self.cleaned_data.get("unidade_medida") or "").strip().lower()


class WarehouseForm(StyledFormMixin, forms.ModelForm):
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        label=tr("Sucursal", "Branch"),
    )

    class Meta:
        model = Armazem
        fields = ["branch", "name", "code", "location", "manager_name", "manager_phone", "description", "is_active"]
        labels = {
            "name": tr("Armazém", "Warehouse"),
            "code": tr("Código", "Code"),
            "location": tr("Localização", "Location"),
            "manager_name": tr("Responsável", "Manager"),
            "manager_phone": tr("Telefone do responsável", "Manager phone"),
            "description": tr("Descrição", "Description"),
            "is_active": tr("Activo", "Active"),
        }
        help_texts = {
            "branch": tr("Sucursal onde este armazém opera.", "Branch where this warehouse operates."),
            "name": tr("Nome operacional do armazém.", "Operational warehouse name."),
            "code": tr("Código curto único para o armazém.", "Unique short code for the warehouse."),
            "location": tr("Sala, edifício ou referência interna.", "Room, building, or internal location."),
            "description": tr("Observações sobre cobertura, regras ou uso do armazém.", "Notes about warehouse coverage, rules, or usage."),
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        self.fields["branch"].queryset = appointment_branch_queryset(self.request)
        current_branch = getattr(self.request, "clinic_current_branch", None) if self.request else None
        if current_branch and not self.instance.pk and not self.initial.get("branch"):
            self.fields["branch"].initial = current_branch


class MedicationStockForm(StyledFormMixin, forms.ModelForm):
    armazem = WarehouseChoiceField(
        queryset=Armazem.objects.none(),
        label=tr("Armazém", "Warehouse"),
    )
    medicamento = MedicationChoiceField(
        queryset=Medicamento.objects.none(),
        label=tr("Medicamento", "Medication"),
    )

    class Meta:
        model = EstoqueMedicamento
        fields = [
            "armazem",
            "medicamento",
            "quantidade",
            "stock_minimo",
            "ponto_reposicao",
            "stock_maximo",
            "last_counted_at",
            "observacoes",
        ]
        labels = {
            "quantidade": tr("Stock actual", "Current stock"),
            "stock_minimo": tr("Stock mínimo", "Minimum stock"),
            "ponto_reposicao": tr("Ponto de reposição", "Reorder point"),
            "stock_maximo": tr("Stock máximo", "Maximum stock"),
            "last_counted_at": tr("Última contagem", "Last count"),
            "observacoes": tr("Observações", "Notes"),
        }
        widgets = {
            "last_counted_at": forms.DateInput(attrs={"type": "date"}),
            "observacoes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        self.fields["armazem"].queryset = (
            Armazem.objects.select_related("branch")
            .filter(branch__in=appointment_branch_queryset(self.request), is_active=True)
            .order_by("branch__name", "name")
        )
        self.fields["medicamento"].queryset = Medicamento.objects.filter(is_active=True).order_by("name", "dosagem")
        if self.request and not self.is_bound and not self.instance.pk:
            requested_medication = self.request.GET.get("medicamento")
            if requested_medication and self.fields["medicamento"].queryset.filter(pk=requested_medication).exists():
                self.fields["medicamento"].initial = requested_medication


class ConsumableStockForm(StyledFormMixin, forms.ModelForm):
    armazem = WarehouseChoiceField(
        queryset=Armazem.objects.none(),
        label=tr("Armazém", "Warehouse"),
    )
    consumivel = ConsumableChoiceField(
        queryset=Consumivel.objects.none(),
        label=tr("Consumível", "Consumable"),
    )

    class Meta:
        model = EstoqueConsumivel
        fields = [
            "armazem",
            "consumivel",
            "quantidade",
            "stock_minimo",
            "ponto_reposicao",
            "stock_maximo",
            "last_counted_at",
            "observacoes",
        ]
        labels = {
            "quantidade": tr("Stock actual", "Current stock"),
            "stock_minimo": tr("Stock mínimo", "Minimum stock"),
            "ponto_reposicao": tr("Ponto de reposição", "Reorder point"),
            "stock_maximo": tr("Stock máximo", "Maximum stock"),
            "last_counted_at": tr("Última contagem", "Last count"),
            "observacoes": tr("Observações", "Notes"),
        }
        widgets = {
            "last_counted_at": forms.DateInput(attrs={"type": "date"}),
            "observacoes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        self.fields["armazem"].queryset = (
            Armazem.objects.select_related("branch")
            .filter(branch__in=appointment_branch_queryset(self.request), is_active=True)
            .order_by("branch__name", "name")
        )
        self.fields["consumivel"].queryset = Consumivel.objects.filter(is_active=True).order_by("name")
        if self.request and not self.is_bound and not self.instance.pk:
            requested_consumable = self.request.GET.get("consumivel")
            if requested_consumable and self.fields["consumivel"].queryset.filter(pk=requested_consumable).exists():
                self.fields["consumivel"].initial = requested_consumable


class InventoryMovementForm(StyledFormMixin, forms.ModelForm):
    armazem = WarehouseChoiceField(
        queryset=Armazem.objects.none(),
        label=tr("Armazém", "Warehouse"),
    )
    medicamento = MedicationChoiceField(
        queryset=Medicamento.objects.none(),
        required=False,
        label=tr("Medicamento", "Medication"),
    )
    consumivel = ConsumableChoiceField(
        queryset=Consumivel.objects.none(),
        required=False,
        label=tr("Consumível", "Consumable"),
    )

    class Meta:
        model = MovimentoInventario
        fields = [
            "armazem",
            "item_type",
            "medicamento",
            "consumivel",
            "movement_type",
            "quantity",
            "unit_cost",
            "reference",
            "notes",
        ]
        labels = {
            "item_type": tr("Tipo de item", "Item type"),
            "movement_type": tr("Tipo de movimento", "Movement type"),
            "quantity": tr("Quantidade", "Quantity"),
            "unit_cost": tr("Custo unitário", "Unit cost"),
            "reference": tr("Referência", "Reference"),
            "notes": tr("Notas", "Notes"),
        }
        help_texts = {
            "movement_type": tr(
                "Em Ajuste, a quantidade passa a ser o valor contado no stock.",
                "In Adjustment, quantity becomes the counted stock value.",
            ),
            "reference": tr(
                "Ex.: compra, requisição, ajuste físico, devolução.",
                "Example: purchase, requisition, stock count, return.",
            ),
        }
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        self.apply_widget_classes()
        warehouse_queryset = (
            Armazem.objects.select_related("branch")
            .filter(branch__in=appointment_branch_queryset(self.request), is_active=True)
            .order_by("branch__name", "name")
        )
        self.fields["armazem"].queryset = warehouse_queryset
        self.fields["medicamento"].queryset = Medicamento.objects.filter(is_active=True).order_by("name", "dosagem")
        self.fields["consumivel"].queryset = Consumivel.objects.filter(is_active=True).order_by("name")

    def clean(self):
        cleaned_data = super().clean()
        item_type = cleaned_data.get("item_type")
        warehouse = cleaned_data.get("armazem")
        medication = cleaned_data.get("medicamento")
        consumable = cleaned_data.get("consumivel")
        movement_type = cleaned_data.get("movement_type")
        quantity = cleaned_data.get("quantity")

        if item_type == MovimentoInventario.ItemTypeChoices.MEDICAMENTO and medication is None:
            self.add_error("medicamento", tr("Seleccione o medicamento deste movimento.", "Select the medication for this movement."))
        if item_type == MovimentoInventario.ItemTypeChoices.CONSUMIVEL and consumable is None:
            self.add_error("consumivel", tr("Seleccione o consumível deste movimento.", "Select the consumable for this movement."))

        if movement_type == MovimentoInventario.MovementTypeChoices.SAIDA and warehouse and quantity:
            if item_type == MovimentoInventario.ItemTypeChoices.MEDICAMENTO and medication is not None:
                current_stock = EstoqueMedicamento.objects.filter(armazem=warehouse, medicamento=medication).first()
                if current_stock is None or quantity > current_stock.quantidade:
                    self.add_error("quantity", tr("A saída não pode ser superior ao stock disponível.", "The exit cannot exceed available stock."))
            if item_type == MovimentoInventario.ItemTypeChoices.CONSUMIVEL and consumable is not None:
                current_stock = EstoqueConsumivel.objects.filter(armazem=warehouse, consumivel=consumable).first()
                if current_stock is None or quantity > current_stock.quantidade:
                    self.add_error("quantity", tr("A saída não pode ser superior ao stock disponível.", "The exit cannot exceed available stock."))

        return cleaned_data

    @transaction.atomic
    def save(self, *, user=None, commit=True):
        movement = super().save(commit=False)
        movement.created_by = user

        if movement.item_type == MovimentoInventario.ItemTypeChoices.MEDICAMENTO:
            stock_entry, _ = EstoqueMedicamento.objects.get_or_create(
                armazem=movement.armazem,
                medicamento=movement.medicamento,
            )
        else:
            stock_entry, _ = EstoqueConsumivel.objects.get_or_create(
                armazem=movement.armazem,
                consumivel=movement.consumivel,
            )

        stock_before = stock_entry.quantidade
        if movement.movement_type == MovimentoInventario.MovementTypeChoices.ENTRADA:
            stock_after = stock_before + movement.quantity
        elif movement.movement_type == MovimentoInventario.MovementTypeChoices.SAIDA:
            stock_after = stock_before - movement.quantity
        else:
            stock_after = movement.quantity

        stock_entry.quantidade = stock_after
        stock_entry.last_counted_at = timezone.localdate()
        stock_entry.save(update_fields=["quantidade", "last_counted_at", "updated_at"])

        movement.stock_before = stock_before
        movement.stock_after = stock_after

        if commit:
            movement.save()
            self.save_m2m()

        return movement


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
