from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from accounts.models import Branch

# Hospital/Clínica Model
class Hospital(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    zip_code = models.CharField(max_length=20)
    logo = models.ImageField(upload_to='hospital_logos/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Hospital"
        verbose_name_plural = "Hospitais"
    
    def __str__(self):
        return self.name


# Especialidades Model
class Especialidade(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True)
    
    class Meta:
        verbose_name = "Especialidade"
        verbose_name_plural = "Especialidades"
    
    def __str__(self):
        return self.name


# Médicos Model
class Medico(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name='medicos')
    especialidade = models.ForeignKey(Especialidade, on_delete=models.SET_NULL, null=True)
    crm = models.CharField(max_length=20, unique=True)
    phone = models.CharField(max_length=20)
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to='doctor_photos/', blank=True, null=True)
    availability_start = models.TimeField(default='08:00')
    availability_end = models.TimeField(default='18:00')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Médico"
        verbose_name_plural = "Médicos"
    
    def __str__(self):
        return f"Dr./Dra. {self.user.get_full_name()}"


# Pacientes Model
class Paciente(models.Model):
    GENDER_CHOICES = [
        ('M', 'Masculino'),
        ('F', 'Feminino'),
        ('O', 'Outro'),
    ]
    COUNTRY_CHOICES = [
        ('Moçambique', 'Moçambique'),
        ('África do Sul', 'África do Sul'),
        ('Angola', 'Angola'),
        ('Botswana', 'Botswana'),
        ('Brasil', 'Brasil'),
        ('Cabo Verde', 'Cabo Verde'),
        ('China', 'China'),
        ('Estados Unidos', 'Estados Unidos'),
        ('Eswatini', 'Eswatini'),
        ('França', 'França'),
        ('Índia', 'Índia'),
        ('Malawi', 'Malawi'),
        ('Portugal', 'Portugal'),
        ('Reino Unido', 'Reino Unido'),
        ('Tanzânia', 'Tanzânia'),
        ('Zâmbia', 'Zâmbia'),
        ('Zimbabwe', 'Zimbabwe'),
        ('Outro', 'Outro'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.CASCADE,
        related_name='pacientes',
        null=True,
        blank=True,
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        related_name='pacientes',
        null=True,
        blank=True,
    )
    cpf = models.CharField(max_length=14, unique=True)
    phone = models.CharField(max_length=20)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=100, choices=COUNTRY_CHOICES, default='Moçambique')
    state = models.CharField(max_length=100)
    zip_code = models.CharField(max_length=20, blank=True)
    emergency_contact = models.CharField(max_length=255)
    emergency_phone = models.CharField(max_length=20, blank=True)
    medical_history = models.TextField(blank=True)
    allergies = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Paciente"
        verbose_name_plural = "Pacientes"
    
    def __str__(self):
        return self.user.get_full_name()

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.username

    @property
    def clinic_name(self):
        if self.branch_id:
            return self.branch.name
        if self.hospital_id:
            return self.hospital.name
        return ""

    @property
    def age(self):
        if not self.date_of_birth:
            return None

        today = timezone.localdate()
        years = today.year - self.date_of_birth.year
        if (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day):
            years -= 1
        return years


# Agendamentos Model
class Agendamento(models.Model):
    STATUS_CHOICES = [
        ('agendado', 'Agendado'),
        ('concluido', 'Concluído'),
        ('cancelado', 'Cancelado'),
        ('nao_compareceu', 'Não Compareceu'),
    ]
    
    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name='agendamentos')
    medico = models.ForeignKey(Medico, on_delete=models.CASCADE, related_name='agendamentos')
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name='agendamentos')
    data = models.DateField()
    hora = models.TimeField()
    motivo = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='agendado')
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Agendamento"
        verbose_name_plural = "Agendamentos"
        unique_together = ['medico', 'data', 'hora']
    
    def __str__(self):
        return f"{self.paciente.user.get_full_name()} - {self.data} às {self.hora}"


# Consultas Model
class Consulta(models.Model):
    agendamento = models.OneToOneField(Agendamento, on_delete=models.CASCADE)
    diagnostico = models.TextField()
    prescricao = models.TextField()
    notas_medico = models.TextField(blank=True)
    data_consulta = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Consulta"
        verbose_name_plural = "Consultas"
    
    def __str__(self):
        return f"Consulta de {self.agendamento.paciente.user.get_full_name()}"


# Medicamentos Model
class Medicamento(models.Model):
    name = models.CharField(max_length=255)
    principio_ativo = models.CharField(max_length=255)
    dosagem = models.CharField(max_length=100)
    quantidade = models.IntegerField()
    preco = models.DecimalField(max_digits=10, decimal_places=2)
    descricao = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Medicamento"
        verbose_name_plural = "Medicamentos"
    
    def __str__(self):
        return self.name


# Departamentos Model
class Departamento(models.Model):
    name = models.CharField(max_length=255)
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name='departamentos')
    responsavel = models.ForeignKey(Medico, on_delete=models.SET_NULL, null=True, blank=True)
    descricao = models.TextField(blank=True)
    
    class Meta:
        verbose_name = "Departamento"
        verbose_name_plural = "Departamentos"
    
    def __str__(self):
        return self.name

