import logging

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, transaction
from django.db.models import Count, Max, Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.ui import (
    BRANCH_SESSION_KEY,
    LANGUAGE_SESSION_KEY,
    available_branches_for_user,
    get_system_default_language,
    ui_text,
)
from accounts.views.base_view import AppPermissionMixin, ClinicPageMixin, ModalDetailMixin, ModalFormMixin
from clinic.forms import PatientForm
from clinic.models import Agendamento, Consulta, Paciente


logger = logging.getLogger(__name__)


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
            "consulta",
            "hospital",
        ).order_by("-data", "-hora"),
        to_attr="history_entries",
    )
    return patient_queryset().prefetch_related(history_entries)


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
        'total_hospitals': 1,
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
        context["history_entries"] = history_entries
        context["consultation_entries"] = consultation_entries
        context["latest_consultation"] = latest_consultation
        context["completed_appointments"] = sum(1 for entry in history_entries if entry.status == "concluido")
        context["cancel_url"] = reverse("clinic:patient_history_list")
        return context

