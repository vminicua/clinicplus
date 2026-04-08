import logging

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.db import DatabaseError
from django.utils import timezone

from accounts.ui import (
    BRANCH_SESSION_KEY,
    LANGUAGE_SESSION_KEY,
    available_branches_for_user,
    get_system_default_language,
    ui_text,
)
from clinic.models import Hospital, Medico, Paciente, Agendamento, Consulta

logger = logging.getLogger(__name__)

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

