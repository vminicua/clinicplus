from django.contrib import admin
from .models import (
    Agendamento,
    Armazem,
    Consumivel,
    Consulta,
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

@admin.register(Hospital)
class HospitalAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone', 'city')
    search_fields = ('name', 'email')
    list_filter = ('city', 'state')

@admin.register(Especialidade)
class EspecialidadeAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(Medico)
class MedicoAdmin(admin.ModelAdmin):
    list_display = ('get_full_name', 'departamento', 'hospital', 'especialidade', 'crm', 'phone')
    search_fields = ('user__first_name', 'user__last_name', 'crm')
    list_filter = ('departamento', 'hospital', 'especialidade')
    
    def get_full_name(self, obj):
        return obj.user.get_full_name()
    get_full_name.short_description = 'Médico'

@admin.register(Paciente)
class PacienteAdmin(admin.ModelAdmin):
    list_display = ('get_full_name', 'get_clinic', 'cpf', 'phone', 'gender', 'is_active')
    search_fields = ('user__first_name', 'user__last_name', 'cpf')
    list_filter = ('branch', 'gender', 'city', 'is_active')
    
    def get_full_name(self, obj):
        return obj.user.get_full_name()
    get_full_name.short_description = 'Paciente'

    def get_clinic(self, obj):
        return obj.clinic_name or "Sem clínica"
    get_clinic.short_description = 'Clínica'

@admin.register(Agendamento)
class AgendamentoAdmin(admin.ModelAdmin):
    list_display = ('get_paciente', 'get_medico', 'data', 'hora', 'status')
    search_fields = ('paciente__user__first_name', 'medico__user__first_name')
    list_filter = ('status', 'data', 'branch', 'hospital')
    
    def get_paciente(self, obj):
        return obj.paciente.user.get_full_name()
    get_paciente.short_description = 'Paciente'
    
    def get_medico(self, obj):
        return obj.medico.user.get_full_name()
    get_medico.short_description = 'Médico'

@admin.register(Consulta)
class ConsultaAdmin(admin.ModelAdmin):
    list_display = ('get_paciente', 'get_medico', 'data_consulta')
    search_fields = ('agendamento__paciente__user__first_name',)
    
    def get_paciente(self, obj):
        return obj.agendamento.paciente.user.get_full_name()
    get_paciente.short_description = 'Paciente'
    
    def get_medico(self, obj):
        return obj.agendamento.medico.user.get_full_name()
    get_medico.short_description = 'Médico'

@admin.register(Medicamento)
class MedicamentoAdmin(admin.ModelAdmin):
    list_display = ('name', 'principio_ativo', 'dosagem', 'quantidade', 'preco', 'is_active')
    search_fields = ('name', 'principio_ativo', 'sku')
    list_filter = ('is_active', 'created_at')

@admin.register(Departamento)
class DepartamentoAdmin(admin.ModelAdmin):
    list_display = ('name', 'branch', 'hospital', 'get_responsavel')
    search_fields = ('name',)
    list_filter = ('branch', 'hospital')
    
    def get_responsavel(self, obj):
        if obj.responsavel:
            return obj.responsavel.user.get_full_name()
        return "Sem responsável"
    get_responsavel.short_description = 'Responsável'


@admin.register(HorarioTrabalho)
class HorarioTrabalhoAdmin(admin.ModelAdmin):
    list_display = ('get_profissional', 'branch', 'role', 'weekday', 'start_time', 'end_time', 'is_active')
    search_fields = ('user__first_name', 'user__last_name', 'user__username', 'branch__name', 'shift_name')
    list_filter = ('branch', 'role', 'weekday', 'accepts_appointments', 'is_active')

    def get_profissional(self, obj):
        return obj.professional_name
    get_profissional.short_description = 'Profissional'


@admin.register(Armazem)
class ArmazemAdmin(admin.ModelAdmin):
    list_display = ('name', 'branch', 'code', 'manager_name', 'is_active')
    search_fields = ('name', 'code', 'branch__name', 'manager_name')
    list_filter = ('branch', 'is_active')


@admin.register(Consumivel)
class ConsumivelAdmin(admin.ModelAdmin):
    list_display = ('name', 'sku', 'unidade_medida', 'preco_referencia', 'is_active')
    search_fields = ('name', 'sku')
    list_filter = ('is_active', 'created_at')


@admin.register(EstoqueMedicamento)
class EstoqueMedicamentoAdmin(admin.ModelAdmin):
    list_display = ('medicamento', 'armazem', 'quantidade', 'stock_minimo', 'ponto_reposicao', 'last_counted_at')
    search_fields = ('medicamento__name', 'armazem__name', 'armazem__branch__name')
    list_filter = ('armazem__branch', 'armazem')


@admin.register(EstoqueConsumivel)
class EstoqueConsumivelAdmin(admin.ModelAdmin):
    list_display = ('consumivel', 'armazem', 'quantidade', 'stock_minimo', 'ponto_reposicao', 'last_counted_at')
    search_fields = ('consumivel__name', 'armazem__name', 'armazem__branch__name')
    list_filter = ('armazem__branch', 'armazem')


@admin.register(MovimentoInventario)
class MovimentoInventarioAdmin(admin.ModelAdmin):
    list_display = ('item_label', 'armazem', 'movement_type', 'quantity', 'stock_before', 'stock_after', 'created_at')
    search_fields = ('reference', 'notes', 'armazem__name', 'medicamento__name', 'consumivel__name')
    list_filter = ('item_type', 'movement_type', 'armazem__branch', 'armazem')

