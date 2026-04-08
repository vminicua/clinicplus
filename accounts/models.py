from django.conf import settings
from django.db import models


LANGUAGE_PORTUGUESE = "pt"
LANGUAGE_ENGLISH = "en"
LANGUAGE_CHOICES = [
    (LANGUAGE_PORTUGUESE, "Português (Moçambique)"),
    (LANGUAGE_ENGLISH, "English"),
]

CURRENCY_METICAL = "MZN"
CURRENCY_US_DOLLAR = "USD"
CURRENCY_RAND = "ZAR"
CURRENCY_CHOICES = [
    (CURRENCY_METICAL, "Metical (MZN)"),
    (CURRENCY_US_DOLLAR, "US Dollar (USD)"),
    (CURRENCY_RAND, "Rand Sul-Africano (ZAR)"),
]


class Branch(models.Model):
    name = models.CharField(max_length=150, unique=True, verbose_name="Nome da sucursal")
    code = models.CharField(max_length=20, unique=True, verbose_name="Código interno")
    city = models.CharField(max_length=120, blank=True, verbose_name="Cidade")
    address = models.CharField(max_length=255, blank=True, verbose_name="Endereço")
    phone = models.CharField(max_length=30, blank=True, verbose_name="Telefone")
    email = models.EmailField(blank=True, verbose_name="Email")
    is_active = models.BooleanField(default=True, verbose_name="Activa")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "Sucursal"
        verbose_name_plural = "Sucursais"

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class SystemPreference(models.Model):
    singleton_key = models.CharField(
        max_length=30,
        unique=True,
        default="system",
        editable=False,
    )
    default_language = models.CharField(
        max_length=5,
        choices=LANGUAGE_CHOICES,
        default=LANGUAGE_PORTUGUESE,
        verbose_name="Idioma por defeito",
    )
    default_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=CURRENCY_METICAL,
        verbose_name="Moeda por defeito",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Preferência do sistema"
        verbose_name_plural = "Preferências do sistema"

    def __str__(self) -> str:
        return "Preferências do sistema"

    @classmethod
    def get_solo(cls):
        preferences, _ = cls.objects.get_or_create(singleton_key="system")
        return preferences


class UserProfile(models.Model):
    LANGUAGE_PORTUGUESE = LANGUAGE_PORTUGUESE
    LANGUAGE_ENGLISH = LANGUAGE_ENGLISH
    LANGUAGE_CHOICES = LANGUAGE_CHOICES

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="Utilizador",
    )
    preferred_language = models.CharField(
        max_length=5,
        choices=LANGUAGE_CHOICES,
        default=LANGUAGE_PORTUGUESE,
        verbose_name="Idioma preferido",
    )
    assigned_branches = models.ManyToManyField(
        Branch,
        blank=True,
        related_name="user_profiles",
        verbose_name="Sucursais atribuídas",
    )
    default_branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_user_profiles",
        verbose_name="Sucursal principal",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Perfil do utilizador"
        verbose_name_plural = "Perfis dos utilizadores"

    def __str__(self) -> str:
        return f"Perfil de {self.user.get_username()}"
