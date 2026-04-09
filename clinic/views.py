import logging
from datetime import date, timedelta
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, transaction
from django.db.models import Count, Exists, Max, OuterRef, Prefetch, Q, Sum
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from accounts.models import Clinic
from accounts.ui import (
    BRANCH_SESSION_KEY,
    LANGUAGE_SESSION_KEY,
    available_branches_for_user,
    get_system_preferences,
    get_system_default_language,
    ui_text,
)
from accounts.views.base_view import AppPermissionMixin, ClinicPageMixin, ModalDetailMixin, ModalFormMixin
from clinic.forms import (
    AppointmentForm,
    ConsultationForm,
    MedicationForm,
    DepartmentForm,
    PatientForm,
    SpecialtyForm,
    WorkScheduleBatchCreateForm,
    WorkScheduleForm,
)
from clinic.models import Agendamento, Consulta, Departamento, Especialidade, HorarioTrabalho, Medicamento, Medico, Paciente


logger = logging.getLogger(__name__)


def get_patient_code_prefix() -> str:
    preferences = get_system_preferences()
    if preferences and preferences.patient_code_prefix:
        return preferences.patient_code_prefix
    return "PCCP000"


def format_patient_code(patient_id: int, prefix: str | None = None) -> str:
    return f"{prefix or get_patient_code_prefix()}{patient_id}"


def attach_patient_codes(patients, prefix: str | None = None):
    patient_prefix = prefix or get_patient_code_prefix()
    for patient in patients:
        patient.display_code = format_patient_code(patient.pk, patient_prefix)
    return patients


def patient_queryset():
    return Paciente.objects.select_related("user", "hospital", "branch").annotate(
        total_appointments=Count("agendamentos", distinct=True),
        total_consultations=Count("agendamentos__consulta", distinct=True),
        last_appointment=Max("agendamentos__data"),
        last_consultation_at=Max("agendamentos__consulta__data_consulta"),
    )


def patient_history_queryset():
    history_entries = Prefetch(
        "agendamentos",
        queryset=Agendamento.objects.select_related(
            "medico__user",
            "medico__especialidade",
            "medico__departamento",
            "medico__departamento__branch",
            "branch",
            "consulta",
            "hospital",
        ).order_by("-data", "-hora"),
        to_attr="history_entries",
    )
    return patient_queryset().prefetch_related(history_entries)


def work_schedule_queryset():
    today = timezone.localdate()
    return (
        HorarioTrabalho.objects.select_related(
            "user",
            "branch",
            "user__medico__especialidade",
            "user__medico__departamento",
            "user__medico__departamento__branch",
            "user__medico__hospital",
        )
        .annotate(
            appointments_today=Count(
                "user__medico__agendamentos",
                filter=Q(user__medico__agendamentos__data=today),
                distinct=True,
            ),
            future_appointments=Count(
                "user__medico__agendamentos",
                filter=Q(user__medico__agendamentos__data__gte=today),
                distinct=True,
            ),
            last_appointment_date=Max("user__medico__agendamentos__data"),
        )
        .order_by("weekday", "start_time", "user__first_name", "user__last_name", "user__username")
    )


def appointment_queryset():
    return (
        Agendamento.objects.select_related(
            "paciente__user",
            "paciente__branch",
            "medico__user",
            "medico__especialidade",
            "medico__departamento",
            "medico__departamento__branch",
            "branch",
            "consulta",
            "hospital",
        )
        .annotate(
            has_consultation=Exists(
                Consulta.objects.filter(agendamento=OuterRef("pk"))
            )
        )
        .order_by("data", "hora", "paciente__user__first_name", "paciente__user__last_name")
    )


def appointment_professional_schedule_queryset():
    return work_schedule_queryset().filter(is_active=True, accepts_appointments=True)


def build_appointment_professionals(schedule_list, reference_date):
    registry = {}
    for schedule in schedule_list:
        entry = registry.setdefault(
            schedule.user_id,
            {
                "user_id": schedule.user_id,
                "professional_name": schedule.professional_name,
                "username": schedule.user.username,
                "role_labels": [],
                "branch_names": [],
                "schedule_blocks": 0,
                "linked_medico": schedule.linked_medico is not None,
                "doctor_badge": "",
                "appointments_today": schedule.appointments_today or 0,
                "future_appointments": schedule.future_appointments or 0,
                "is_on_duty_today": False,
            },
        )
        role_label = schedule.get_role_display()
        if role_label not in entry["role_labels"]:
            entry["role_labels"].append(role_label)
        if schedule.branch.name not in entry["branch_names"]:
            entry["branch_names"].append(schedule.branch.name)
        entry["schedule_blocks"] += 1
        entry["is_on_duty_today"] = entry["is_on_duty_today"] or schedule.applies_to_date(reference_date)
        entry["appointments_today"] = max(entry["appointments_today"], schedule.appointments_today or 0)
        entry["future_appointments"] = max(entry["future_appointments"], schedule.future_appointments or 0)

        if schedule.linked_medico is not None and not entry["doctor_badge"]:
            badge_parts = []
            if schedule.linked_medico.especialidade_id:
                badge_parts.append(schedule.linked_medico.especialidade.name)
            if schedule.linked_medico.departamento_id:
                badge_parts.append(schedule.linked_medico.departamento.name)
            if schedule.linked_medico.crm:
                badge_parts.append(schedule.linked_medico.crm)
            entry["doctor_badge"] = " · ".join(part for part in badge_parts if part)

    return sorted(registry.values(), key=lambda item: item["professional_name"].lower())


def serialize_work_schedule(schedule):
    linked_medico = schedule.linked_medico
    next_shift_date = schedule.next_occurrence_date()
    return {
        "id": schedule.pk,
        "professional_name": schedule.professional_name,
        "user_id": schedule.user_id,
        "username": schedule.user.username,
        "email": schedule.user.email,
        "role": schedule.role,
        "role_label": schedule.get_role_display(),
        "weekday": schedule.weekday,
        "weekday_label": schedule.get_weekday_display(),
        "shift_name": schedule.display_shift_name,
        "start_time": schedule.start_time.strftime("%H:%M"),
        "end_time": schedule.end_time.strftime("%H:%M"),
        "break_start": schedule.break_start.strftime("%H:%M") if schedule.break_start else "",
        "break_end": schedule.break_end.strftime("%H:%M") if schedule.break_end else "",
        "break_label": schedule.break_label,
        "slot_minutes": schedule.slot_minutes,
        "valid_from": schedule.valid_from.isoformat(),
        "valid_until": schedule.valid_until.isoformat() if schedule.valid_until else None,
        "accepts_appointments": schedule.accepts_appointments,
        "is_active": schedule.is_active,
        "notes": schedule.display_notes,
        "branch_id": schedule.branch_id,
        "branch_name": schedule.branch.name,
        "appointments_today": schedule.appointments_today or 0,
        "future_appointments": schedule.future_appointments or 0,
        "linked_doctor": linked_medico is not None,
        "doctor_badge": (
            " · ".join(
                part
                for part in [
                    linked_medico.especialidade.name if linked_medico and linked_medico.especialidade_id else "",
                    linked_medico.departamento.name if linked_medico and linked_medico.departamento_id else "",
                    linked_medico.crm if linked_medico else "",
                ]
                if part
            )
            if linked_medico is not None
            else ""
        ),
        "next_occurrence_date": next_shift_date.isoformat() if next_shift_date else None,
        "detail_url": reverse("clinic:work_schedule_detail", args=[schedule.pk]),
        "edit_url": reverse("clinic:work_schedule_update", args=[schedule.pk]),
        "toggle_url": reverse("clinic:work_schedule_toggle_status", args=[schedule.pk]),
    }


def custom_login(request):
    """Tela de login personalizada para a aplicação Clinic"""
    if request.user.is_authenticated:
        return redirect('clinic:index')

    next_url = request.POST.get('next') or request.GET.get('next') or ''
    context = {
        'next_url': next_url,
    }

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''

        try:
            user = authenticate(request, username=username, password=password)

            if user is not None:
                login(request, user)
        except DatabaseError:
            logger.exception(
                "Falha de banco/tunel durante o login do usuario '%s'.",
                username or '<vazio>',
            )
            messages.error(
                request,
                ui_text(
                    request,
                    'Nao foi possivel conectar ao banco de dados. Verifique o tunel SSH/MySQL e tente novamente.',
                    'The database connection is unavailable. Check the SSH/MySQL tunnel and try again.',
                ),
            )
            return render(request, 'clinic/login.html', context, status=503)

        if user is not None:
            profile = getattr(user, "profile", None)
            request.session[LANGUAGE_SESSION_KEY] = (
                profile.preferred_language if profile and profile.preferred_language else get_system_default_language()
            )
            available_branches = list(available_branches_for_user(user))
            if available_branches:
                if profile and profile.default_branch_id:
                    selected_branch = next(
                        (branch for branch in available_branches if branch.id == profile.default_branch_id),
                        available_branches[0],
                    )
                else:
                    selected_branch = available_branches[0]
                request.session[BRANCH_SESSION_KEY] = selected_branch.id
            messages.success(
                request,
                ui_text(
                    request,
                    'Bem-vindo, %(user)s!',
                    'Welcome, %(user)s!',
                )
                % {"user": user.get_full_name() or user.username},
            )
            return redirect(next_url or 'clinic:index')
        else:
            messages.error(
                request,
                ui_text(request, 'Credenciais inválidas. Tente novamente.', 'Invalid credentials. Please try again.'),
            )

    return render(request, 'clinic/login.html', context)


def custom_logout(request):
    """Logout personalizado"""
    logout(request)
    messages.info(
        request,
        ui_text(request, 'Sessão terminada com sucesso.', 'You have been signed out successfully.'),
    )
    return redirect('clinic:login')


@login_required(login_url='clinic:login')
def dashboard(request):
    greeting_name = request.user.get_short_name() or request.user.first_name or request.user.username
    current_branch = getattr(request, "clinic_current_branch", None)

    context = {
        'segment': 'dashboard',
        'meta_title': ui_text(request, 'Clinic Plus | Painel de Operacoes', 'Clinic Plus | Operations dashboard'),
        'page_title': ui_text(request, 'Painel de Operacoes', 'Operations dashboard'),
        'page_subtitle': ui_text(
            request,
            'Uma visao mais limpa da agenda, do atendimento e da saude financeira da clinica.',
            'A cleaner overview of scheduling, patient flow, and clinic financial health.',
        ),
        'branch_scope_label': (
            (
                ui_text(
                    request,
                    'Sucursal activa: %(branch)s',
                    'Active branch: %(branch)s',
                )
                % {"branch": current_branch.name}
            )
            if current_branch else ""
        ),
        'current_date': timezone.localdate(),
        'greeting_name': greeting_name,
        'current_branch': current_branch,
        'total_clinics': Clinic.objects.filter(is_active=True).count() or Clinic.objects.count() or 1,
        'total_doctors': 5,
        'total_patients': 24,
        'total_appointments': 48,
        'completed_consultations': 42,
        'appointments_today': 6,
        'revenue_today': 'MZN 2 850,00',
        'revenue_month': 'MZN 45 230,00',
        'satisfaction_rate': 96,
        'confirmed_rate': 88,
        'monthly_target_progress': 76,
        'daily_capacity': 68,
        'pending_followups': 3,
        'service_mix': [
            {'label': ui_text(request, 'Consultas confirmadas', 'Confirmed consultations'), 'value': 88, 'tone': 'success'},
            {'label': ui_text(request, 'Capacidade ocupada hoje', 'Capacity occupied today'), 'value': 68, 'tone': 'info'},
            {'label': ui_text(request, 'Meta de receita do mes', 'Monthly revenue target'), 'value': 76, 'tone': 'warning'},
        ],
        'timeline_events': [
            {
                'title': ui_text(request, 'Checklist da recepcao concluido', 'Reception checklist completed'),
                'time': '08:10',
                'icon': 'task_alt',
                'tone': 'success',
            },
            {
                'title': ui_text(request, '3 retornos precisam de confirmacao', '3 follow-ups need confirmation'),
                'time': '09:00',
                'icon': 'call',
                'tone': 'warning',
            },
            {
                'title': ui_text(request, 'Laboratorio enviou resultados pendentes', 'Laboratory sent pending results'),
                'time': '10:25',
                'icon': 'lab_profile',
                'tone': 'info',
            },
            {
                'title': ui_text(request, 'Financeiro fechou conciliacao parcial', 'Finance closed a partial reconciliation'),
                'time': '11:40',
                'icon': 'payments',
                'tone': 'primary',
            },
        ],

        'top_doctors': [
            {
                'name': 'Dr. João Silva',
                'specialty': ui_text(request, 'Cardiologia', 'Cardiology'),
                'appointments': 12,
                'satisfaction': 98
            },
            {
                'name': 'Dra. Maria Santos',
                'specialty': ui_text(request, 'Pediatria', 'Pediatrics'),
                'appointments': 11,
                'satisfaction': 97
            },
            {
                'name': 'Dr. Carlos Oliveira',
                'specialty': ui_text(request, 'Ortopedia', 'Orthopedics'),
                'appointments': 10,
                'satisfaction': 95
            },
            {
                'name': 'Dra. Ana Costa',
                'specialty': ui_text(request, 'Dermatologia', 'Dermatology'),
                'appointments': 9,
                'satisfaction': 94
            },
            {
                'name': 'Dr. Paulo Ferreira',
                'specialty': ui_text(request, 'Neurologia', 'Neurology'),
                'appointments': 8,
                'satisfaction': 93
            },
        ],

        # Agendamentos recentes
        'recent_appointments': [
            {
                'patient': 'João Dos Santos',
                'doctor': 'Dr. João Silva',
                'specialty': ui_text(request, 'Cardiologia', 'Cardiology'),
                'time': '09:00',
                'status': ui_text(request, 'Confirmado', 'Confirmed'),
                'tone': 'success',
            },
            {
                'patient': 'Maria Silva',
                'doctor': 'Dra. Maria Santos',
                'specialty': ui_text(request, 'Pediatria', 'Pediatrics'),
                'time': '09:30',
                'status': ui_text(request, 'Confirmado', 'Confirmed'),
                'tone': 'success',
            },
            {
                'patient': 'Carlos Santos',
                'doctor': 'Dr. Carlos Oliveira',
                'specialty': ui_text(request, 'Ortopedia', 'Orthopedics'),
                'time': '10:00',
                'status': ui_text(request, 'Em andamento', 'In progress'),
                'tone': 'info',
            },
            {
                'patient': 'Ana Costa',
                'doctor': 'Dra. Ana Costa',
                'specialty': ui_text(request, 'Dermatologia', 'Dermatology'),
                'time': '10:30',
                'status': ui_text(request, 'Confirmado', 'Confirmed'),
                'tone': 'success',
            },
            {
                'patient': 'Paulo Oliveira',
                'doctor': 'Dr. Paulo Ferreira',
                'specialty': ui_text(request, 'Neurologia', 'Neurology'),
                'time': '11:00',
                'status': ui_text(request, 'Confirmado', 'Confirmed'),
                'tone': 'success',
            },
        ],
    }

    return render(request, 'clinic/index.html', context)


class PatientListView(AppPermissionMixin, ClinicPageMixin, ListView):
    template_name = "clinic/patients/list.html"
    context_object_name = "patients"
    permission_required = "clinic.view_paciente"
    segment = "patients"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Pacientes", "Patients")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Cadastro central de pacientes com acesso rápido à ficha, edição e histórico clínico.",
            "Central patient registry with quick access to records, editing, and clinical history.",
        )

    def get_queryset(self):
        return patient_queryset().order_by("user__first_name", "user__last_name", "cpf")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = patient_queryset()
        month_start = timezone.localdate().replace(day=1)
        patient_code_prefix = get_patient_code_prefix()
        attach_patient_codes(context["patients"], patient_code_prefix)
        context["total_patients"] = base_queryset.count()
        context["active_patients"] = base_queryset.filter(is_active=True).count()
        context["inactive_patients"] = base_queryset.filter(is_active=False).count()
        context["patients_with_history"] = base_queryset.filter(agendamentos__isnull=False).distinct().count()
        context["new_patients_this_month"] = base_queryset.filter(created_at__date__gte=month_start).count()
        return context


class PatientDetailView(AppPermissionMixin, ModalDetailMixin, ClinicPageMixin, DetailView):
    template_name = "clinic/patients/detail.html"
    permission_required = "clinic.view_paciente"
    segment = "patients"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Detalhes do paciente", "Patient details")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Resumo completo da ficha, contactos e sinais clínicos registados.",
            "Complete overview of the record, contacts, and registered clinical notes.",
        )

    def get_queryset(self):
        return patient_history_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        history_entries = list(getattr(self.object, "history_entries", []))
        latest_consultation = next(
            (entry for entry in history_entries if getattr(entry, "consulta", None)),
            None,
        )
        context["patient_code"] = format_patient_code(self.object.pk)
        context["detail_partial"] = "clinic/patients/includes/detail_content.html"
        context["modal_heading"] = self.object.full_name
        context["modal_description"] = ui_text(
            self.request,
            "Dados pessoais, emergência, alergias e resumo do histórico mais recente.",
            "Personal data, emergency information, allergies, and a summary of the most recent history.",
        )
        context["recent_history_entries"] = history_entries[:4]
        context["latest_consultation"] = latest_consultation
        return context


class PatientCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Paciente
    form_class = PatientForm
    template_name = "clinic/patients/form.html"
    modal_template_name = "clinic/patients/modal_form.html"
    success_url = reverse_lazy("clinic:patient_list")
    permission_required = "clinic.add_paciente"
    segment = "patients"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo paciente", "New patient")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe dados pessoais, contacto e informação clínica inicial do paciente.",
            "Register personal data, contact details, and the patient's initial clinical information.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar paciente", "Create patient")
        context["form_description"] = ui_text(
            self.request,
            "Preencha a ficha base do paciente para começar a acompanhar atendimentos e histórico.",
            "Fill in the patient's base record to start tracking visits and history.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar paciente", "Save patient")
        context["cancel_url"] = reverse("clinic:patient_list")
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_success_message(self) -> str:
        return ui_text(self.request, "Paciente criado com sucesso.", "Patient created successfully.")


class PatientUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    form_class = PatientForm
    template_name = "clinic/patients/form.html"
    modal_template_name = "clinic/patients/modal_form.html"
    success_url = reverse_lazy("clinic:patient_list")
    permission_required = "clinic.change_paciente"
    segment = "patients"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar paciente", "Edit patient")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize os dados cadastrais e o resumo clínico do paciente seleccionado.",
            "Update the selected patient's registration data and clinical summary.",
        )

    def get_queryset(self):
        return patient_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar paciente", "Edit patient")
        context["form_description"] = ui_text(
            self.request,
            "Revise contactos, documentos e observações clínicas sempre que houver mudanças.",
            "Review contacts, documents, and clinical notes whenever there are changes.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar paciente", "Update patient")
        context["cancel_url"] = reverse("clinic:patient_detail", args=[self.object.pk])
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_success_message(self) -> str:
        return ui_text(self.request, "Paciente actualizado com sucesso.", "Patient updated successfully.")


class PatientToggleStatusView(AppPermissionMixin, View):
    permission_required = "clinic.change_paciente"
    login_url = "clinic:login"

    @transaction.atomic
    def post(self, request, pk):
        patient = get_object_or_404(patient_queryset(), pk=pk)
        patient.is_active = not patient.is_active
        patient.save(update_fields=["is_active", "updated_at"])
        if patient.user.is_active != patient.is_active:
            patient.user.is_active = patient.is_active
            patient.user.save(update_fields=["is_active"])

        return JsonResponse(
            {
                "success": True,
                "message": ui_text(
                    request,
                    "Paciente %(patient)s %(status)s com sucesso.",
                    "Patient %(patient)s %(status)s successfully.",
                )
                % {
                    "patient": patient.full_name,
                    "status": ui_text(
                        request,
                        "activado" if patient.is_active else "desactivado",
                        "activated" if patient.is_active else "deactivated",
                    ),
                },
                "redirect_url": reverse("clinic:patient_list"),
            }
        )


class PatientPdfDownloadView(AppPermissionMixin, View):
    permission_required = "clinic.view_paciente"
    login_url = "clinic:login"

    def get(self, request, pk):
        patient = get_object_or_404(patient_history_queryset(), pk=pk)
        history_entries = list(getattr(patient, "history_entries", []))
        latest_consultation = next(
            (entry for entry in history_entries if getattr(entry, "consulta", None)),
            None,
        )

        html = render_to_string(
            "clinic/patients/record_pdf.html",
            {
                "patient": patient,
                "patient_code": format_patient_code(patient.pk),
                "history_entries": history_entries[:8],
                "latest_consultation": latest_consultation,
                "generated_at": timezone.localtime(),
                "request": request,
            },
            request=request,
        )

        from weasyprint import HTML

        pdf_bytes = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
        filename = f"ficha-paciente-{format_patient_code(patient.pk).lower()}.pdf"
        return FileResponse(
            BytesIO(pdf_bytes),
            as_attachment=True,
            filename=filename,
            content_type="application/pdf",
        )


class PatientHistoryListView(AppPermissionMixin, ClinicPageMixin, ListView):
    template_name = "clinic/patients/history_list.html"
    context_object_name = "patients"
    permission_required = ("clinic.view_paciente", "clinic.view_consulta")
    segment = "patient_history"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Histórico de pacientes", "Patient history")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Escolha um paciente para abrir toda a linha do tempo de consultas, notas e prescrições.",
            "Choose a patient to open the full timeline of visits, notes, and prescriptions.",
        )

    def get_queryset(self):
        return (
            patient_queryset()
            .filter(agendamentos__isnull=False)
            .distinct()
            .order_by("-last_consultation_at", "-last_appointment", "user__first_name", "user__last_name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = patient_queryset()
        attach_patient_codes(context["patients"])
        context["patients_with_history"] = self.object_list.count()
        context["patients_with_consultations"] = base_queryset.filter(agendamentos__consulta__isnull=False).distinct().count()
        context["total_consultations"] = Consulta.objects.count()
        context["appointments_today"] = Agendamento.objects.filter(data=timezone.localdate()).count()
        return context


class PatientHistoryDetailView(AppPermissionMixin, ClinicPageMixin, DetailView):
    template_name = "clinic/patients/history_detail.html"
    permission_required = ("clinic.view_paciente", "clinic.view_consulta")
    segment = "patient_history"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Histórico do paciente", "Patient history")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Linha do tempo clínica completa com consultas, prescrições e notas associadas.",
            "Complete clinical timeline with visits, prescriptions, and associated notes.",
        )

    def get_queryset(self):
        return patient_history_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        history_entries = list(getattr(self.object, "history_entries", []))
        consultation_entries = [entry for entry in history_entries if getattr(entry, "consulta", None)]
        latest_consultation = consultation_entries[0] if consultation_entries else None
        context["patient_code"] = format_patient_code(self.object.pk)
        context["history_entries"] = history_entries
        context["consultation_entries"] = consultation_entries
        context["latest_consultation"] = latest_consultation
        context["completed_appointments"] = sum(1 for entry in history_entries if entry.status == "concluido")
        context["cancel_url"] = reverse("clinic:patient_history_list")
        return context


class AppointmentListView(AppPermissionMixin, ClinicPageMixin, ListView):
    template_name = "clinic/appointments/list.html"
    context_object_name = "appointments"
    permission_required = "clinic.view_agendamento"
    segment = "appointments"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Marcações", "Bookings")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Acompanhe as marcações previstas, concluídas e com consulta associada num único painel.",
            "Track scheduled, completed, and consultation-linked bookings from a single workspace.",
        )

    def get_queryset(self):
        return appointment_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        base_queryset = appointment_queryset()
        context["total_appointments"] = base_queryset.count()
        context["appointments_today"] = base_queryset.filter(data=today).count()
        context["appointments_this_week"] = base_queryset.filter(data__range=(week_start, week_end)).count()
        context["completed_appointments"] = base_queryset.filter(status="concluido").count()
        context["appointments_with_consultation"] = base_queryset.filter(has_consultation=True).count()
        context["pending_appointments"] = base_queryset.filter(status="agendado", data__gte=today).count()
        context["workspace_heading"] = ui_text(self.request, "Painel de marcações", "Bookings workspace")
        context["workspace_description"] = ui_text(
            self.request,
            "Veja rapidamente paciente, profissional, horário e se já existe consulta registada para cada marcação.",
            "Quickly review the patient, professional, time slot, and whether a visit has already been recorded for each booking.",
        )
        context["primary_action_url"] = reverse("clinic:appointment_agenda")
        context["primary_action_label"] = ui_text(self.request, "Abrir agenda", "Open agenda")
        context["show_primary_action"] = True
        return context


class AppointmentCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Agendamento
    form_class = AppointmentForm
    template_name = "clinic/appointments/form.html"
    modal_template_name = "clinic/appointments/modal_form.html"
    success_url = reverse_lazy("clinic:appointment_list")
    permission_required = "clinic.add_agendamento"
    segment = "appointments"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Nova marcação", "New booking")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe uma nova marcação ligando paciente, profissional clínico, sucursal, data e hora.",
            "Register a new booking linking patient, clinical professional, branch, date, and time.",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar marcação", "Create booking")
        context["form_description"] = ui_text(
            self.request,
            "Escolha o paciente, o profissional clínico, a sucursal e a janela exacta da marcação.",
            "Choose the patient, clinical professional, branch, and the exact booking slot.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar marcação", "Save booking")
        context["cancel_url"] = reverse("clinic:appointment_list")
        context["wide_fields"] = ("motivo", "notas")
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Marcação criada com sucesso.", "Booking created successfully.")


class AppointmentConsultationView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Consulta
    form_class = ConsultationForm
    template_name = "clinic/consultations/form.html"
    modal_template_name = "clinic/consultations/modal_form.html"
    success_url = reverse_lazy("clinic:appointment_list")
    segment = "appointments"

    def dispatch(self, request, *args, **kwargs):
        self.appointment = get_object_or_404(appointment_queryset(), pk=kwargs["appointment_pk"])
        self.existing_consultation = getattr(self.appointment, "consulta", None)

        if (
            self.existing_consultation is None
            and self.appointment.status in {"cancelado", "nao_compareceu"}
        ):
            messages.error(
                request,
                ui_text(
                    request,
                    "Não é possível registar consulta para uma marcação cancelada ou marcada como não compareceu.",
                    "It is not possible to record a consultation for a cancelled or no-show booking.",
                ),
            )
            return redirect("clinic:appointment_list")

        return super().dispatch(request, *args, **kwargs)

    def get_permission_required(self):
        if self.existing_consultation is not None:
            return ("clinic.change_consulta",)
        return ("clinic.add_consulta",)

    def get_page_title(self) -> str:
        if self.existing_consultation is not None:
            return ui_text(self.request, "Actualizar consulta", "Update consultation")
        return ui_text(self.request, "Registar consulta", "Register consultation")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Complete o desfecho clínico da marcação com diagnóstico, prescrição e notas.",
            "Complete the clinical outcome of the booking with diagnosis, prescription, and notes.",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.existing_consultation is not None:
            kwargs["instance"] = self.existing_consultation
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["appointment"] = self.appointment
        context["form_title"] = (
            ui_text(self.request, "Editar consulta", "Edit consultation")
            if self.existing_consultation is not None
            else ui_text(self.request, "Nova consulta", "New consultation")
        )
        context["form_description"] = ui_text(
            self.request,
            "A consulta fica associada a esta marcação e conclui o atendimento clínico.",
            "The consultation is linked to this booking and closes the clinical encounter.",
        )
        context["submit_label"] = (
            ui_text(self.request, "Actualizar consulta", "Update consultation")
            if self.existing_consultation is not None
            else ui_text(self.request, "Guardar consulta", "Save consultation")
        )
        context["cancel_url"] = reverse("clinic:appointment_list")
        context["wide_fields"] = ("diagnostico", "prescricao", "notas_medico")
        return context

    def get_success_message(self) -> str:
        if self.existing_consultation is not None:
            return ui_text(self.request, "Consulta actualizada com sucesso.", "Consultation updated successfully.")
        return ui_text(self.request, "Consulta registada com sucesso.", "Consultation recorded successfully.")

    def form_valid(self, form):
        consultation = form.save(commit=False)
        consultation.agendamento = self.appointment
        consultation.save()
        self.object = consultation

        if self.appointment.status != "concluido":
            self.appointment.status = "concluido"
            self.appointment.save(update_fields=["status", "updated_at"])

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


class AppointmentAgendaView(AppPermissionMixin, ClinicPageMixin, TemplateView):
    template_name = "clinic/appointments/agenda.html"
    permission_required = "clinic.view_agendamento"
    segment = "appointment_agenda"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Agenda de consultas", "Consultation agenda")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Seleccione um profissional e acompanhe a semana completa de consultas agendadas para ele.",
            "Select a professional and follow the full week of consultations booked for them.",
        )

    def _parse_anchor_date(self):
        raw_value = (self.request.GET.get("date") or "").strip()
        if not raw_value:
            return timezone.localdate()
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            return timezone.localdate()

    def _selected_professional(self, professionals):
        raw_value = (self.request.GET.get("professional") or "").strip()
        try:
            selected_user_id = int(raw_value)
        except (TypeError, ValueError):
            selected_user_id = None

        for professional in professionals:
            if professional["user_id"] == selected_user_id:
                return professional
        return professionals[0] if professionals else None

    def _build_agenda_url(self, *, professional_id, anchor_date):
        params = {}
        if professional_id:
            params["professional"] = professional_id
        if anchor_date:
            params["date"] = anchor_date.isoformat()
        base_url = reverse("clinic:appointment_agenda")
        return f"{base_url}?{urlencode(params)}" if params else base_url

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        anchor_date = self._parse_anchor_date()
        week_start = anchor_date - timedelta(days=anchor_date.weekday())
        week_end = week_start + timedelta(days=6)
        professional_schedules = list(appointment_professional_schedule_queryset())
        professionals = build_appointment_professionals(professional_schedules, today)
        selected_professional = self._selected_professional(professionals)
        selected_schedules = (
            [schedule for schedule in professional_schedules if schedule.user_id == selected_professional["user_id"]]
            if selected_professional
            else []
        )
        selected_user_id = selected_professional["user_id"] if selected_professional else None
        selected_medico = (
            Medico.objects.select_related("user", "especialidade", "departamento", "departamento__branch", "hospital")
            .filter(user_id=selected_user_id)
            .first()
            if selected_user_id
            else None
        )
        weekly_appointments = (
            list(
                appointment_queryset().filter(
                    medico__user_id=selected_user_id,
                    data__range=(week_start, week_end),
                )
            )
            if selected_user_id is not None
            else []
        )
        appointments_by_day = {}
        for appointment in weekly_appointments:
            appointments_by_day.setdefault(appointment.data, []).append(appointment)

        week_days = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            day_schedules = sorted(
                [schedule for schedule in selected_schedules if schedule.applies_to_date(day)],
                key=lambda schedule: (schedule.start_time, schedule.end_time),
            )
            week_days.append(
                {
                    "date": day,
                    "is_today": day == today,
                    "is_anchor": day == anchor_date,
                    "schedule_blocks": day_schedules,
                    "appointments": appointments_by_day.get(day, []),
                }
            )

        professional_id = selected_user_id
        upcoming_appointments = (
            list(
                appointment_queryset()
                .filter(medico__user_id=selected_user_id, data__gte=today)
                .order_by("data", "hora")[:8]
            )
            if selected_user_id is not None
            else []
        )

        context["professionals"] = professionals
        context["selected_professional"] = selected_professional
        context["selected_professional_id"] = professional_id
        context["selected_professional_schedules"] = sorted(
            selected_schedules,
            key=lambda schedule: (schedule.weekday, schedule.start_time, schedule.end_time),
        )
        context["selected_medico"] = selected_medico
        context["selected_date"] = anchor_date.isoformat()
        context["week_start"] = week_start
        context["week_end"] = week_end
        context["week_days"] = week_days
        context["weekly_appointment_count"] = len(weekly_appointments)
        context["upcoming_appointments"] = upcoming_appointments
        context["prev_week_url"] = self._build_agenda_url(
            professional_id=professional_id,
            anchor_date=week_start - timedelta(days=7),
        )
        context["next_week_url"] = self._build_agenda_url(
            professional_id=professional_id,
            anchor_date=week_start + timedelta(days=7),
        )
        context["today_week_url"] = self._build_agenda_url(
            professional_id=professional_id,
            anchor_date=today,
        )
        context["list_url"] = reverse("clinic:appointment_list")
        context["create_url"] = reverse("clinic:appointment_create")
        return context


class SpecialtyListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = Especialidade
    template_name = "clinic/structure/specialties/list.html"
    context_object_name = "specialties"
    permission_required = "clinic.view_especialidade"
    segment = "specialties"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Especialidades", "Specialties")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Defina as designações clínicas usadas nos perfis dos médicos.",
            "Define the clinical designations used in doctor profiles.",
        )

    def get_queryset(self):
        return Especialidade.objects.annotate(doctor_count=Count("medico")).order_by("name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = self.get_queryset()
        context["total_specialties"] = base_queryset.count()
        context["specialties_with_doctors"] = base_queryset.filter(doctor_count__gt=0).count()
        context["total_linked_doctors"] = Medico.objects.filter(especialidade__isnull=False).count()
        return context


class SpecialtyCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Especialidade
    form_class = SpecialtyForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:specialty_list")
    permission_required = "clinic.add_especialidade"
    segment = "specialties"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Nova especialidade", "New specialty")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe uma nova designação clínica para os profissionais.",
            "Register a new clinical designation for professionals.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar especialidade", "Create specialty")
        context["form_description"] = ui_text(
            self.request,
            "Esta especialidade poderá ser atribuída aos médicos e aparecerá nas consultas e agendas.",
            "This specialty can be assigned to doctors and will appear in consultations and agendas.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar especialidade", "Save specialty")
        context["cancel_url"] = reverse("clinic:specialty_list")
        context["wide_fields"] = ("description",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Especialidade criada com sucesso.", "Specialty created successfully.")


class SpecialtyUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = Especialidade.objects.all()
    form_class = SpecialtyForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:specialty_list")
    permission_required = "clinic.change_especialidade"
    segment = "specialties"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar especialidade", "Edit specialty")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize a designação e a descrição desta especialidade.",
            "Update the designation and description of this specialty.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar especialidade", "Edit specialty")
        context["form_description"] = ui_text(
            self.request,
            "As alterações reflectem-se imediatamente nos perfis clínicos associados.",
            "Changes are immediately reflected in the linked clinical profiles.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar especialidade", "Update specialty")
        context["cancel_url"] = reverse("clinic:specialty_list")
        context["wide_fields"] = ("description",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Especialidade actualizada com sucesso.", "Specialty updated successfully.")


class DepartmentListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = Departamento
    template_name = "clinic/structure/departments/list.html"
    context_object_name = "departments"
    permission_required = "clinic.view_departamento"
    segment = "departments"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Departamentos", "Departments")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Organize os serviços clínicos por sucursal e defina o responsável de cada área.",
            "Organize clinical services by branch and define the lead for each area.",
        )

    def get_queryset(self):
        return (
            Departamento.objects.select_related(
                "branch",
                "hospital",
                "responsavel__user",
                "responsavel__especialidade",
            )
            .annotate(doctor_count=Count("medicos"))
            .order_by("branch__name", "name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = self.get_queryset()
        context["total_departments"] = base_queryset.count()
        context["departments_with_lead"] = base_queryset.filter(responsavel__isnull=False).count()
        context["departments_with_doctors"] = base_queryset.filter(doctor_count__gt=0).count()
        return context


class DepartmentCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Departamento
    form_class = DepartmentForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:department_list")
    permission_required = "clinic.add_departamento"
    segment = "departments"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo departamento", "New department")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Crie um serviço clínico por sucursal e defina a liderança médica.",
            "Create a clinical service by branch and define the medical lead.",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar departamento", "Create department")
        context["form_description"] = ui_text(
            self.request,
            "Use departamentos para estruturar áreas como Ginecologia, Pediatria ou Ortopedia.",
            "Use departments to structure areas such as Gynecology, Pediatrics, or Orthopedics.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar departamento", "Save department")
        context["cancel_url"] = reverse("clinic:department_list")
        context["wide_fields"] = ("descricao",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Departamento criado com sucesso.", "Department created successfully.")


class DepartmentUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = Departamento.objects.select_related("branch", "responsavel__user")
    form_class = DepartmentForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:department_list")
    permission_required = "clinic.change_departamento"
    segment = "departments"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar departamento", "Edit department")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize o serviço, a sucursal e o responsável desta área clínica.",
            "Update the service, branch, and lead of this clinical area.",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar departamento", "Edit department")
        context["form_description"] = ui_text(
            self.request,
            "As alterações reflectem-se nos perfis médicos e na leitura operacional da sucursal.",
            "Changes are reflected in doctor profiles and the branch operational view.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar departamento", "Update department")
        context["cancel_url"] = reverse("clinic:department_list")
        context["wide_fields"] = ("descricao",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Departamento actualizado com sucesso.", "Department updated successfully.")


class MedicationListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = Medicamento
    template_name = "clinic/structure/medications/list.html"
    context_object_name = "medications"
    permission_required = "clinic.view_medicamento"
    segment = "medications"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Medicamentos", "Medications")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Catálogo de medicamentos e stock de referência usado pela operação clínica e farmácia.",
            "Medication catalog and reference stock used by clinical operations and pharmacy.",
        )

    def get_queryset(self):
        return Medicamento.objects.order_by("name", "dosagem")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = self.get_queryset()
        context["total_medications"] = base_queryset.count()
        context["in_stock_medications"] = base_queryset.filter(quantidade__gt=0).count()
        context["low_stock_medications"] = base_queryset.filter(quantidade__gt=0, quantidade__lte=10).count()
        context["total_units"] = base_queryset.aggregate(total=Sum("quantidade")).get("total") or 0
        return context


class MedicationCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Medicamento
    form_class = MedicationForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:medication_list")
    permission_required = "clinic.add_medicamento"
    segment = "medications"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo medicamento", "New medication")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe um medicamento para catálogo clínico e controlo de stock.",
            "Register a medication for the clinical catalog and stock control.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar medicamento", "Create medication")
        context["form_description"] = ui_text(
            self.request,
            "Use esta ficha para manter o catálogo farmacêutico organizado.",
            "Use this record to keep the pharmacy catalog organized.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar medicamento", "Save medication")
        context["cancel_url"] = reverse("clinic:medication_list")
        context["wide_fields"] = ("descricao",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Medicamento criado com sucesso.", "Medication created successfully.")


class MedicationUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = Medicamento.objects.all()
    form_class = MedicationForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:medication_list")
    permission_required = "clinic.change_medicamento"
    segment = "medications"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar medicamento", "Edit medication")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize princípio activo, stock e preço de referência.",
            "Update the active ingredient, stock, and reference price.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar medicamento", "Edit medication")
        context["form_description"] = ui_text(
            self.request,
            "As alterações reflectem-se imediatamente nas listagens operacionais.",
            "Changes are immediately reflected in operational listings.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar medicamento", "Update medication")
        context["cancel_url"] = reverse("clinic:medication_list")
        context["wide_fields"] = ("descricao",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Medicamento actualizado com sucesso.", "Medication updated successfully.")


class WorkScheduleListView(AppPermissionMixin, ClinicPageMixin, ListView):
    template_name = "clinic/schedules/list.html"
    context_object_name = "schedules"
    permission_required = "clinic.view_horariotrabalho"
    segment = "schedules"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Horários", "Schedules")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Grade semanal da equipa clínica, independente das marcações, já pronta para sincronizar com agenda.",
            "Weekly team roster, independent from bookings, already prepared to sync with scheduling.",
        )

    def get_queryset(self):
        return work_schedule_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        schedule_list = list(context["schedules"])
        today_schedules = [schedule for schedule in schedule_list if schedule.applies_to_date(today)]
        context["schedules"] = schedule_list
        context["total_schedule_blocks"] = len(schedule_list)
        context["active_schedule_blocks"] = sum(1 for schedule in schedule_list if schedule.is_active)
        context["scheduled_professionals"] = len({schedule.user_id for schedule in schedule_list})
        context["today_on_duty"] = len(today_schedules)
        context["appointment_ready"] = len(
            {schedule.user_id for schedule in schedule_list if schedule.accepts_appointments}
        )
        context["today_schedules"] = sorted(
            today_schedules,
            key=lambda schedule: (schedule.start_time, schedule.professional_name.lower()),
        )[:6]
        context["schedule_calendar_payload"] = [
            serialize_work_schedule(schedule) for schedule in schedule_list
        ]
        context["calendar_anchor_date"] = today.isoformat()
        return context


class WorkScheduleDetailView(AppPermissionMixin, ModalDetailMixin, ClinicPageMixin, DetailView):
    template_name = "clinic/schedules/detail.html"
    permission_required = "clinic.view_horariotrabalho"
    segment = "schedules"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Detalhes do horário", "Schedule details")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Resumo operacional do turno, validade, sincronização e ocupação da agenda.",
            "Operational summary of the shift, validity, synchronization, and calendar occupancy.",
        )

    def get_queryset(self):
        return work_schedule_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        today_appointments = list(self.object.appointment_queryset(on_date=today)[:5])
        upcoming_appointments = list(self.object.appointment_queryset().filter(data__gte=today)[:6])
        context["detail_partial"] = "clinic/schedules/includes/detail_content.html"
        context["modal_heading"] = self.object.professional_name
        context["modal_description"] = ui_text(
            self.request,
            "Turno semanal, dados da sucursal e integração com a agenda clínica.",
            "Weekly shift, branch details, and clinical calendar integration.",
        )
        context["linked_medico"] = self.object.linked_medico
        context["next_shift_date"] = self.object.next_occurrence_date(today)
        context["is_on_duty_today"] = self.object.applies_to_date(today)
        context["today_appointments"] = today_appointments
        context["upcoming_appointments"] = upcoming_appointments
        return context


class WorkScheduleCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = HorarioTrabalho
    form_class = WorkScheduleBatchCreateForm
    template_name = "clinic/schedules/form.html"
    modal_template_name = "clinic/schedules/modal_form.html"
    success_url = reverse_lazy("clinic:work_schedule_list")
    permission_required = "clinic.add_horariotrabalho"
    segment = "schedules"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo horário", "New schedule")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe o horário base de médicos, enfermeiros e outros colaboradores por sucursal.",
            "Register the base schedule of doctors, nurses, and other collaborators by branch.",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form")
        context["form_title"] = ui_text(self.request, "Criar horário", "Create schedule")
        context["form_description"] = ui_text(
            self.request,
            "Crie um ou vários blocos semanais de uma vez, com opção de ajustar horas diferentes por dia.",
            "Create one or multiple weekly blocks at once, with the option to adjust different hours by day.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar horários", "Save schedules")
        context["cancel_url"] = reverse("clinic:work_schedule_list")
        context["schedule_form_mode"] = "batch_create"
        context["weekday_override_groups"] = form.get_weekday_override_groups() if form else []
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Horário criado com sucesso.", "Schedule created successfully.")

    def form_valid(self, form):
        created_schedules = form.save()
        self.object = created_schedules[0] if created_schedules else None
        total_created = len(created_schedules)
        message = (
            ui_text(
                self.request,
                "%(count)s horário criado com sucesso.",
                "%(count)s schedule created successfully.",
            )
            if total_created == 1
            else ui_text(
                self.request,
                "%(count)s horários criados com sucesso.",
                "%(count)s schedules created successfully.",
            )
        ) % {"count": total_created}

        if self.is_modal():
            return JsonResponse(
                {
                    "success": True,
                    "message": message,
                    "reload": True,
                }
            )

        messages.success(self.request, message)
        return redirect(self.get_success_url())


class WorkScheduleUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    form_class = WorkScheduleForm
    template_name = "clinic/schedules/form.html"
    modal_template_name = "clinic/schedules/modal_form.html"
    success_url = reverse_lazy("clinic:work_schedule_list")
    permission_required = "clinic.change_horariotrabalho"
    segment = "schedules"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar horário", "Edit schedule")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Ajuste dias, intervalos e regras do turno sem perder o histórico da equipa.",
            "Adjust days, intervals, and shift rules without losing team history.",
        )

    def get_queryset(self):
        return work_schedule_queryset()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar horário", "Edit schedule")
        context["form_description"] = ui_text(
            self.request,
            "Actualize a escala do profissional seleccionado e mantenha a sincronização preparada para agendamentos.",
            "Update the selected professional's roster and keep synchronization ready for appointments.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar horário", "Update schedule")
        context["cancel_url"] = reverse("clinic:work_schedule_detail", args=[self.object.pk])
        context["wide_fields"] = "notes"
        context["schedule_form_mode"] = "single_update"
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Horário actualizado com sucesso.", "Schedule updated successfully.")


class WorkScheduleToggleStatusView(AppPermissionMixin, View):
    permission_required = "clinic.change_horariotrabalho"
    login_url = "clinic:login"

    @transaction.atomic
    def post(self, request, pk):
        schedule = get_object_or_404(work_schedule_queryset(), pk=pk)
        schedule.is_active = not schedule.is_active
        schedule.save(update_fields=["is_active", "updated_at"])

        return JsonResponse(
            {
                "success": True,
                "message": ui_text(
                    request,
                    "Horário de %(professional)s %(status)s com sucesso.",
                    "Schedule for %(professional)s %(status)s successfully.",
                )
                % {
                    "professional": schedule.professional_name,
                    "status": ui_text(
                        request,
                        "activado" if schedule.is_active else "desactivado",
                        "activated" if schedule.is_active else "deactivated",
                    ),
                },
                "redirect_url": reverse("clinic:work_schedule_list"),
            }
        )

