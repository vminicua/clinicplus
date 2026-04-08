import logging

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.db import DatabaseError
from django.utils import timezone
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
                'Nao foi possivel conectar ao banco de dados. Verifique o tunel SSH/MySQL e tente novamente.',
            )
            return render(request, 'clinic/login.html', context, status=503)

        if user is not None:
            messages.success(request, f'Bem-vindo, {user.get_full_name() or user.username}!')
            return redirect(next_url or 'clinic:index')
        else:
            messages.error(request, 'Credenciais inválidas. Tente novamente.')

    return render(request, 'clinic/login.html', context)

def custom_logout(request):
    """Logout personalizado"""
    logout(request)
    messages.info(request, 'Você foi desconectado com sucesso.')
    return redirect('clinic:login')

@login_required(login_url='clinic:login')
def dashboard(request):
    greeting_name = request.user.get_short_name() or request.user.first_name or request.user.username

    context = {
        'segment': 'dashboard',
        'meta_title': 'Clinic Plus | Painel de Operacoes',
        'page_title': 'Painel de Operacoes',
        'page_subtitle': 'Uma visao mais limpa da agenda, do atendimento e da saude financeira da clinica.',
        'current_date': timezone.localdate(),
        'greeting_name': greeting_name,
        'total_hospitals': 1,
        'total_doctors': 5,
        'total_patients': 24,
        'total_appointments': 48,
        'completed_consultations': 42,
        'appointments_today': 6,
        'revenue_today': 'R$ 2,850.00',
        'revenue_month': 'R$ 45,230.00',
        'satisfaction_rate': 96,
        'confirmed_rate': 88,
        'monthly_target_progress': 76,
        'daily_capacity': 68,
        'pending_followups': 3,
        'service_mix': [
            {'label': 'Consultas confirmadas', 'value': 88, 'tone': 'success'},
            {'label': 'Capacidade ocupada hoje', 'value': 68, 'tone': 'info'},
            {'label': 'Meta de receita do mes', 'value': 76, 'tone': 'warning'},
        ],
        'timeline_events': [
            {
                'title': 'Checklist da recepcao concluido',
                'time': '08:10',
                'icon': 'task_alt',
                'tone': 'success',
            },
            {
                'title': '3 retornos precisam de confirmacao',
                'time': '09:00',
                'icon': 'call',
                'tone': 'warning',
            },
            {
                'title': 'Laboratorio enviou resultados pendentes',
                'time': '10:25',
                'icon': 'lab_profile',
                'tone': 'info',
            },
            {
                'title': 'Financeiro fechou conciliacao parcial',
                'time': '11:40',
                'icon': 'payments',
                'tone': 'primary',
            },
        ],

        'top_doctors': [
            {
                'name': 'Dr. João Silva',
                'specialty': 'Cardiologia',
                'appointments': 12,
                'satisfaction': 98
            },
            {
                'name': 'Dra. Maria Santos',
                'specialty': 'Pediatria',
                'appointments': 11,
                'satisfaction': 97
            },
            {
                'name': 'Dr. Carlos Oliveira',
                'specialty': 'Ortopedia',
                'appointments': 10,
                'satisfaction': 95
            },
            {
                'name': 'Dra. Ana Costa',
                'specialty': 'Dermatologia',
                'appointments': 9,
                'satisfaction': 94
            },
            {
                'name': 'Dr. Paulo Ferreira',
                'specialty': 'Neurologia',
                'appointments': 8,
                'satisfaction': 93
            },
        ],

        # Agendamentos recentes
        'recent_appointments': [
            {
                'patient': 'João Dos Santos',
                'doctor': 'Dr. João Silva',
                'specialty': 'Cardiologia',
                'time': '09:00',
                'status': 'Confirmado'
            },
            {
                'patient': 'Maria Silva',
                'doctor': 'Dra. Maria Santos',
                'specialty': 'Pediatria',
                'time': '09:30',
                'status': 'Confirmado'
            },
            {
                'patient': 'Carlos Santos',
                'doctor': 'Dr. Carlos Oliveira',
                'specialty': 'Ortopedia',
                'time': '10:00',
                'status': 'Em andamento'
            },
            {
                'patient': 'Ana Costa',
                'doctor': 'Dra. Ana Costa',
                'specialty': 'Dermatologia',
                'time': '10:30',
                'status': 'Confirmado'
            },
            {
                'patient': 'Paulo Oliveira',
                'doctor': 'Dr. Paulo Ferreira',
                'specialty': 'Neurologia',
                'time': '11:00',
                'status': 'Confirmado'
            },
        ],
    }

    return render(request, 'clinic/index.html', context)

