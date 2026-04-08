from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    LANGUAGE_PORTUGUESE = "pt"
    LANGUAGE_ENGLISH = "en"
    LANGUAGE_CHOICES = [
        (LANGUAGE_PORTUGUESE, "Português (Moçambique)"),
        (LANGUAGE_ENGLISH, "English"),
    ]

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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Perfil do utilizador"
        verbose_name_plural = "Perfis dos utilizadores"

    def __str__(self) -> str:
        return f"Perfil de {self.user.get_username()}"

