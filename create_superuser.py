"""
Script seguro para criar um superuser sem guardar credenciais no repositorio.
Execute: python create_superuser.py
"""

import getpass
import os

import django


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model


User = get_user_model()


def prompt_value(label: str, env_name: str, secret: bool = False, required: bool = True) -> str:
    value = os.getenv(env_name, "").strip()
    if value:
        return value

    while True:
        if secret:
            value = getpass.getpass(f"{label}: ").strip()
        else:
            value = input(f"{label}: ").strip()

        if value or not required:
            return value

        print(f"{label} nao pode ficar vazio.")


username = prompt_value("Username", "DJANGO_SUPERUSER_USERNAME")
email = prompt_value("Email (opcional)", "DJANGO_SUPERUSER_EMAIL", required=False)
password = prompt_value("Password", "DJANGO_SUPERUSER_PASSWORD", secret=True)

if User.objects.filter(username=username).exists():
    print(f"Usuario '{username}' ja existe no banco de dados.")
else:
    User.objects.create_superuser(username, email or None, password)
    print(f"Superuser '{username}' criado com sucesso.")
    print("Acesse: http://localhost:8000/admin/")
