from django.contrib import admin
from .models import (
    Hospital, Especialidade, Medico, Paciente, 
    Agendamento, Consulta, Medicamento, Departamento
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
    list_display = ('get_full_name', 'hospital', 'especialidade', 'crm', 'phone')
    search_fields = ('user__first_name', 'user__last_name', 'crm')
    list_filter = ('hospital', 'especialidade')
    
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
    list_filter = ('status', 'data', 'hospital')
    
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
    list_display = ('name', 'principio_ativo', 'dosagem', 'quantidade', 'preco')
    search_fields = ('name', 'principio_ativo')
    list_filter = ('created_at',)

@admin.register(Departamento)
class DepartamentoAdmin(admin.ModelAdmin):
    list_display = ('name', 'hospital', 'get_responsavel')
    search_fields = ('name',)
    list_filter = ('hospital',)
    
    def get_responsavel(self, obj):
        if obj.responsavel:
            return obj.responsavel.user.get_full_name()
        return "Sem responsável"
    get_responsavel.short_description = 'Responsável'

