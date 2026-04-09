from django.conf import settings
from django.db import models

from .i18n import translate_pair


LANGUAGE_PORTUGUESE = "pt"
LANGUAGE_ENGLISH = "en"
LANGUAGE_CHOICES = [
    (LANGUAGE_PORTUGUESE, translate_pair("Português (Moçambique)", "Portuguese (Mozambique)")),
    (LANGUAGE_ENGLISH, "English"),
]

CURRENCY_METICAL = "MZN"
CURRENCY_US_DOLLAR = "USD"
CURRENCY_RAND = "ZAR"
CURRENCY_CHOICES = [
    (CURRENCY_METICAL, translate_pair("Metical (MZN)", "Metical (MZN)")),
    (CURRENCY_US_DOLLAR, "US Dollar (USD)"),
    (CURRENCY_RAND, translate_pair("Rand Sul-Africano (ZAR)", "South African Rand (ZAR)")),
]


class Branch(models.Model):
    clinic = models.ForeignKey(
        "accounts.Clinic",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="branches",
        verbose_name=translate_pair("Clínica", "Clinic"),
    )
    name = models.CharField(
        max_length=150,
        unique=True,
        verbose_name=translate_pair("Nome da sucursal", "Branch name"),
    )
    code = models.CharField(
        max_length=20,
        unique=True,
        verbose_name=translate_pair("Código interno", "Internal code"),
    )
    legal_name = models.CharField(
        max_length=180,
        blank=True,
        verbose_name=translate_pair("Nome legal", "Legal name"),
    )
    nuit = models.CharField(
        max_length=30,
        blank=True,
        null=True,
        unique=True,
        verbose_name=translate_pair("NUIT", "Tax ID"),
    )
    logo = models.ImageField(
        upload_to="branches/logos/",
        blank=True,
        null=True,
        verbose_name=translate_pair("Logotipo", "Logo"),
    )
    favicon = models.ImageField(
        upload_to="branches/favicons/",
        blank=True,
        null=True,
        verbose_name=translate_pair("Favicon", "Favicon"),
    )
    city = models.CharField(max_length=120, blank=True, verbose_name=translate_pair("Cidade", "City"))
    province = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=translate_pair("Província / estado", "Province / state"),
    )
    country = models.CharField(
        max_length=120,
        blank=True,
        default="Moçambique",
        verbose_name=translate_pair("País", "Country"),
    )
    postal_code = models.CharField(
        max_length=30,
        blank=True,
        verbose_name=translate_pair("Código postal", "Postal code"),
    )
    address = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=translate_pair("Endereço", "Address"),
    )
    phone = models.CharField(max_length=30, blank=True, verbose_name=translate_pair("Telefone", "Phone"))
    email = models.EmailField(blank=True, verbose_name="Email")
    website = models.URLField(blank=True, verbose_name=translate_pair("Website", "Website"))
    manager_name = models.CharField(
        max_length=150,
        blank=True,
        verbose_name=translate_pair("Responsável", "Manager"),
    )
    manager_phone = models.CharField(
        max_length=30,
        blank=True,
        verbose_name=translate_pair("Telefone do responsável", "Manager phone"),
    )
    manager_email = models.EmailField(
        blank=True,
        verbose_name=translate_pair("Email do responsável", "Manager email"),
    )
    description = models.TextField(
        blank=True,
        verbose_name=translate_pair("Descrição", "Description"),
    )
    is_active = models.BooleanField(default=True, verbose_name=translate_pair("Activa", "Active"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = translate_pair("Sucursal", "Branch")
        verbose_name_plural = translate_pair("Sucursais", "Branches")

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class Clinic(models.Model):
    name = models.CharField(
        max_length=180,
        unique=True,
        verbose_name=translate_pair("Nome da clínica", "Clinic name"),
    )
    legal_name = models.CharField(
        max_length=220,
        blank=True,
        verbose_name=translate_pair("Nome legal", "Legal name"),
    )
    nuit = models.CharField(
        max_length=30,
        blank=True,
        null=True,
        unique=True,
        verbose_name=translate_pair("NUIT", "Tax ID"),
    )
    logo = models.ImageField(
        upload_to="clinics/logos/",
        blank=True,
        null=True,
        verbose_name=translate_pair("Logotipo", "Logo"),
    )
    favicon = models.ImageField(
        upload_to="clinics/favicons/",
        blank=True,
        null=True,
        verbose_name=translate_pair("Favicon", "Favicon"),
    )
    city = models.CharField(max_length=120, blank=True, verbose_name=translate_pair("Cidade", "City"))
    province = models.CharField(
        max_length=120,
        blank=True,
        verbose_name=translate_pair("Província / estado", "Province / state"),
    )
    country = models.CharField(
        max_length=120,
        blank=True,
        default="Moçambique",
        verbose_name=translate_pair("País", "Country"),
    )
    postal_code = models.CharField(
        max_length=30,
        blank=True,
        verbose_name=translate_pair("Código postal", "Postal code"),
    )
    address = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=translate_pair("Endereço", "Address"),
    )
    phone = models.CharField(max_length=30, blank=True, verbose_name=translate_pair("Telefone", "Phone"))
    email = models.EmailField(blank=True, verbose_name="Email")
    website = models.URLField(blank=True, verbose_name=translate_pair("Website", "Website"))
    description = models.TextField(
        blank=True,
        verbose_name=translate_pair("Descrição", "Description"),
    )
    is_active = models.BooleanField(default=True, verbose_name=translate_pair("Activa", "Active"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = translate_pair("Clínica", "Clinic")
        verbose_name_plural = translate_pair("Clínicas", "Clinics")

    def __str__(self) -> str:
        return self.name


class SystemPreference(models.Model):
    DEFAULT_PATIENT_CODE_PREFIX = "PCCP000"

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
        verbose_name=translate_pair("Idioma por defeito", "Default language"),
    )
    default_currency = models.CharField(
        max_length=3,
        choices=CURRENCY_CHOICES,
        default=CURRENCY_METICAL,
        verbose_name=translate_pair("Moeda por defeito", "Default currency"),
    )
    patient_code_prefix = models.CharField(
        max_length=20,
        default=DEFAULT_PATIENT_CODE_PREFIX,
        verbose_name=translate_pair("Prefixo do ID do paciente", "Patient ID prefix"),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = translate_pair("Preferência do sistema", "System preference")
        verbose_name_plural = translate_pair("Preferências do sistema", "System preferences")

    def __str__(self) -> str:
        return str(translate_pair("Preferências do sistema", "System preferences"))

    @classmethod
    def get_solo(cls):
        preferences, _ = cls.objects.get_or_create(singleton_key="system")
        return preferences

    def format_patient_code(self, patient_id: int) -> str:
        return f"{self.patient_code_prefix}{patient_id}"


class UserProfile(models.Model):
    LANGUAGE_PORTUGUESE = LANGUAGE_PORTUGUESE
    LANGUAGE_ENGLISH = LANGUAGE_ENGLISH
    LANGUAGE_CHOICES = LANGUAGE_CHOICES

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name=translate_pair("Utilizador", "User"),
    )
    preferred_language = models.CharField(
        max_length=5,
        choices=LANGUAGE_CHOICES,
        default=LANGUAGE_PORTUGUESE,
        verbose_name=translate_pair("Idioma preferido", "Preferred language"),
    )
    assigned_branches = models.ManyToManyField(
        Branch,
        blank=True,
        related_name="user_profiles",
        verbose_name=translate_pair("Sucursais atribuídas", "Assigned branches"),
    )
    default_branch = models.ForeignKey(
        Branch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_user_profiles",
        verbose_name=translate_pair("Sucursal principal", "Primary branch"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = translate_pair("Perfil do utilizador", "User profile")
        verbose_name_plural = translate_pair("Perfis dos utilizadores", "User profiles")

    def __str__(self) -> str:
        return str(
            translate_pair("Perfil de %(username)s", "Profile for %(username)s")
            % {"username": self.user.get_username()}
        )
