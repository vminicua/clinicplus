from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.contrib.auth.models import User
from django.utils import timezone

from accounts.models import Branch


MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "â€™", "â€œ", "â€“", "â€”")


def normalize_mojibake_text(value: str) -> str:
    if not isinstance(value, str) or not value:
        return value
    if not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value

    for encoding in ("latin-1", "cp1252"):
        try:
            candidate = value.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if candidate and candidate != value:
            return candidate
    return value


# Legacy organizational model kept only for backward compatibility.
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
        verbose_name = "Clínica legada"
        verbose_name_plural = "Clínicas legadas"
    
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
        return self.display_name

    @property
    def display_name(self):
        return normalize_mojibake_text(self.name)

    @property
    def display_description(self):
        return normalize_mojibake_text(self.description)

    def save(self, *args, **kwargs):
        self.name = normalize_mojibake_text(self.name)
        self.description = normalize_mojibake_text(self.description)
        self.icon = normalize_mojibake_text(self.icon)
        super().save(*args, **kwargs)


# Médicos Model
class Medico(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.SET_NULL,
        related_name='medicos',
        null=True,
        blank=True,
    )
    especialidade = models.ForeignKey(Especialidade, on_delete=models.SET_NULL, null=True)
    departamento = models.ForeignKey(
        "Departamento",
        on_delete=models.SET_NULL,
        related_name="medicos",
        null=True,
        blank=True,
    )
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


class HorarioTrabalho(models.Model):
    class RoleChoices(models.TextChoices):
        MEDICO = "medico", "Médico"
        ENFERMEIRO = "enfermeiro", "Enfermeiro(a)"
        LABORATORIO = "laboratorio", "Laboratório / Técnico"
        FARMACIA = "farmacia", "Farmácia / Stock"
        RECEPCAO = "recepcao", "Recepção"
        ADMINISTRATIVO = "administrativo", "Administrativo"
        OUTRO = "outro", "Outro"

    class WeekdayChoices(models.IntegerChoices):
        MONDAY = 0, "Segunda-feira"
        TUESDAY = 1, "Terça-feira"
        WEDNESDAY = 2, "Quarta-feira"
        THURSDAY = 3, "Quinta-feira"
        FRIDAY = 4, "Sexta-feira"
        SATURDAY = 5, "Sábado"
        SUNDAY = 6, "Domingo"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="horarios_trabalho")
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name="horarios_trabalho")
    role = models.CharField(max_length=20, choices=RoleChoices.choices)
    shift_name = models.CharField(max_length=120, blank=True)
    weekday = models.PositiveSmallIntegerField(choices=WeekdayChoices.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    break_start = models.TimeField(blank=True, null=True)
    break_end = models.TimeField(blank=True, null=True)
    slot_minutes = models.PositiveSmallIntegerField(default=30)
    valid_from = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(blank=True, null=True)
    accepts_appointments = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Horário de trabalho"
        verbose_name_plural = "Horários de trabalho"
        ordering = ("weekday", "start_time", "user__first_name", "user__last_name", "user__username")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "branch", "weekday", "start_time", "end_time", "valid_from"),
                name="unique_work_schedule_block",
            )
        ]

    def __str__(self):
        return f"{self.professional_name} · {self.get_weekday_display()} · {self.time_range_label}"

    @property
    def professional_name(self) -> str:
        return self.user.get_full_name() or self.user.username

    @classmethod
    def normalize_text(cls, value: str) -> str:
        return normalize_mojibake_text(value)

    @property
    def display_shift_name(self) -> str:
        return self.normalize_text(self.shift_name)

    @property
    def display_notes(self) -> str:
        return self.normalize_text(self.notes)

    @property
    def linked_medico(self):
        try:
            return self.user.medico
        except Medico.DoesNotExist:
            return None

    @property
    def time_range_label(self) -> str:
        return f"{self.start_time:%H:%M} - {self.end_time:%H:%M}"

    @property
    def break_label(self) -> str:
        if not self.break_start or not self.break_end:
            return ""
        return f"{self.break_start:%H:%M} - {self.break_end:%H:%M}"

    def applies_to_date(self, target_date) -> bool:
        if not self.is_active:
            return False
        if target_date < self.valid_from:
            return False
        if self.valid_until and target_date > self.valid_until:
            return False
        return target_date.weekday() == self.weekday

    def next_occurrence_date(self, from_date=None):
        if not self.is_active:
            return None

        reference_date = from_date or timezone.localdate()
        candidate = max(reference_date, self.valid_from)
        for offset in range(8):
            target_date = candidate + timedelta(days=offset)
            if self.applies_to_date(target_date):
                return target_date
        return None

    def appointment_queryset(self, on_date=None):
        medico = self.linked_medico
        if medico is None or not self.accepts_appointments:
            return Agendamento.objects.none()

        queryset = Agendamento.objects.select_related(
            "paciente__user",
            "medico__especialidade",
            "hospital",
        ).filter(medico=medico)

        if on_date is not None:
            queryset = queryset.filter(data=on_date)
        return queryset.order_by("data", "hora")

    @staticmethod
    def _time_ranges_overlap(start_a, end_a, start_b, end_b) -> bool:
        return start_a < end_b and start_b < end_a

    @staticmethod
    def _date_ranges_overlap(start_a, end_a, start_b, end_b) -> bool:
        end_a = end_a or date.max
        end_b = end_b or date.max
        return start_a <= end_b and start_b <= end_a

    def clean(self):
        errors = {}

        if self.end_time and self.start_time and self.end_time <= self.start_time:
            errors["end_time"] = "A hora de fim deve ser posterior à hora de início."

        break_pair = (self.break_start, self.break_end)
        if any(break_pair) and not all(break_pair):
            message = "Preencha o início e o fim da pausa, ou deixe ambos vazios."
            errors["break_start"] = message
            errors["break_end"] = message

        if self.break_start and self.break_end:
            if self.break_end <= self.break_start:
                errors["break_end"] = "O fim da pausa deve ser posterior ao início da pausa."
            if self.start_time and self.break_start <= self.start_time:
                errors["break_start"] = "A pausa deve começar depois do início do turno."
            if self.end_time and self.break_end >= self.end_time:
                errors["break_end"] = "A pausa deve terminar antes do fim do turno."

        if self.slot_minutes and not 5 <= self.slot_minutes <= 180:
            errors["slot_minutes"] = "A duração do bloco deve ficar entre 5 e 180 minutos."

        if self.valid_until and self.valid_from and self.valid_until < self.valid_from:
            errors["valid_until"] = "A data final não pode ser anterior à data inicial."

        if (
            self.is_active
            and self.user_id
            and self.branch_id
            and self.weekday is not None
            and self.start_time
            and self.end_time
            and self.valid_from
        ):
            overlapping_schedules = (
                HorarioTrabalho.objects.filter(
                    user=self.user,
                    branch=self.branch,
                    weekday=self.weekday,
                    is_active=True,
                )
                .exclude(pk=self.pk)
                .only("start_time", "end_time", "valid_from", "valid_until")
            )
            for existing_schedule in overlapping_schedules:
                if not self._time_ranges_overlap(
                    self.start_time,
                    self.end_time,
                    existing_schedule.start_time,
                    existing_schedule.end_time,
                ):
                    continue
                if not self._date_ranges_overlap(
                    self.valid_from,
                    self.valid_until,
                    existing_schedule.valid_from,
                    existing_schedule.valid_until,
                ):
                    continue
                errors["start_time"] = (
                    "Já existe um turno activo sobreposto para este profissional, sucursal e dia."
                )
                break

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.shift_name = self.normalize_text(self.shift_name)
        self.notes = self.normalize_text(self.notes)
        super().save(*args, **kwargs)


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
        on_delete=models.SET_NULL,
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
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        related_name='agendamentos',
        null=True,
        blank=True,
    )
    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.SET_NULL,
        related_name='agendamentos',
        null=True,
        blank=True,
    )
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

    @property
    def unit_name(self):
        if self.branch_id:
            return self.branch.name
        if self.hospital_id:
            return self.hospital.name
        return ""


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


class Armazem(models.Model):
    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="armazens",
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30, unique=True)
    location = models.CharField(max_length=255, blank=True)
    manager_name = models.CharField(max_length=150, blank=True)
    manager_phone = models.CharField(max_length=30, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Armazém"
        verbose_name_plural = "Armazéns"
        ordering = ("branch__name", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("branch", "name"),
                name="unique_warehouse_per_branch",
            )
        ]

    def __str__(self):
        return f"{self.display_name} · {self.branch.name}"

    @property
    def display_name(self):
        return normalize_mojibake_text(self.name)

    @property
    def display_description(self):
        return normalize_mojibake_text(self.description)

    def save(self, *args, **kwargs):
        self.name = normalize_mojibake_text(self.name)
        self.code = (normalize_mojibake_text(self.code) or "").upper()
        self.location = normalize_mojibake_text(self.location)
        self.manager_name = normalize_mojibake_text(self.manager_name)
        self.manager_phone = normalize_mojibake_text(self.manager_phone)
        self.description = normalize_mojibake_text(self.description)
        super().save(*args, **kwargs)


class Medicamento(models.Model):
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=40, blank=True, null=True, unique=True)
    principio_ativo = models.CharField(max_length=255)
    dosagem = models.CharField(max_length=100)
    unidade_medida = models.CharField(max_length=30, blank=True, default="un")
    quantidade = models.PositiveIntegerField(default=0)
    preco = models.DecimalField(max_digits=10, decimal_places=2)
    descricao = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Medicamento"
        verbose_name_plural = "Medicamentos"
        ordering = ("name", "dosagem")

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return normalize_mojibake_text(self.name)

    @property
    def display_description(self):
        return normalize_mojibake_text(self.descricao)

    @property
    def total_stock(self):
        return self.estoques.aggregate(total=Sum("quantidade")).get("total") or 0

    @property
    def low_stock_entries(self):
        return self.estoques.filter(quantidade__lte=models.F("stock_minimo"), stock_minimo__gt=0).count()

    def sync_legacy_quantity(self):
        total_quantity = self.total_stock
        if self.quantidade != total_quantity:
            self.quantidade = total_quantity
            self.save(update_fields=["quantidade", "updated_at"])

    def save(self, *args, **kwargs):
        self.name = normalize_mojibake_text(self.name)
        self.sku = normalize_mojibake_text(self.sku) or None
        self.principio_ativo = normalize_mojibake_text(self.principio_ativo)
        self.dosagem = normalize_mojibake_text(self.dosagem)
        self.unidade_medida = (normalize_mojibake_text(self.unidade_medida) or "").strip().lower()
        self.descricao = normalize_mojibake_text(self.descricao)
        super().save(*args, **kwargs)


class Consumivel(models.Model):
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=40, blank=True, null=True, unique=True)
    unidade_medida = models.CharField(max_length=30, blank=True, default="un")
    preco_referencia = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    descricao = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Consumível"
        verbose_name_plural = "Consumíveis"
        ordering = ("name",)

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return normalize_mojibake_text(self.name)

    @property
    def display_description(self):
        return normalize_mojibake_text(self.descricao)

    @property
    def total_stock(self):
        return self.estoques.aggregate(total=Sum("quantidade")).get("total") or 0

    @property
    def low_stock_entries(self):
        return self.estoques.filter(quantidade__lte=models.F("stock_minimo"), stock_minimo__gt=0).count()

    def save(self, *args, **kwargs):
        self.name = normalize_mojibake_text(self.name)
        self.sku = normalize_mojibake_text(self.sku) or None
        self.unidade_medida = (normalize_mojibake_text(self.unidade_medida) or "").strip().lower()
        self.descricao = normalize_mojibake_text(self.descricao)
        super().save(*args, **kwargs)


class EstoqueMedicamento(models.Model):
    armazem = models.ForeignKey(
        Armazem,
        on_delete=models.CASCADE,
        related_name="estoque_medicamentos",
    )
    medicamento = models.ForeignKey(
        Medicamento,
        on_delete=models.CASCADE,
        related_name="estoques",
    )
    quantidade = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=0)
    ponto_reposicao = models.PositiveIntegerField(default=0)
    stock_maximo = models.PositiveIntegerField(blank=True, null=True)
    observacoes = models.TextField(blank=True)
    last_counted_at = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Stock de medicamento"
        verbose_name_plural = "Stock de medicamentos"
        ordering = ("medicamento__name", "armazem__name")
        constraints = [
            models.UniqueConstraint(
                fields=("armazem", "medicamento"),
                name="unique_medication_stock_per_warehouse",
            )
        ]

    def __str__(self):
        return f"{self.medicamento.display_name} · {self.armazem.display_name}"

    @property
    def is_below_minimum(self):
        return self.stock_minimo > 0 and self.quantidade <= self.stock_minimo

    def save(self, *args, **kwargs):
        self.observacoes = normalize_mojibake_text(self.observacoes)
        super().save(*args, **kwargs)
        self.medicamento.sync_legacy_quantity()

    def delete(self, *args, **kwargs):
        medication = self.medicamento
        super().delete(*args, **kwargs)
        medication.sync_legacy_quantity()


class EstoqueConsumivel(models.Model):
    armazem = models.ForeignKey(
        Armazem,
        on_delete=models.CASCADE,
        related_name="estoque_consumiveis",
    )
    consumivel = models.ForeignKey(
        Consumivel,
        on_delete=models.CASCADE,
        related_name="estoques",
    )
    quantidade = models.PositiveIntegerField(default=0)
    stock_minimo = models.PositiveIntegerField(default=0)
    ponto_reposicao = models.PositiveIntegerField(default=0)
    stock_maximo = models.PositiveIntegerField(blank=True, null=True)
    observacoes = models.TextField(blank=True)
    last_counted_at = models.DateField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Stock de consumível"
        verbose_name_plural = "Stock de consumíveis"
        ordering = ("consumivel__name", "armazem__name")
        constraints = [
            models.UniqueConstraint(
                fields=("armazem", "consumivel"),
                name="unique_consumable_stock_per_warehouse",
            )
        ]

    def __str__(self):
        return f"{self.consumivel.display_name} · {self.armazem.display_name}"

    @property
    def is_below_minimum(self):
        return self.stock_minimo > 0 and self.quantidade <= self.stock_minimo

    def save(self, *args, **kwargs):
        self.observacoes = normalize_mojibake_text(self.observacoes)
        super().save(*args, **kwargs)


class MovimentoInventario(models.Model):
    class ItemTypeChoices(models.TextChoices):
        MEDICAMENTO = "medicamento", "Medicamento"
        CONSUMIVEL = "consumivel", "Consumível"

    class MovementTypeChoices(models.TextChoices):
        ENTRADA = "entrada", "Entrada"
        SAIDA = "saida", "Saída"
        AJUSTE = "ajuste", "Ajuste"

    armazem = models.ForeignKey(
        Armazem,
        on_delete=models.CASCADE,
        related_name="movimentos",
    )
    item_type = models.CharField(max_length=20, choices=ItemTypeChoices.choices)
    medicamento = models.ForeignKey(
        Medicamento,
        on_delete=models.CASCADE,
        related_name="movimentos_inventario",
        null=True,
        blank=True,
    )
    consumivel = models.ForeignKey(
        Consumivel,
        on_delete=models.CASCADE,
        related_name="movimentos_inventario",
        null=True,
        blank=True,
    )
    movement_type = models.CharField(max_length=20, choices=MovementTypeChoices.choices)
    quantity = models.PositiveIntegerField()
    stock_before = models.PositiveIntegerField(default=0)
    stock_after = models.PositiveIntegerField(default=0)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="inventory_movements",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Movimento de inventário"
        verbose_name_plural = "Movimentos de inventário"
        ordering = ("-created_at", "-id")

    def __str__(self):
        return f"{self.get_movement_type_display()} · {self.item_label}"

    @property
    def item_label(self):
        if self.item_type == self.ItemTypeChoices.MEDICAMENTO and self.medicamento_id:
            return self.medicamento.display_name
        if self.item_type == self.ItemTypeChoices.CONSUMIVEL and self.consumivel_id:
            return self.consumivel.display_name
        return ""

    def clean(self):
        errors = {}
        if self.quantity is None:
            errors["quantity"] = "Informe a quantidade do movimento."
        elif self.movement_type != self.MovementTypeChoices.AJUSTE and self.quantity <= 0:
            errors["quantity"] = "A quantidade deve ser maior que zero."

        if self.item_type == self.ItemTypeChoices.MEDICAMENTO:
            if not self.medicamento_id:
                errors["medicamento"] = "Seleccione um medicamento para este movimento."
            if self.consumivel_id:
                errors["consumivel"] = "Limpe o consumível quando o item for um medicamento."
        elif self.item_type == self.ItemTypeChoices.CONSUMIVEL:
            if not self.consumivel_id:
                errors["consumivel"] = "Seleccione um consumível para este movimento."
            if self.medicamento_id:
                errors["medicamento"] = "Limpe o medicamento quando o item for um consumível."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.reference = normalize_mojibake_text(self.reference)
        self.notes = normalize_mojibake_text(self.notes)
        super().save(*args, **kwargs)


# Departamentos Model
class Departamento(models.Model):
    name = models.CharField(max_length=255)
    branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        related_name="departamentos",
        null=True,
        blank=True,
    )
    hospital = models.ForeignKey(
        Hospital,
        on_delete=models.SET_NULL,
        related_name='departamentos',
        null=True,
        blank=True,
    )
    responsavel = models.ForeignKey(
        Medico,
        on_delete=models.SET_NULL,
        related_name="departamentos_responsavel",
        null=True,
        blank=True,
    )
    descricao = models.TextField(blank=True)
    
    class Meta:
        verbose_name = "Departamento"
        verbose_name_plural = "Departamentos"
    
    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return normalize_mojibake_text(self.name)

    @property
    def display_description(self):
        return normalize_mojibake_text(self.descricao)

    def save(self, *args, **kwargs):
        self.name = normalize_mojibake_text(self.name)
        self.descricao = normalize_mojibake_text(self.descricao)
        super().save(*args, **kwargs)

    @property
    def unit_name(self):
        if self.branch_id:
            return self.branch.name
        if self.hospital_id:
            return self.hospital.name
        return ""

