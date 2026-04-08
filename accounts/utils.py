from collections import OrderedDict

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission

from .i18n import translate_pair


User = get_user_model()
tr = translate_pair

SYSTEM_PERMISSION_ACTIONS = ("add", "change", "delete", "view")
STANDARD_PERMISSION_LABELS = OrderedDict(
    [
        ("view", tr("Ver", "View")),
        ("add", tr("Criar", "Create")),
        ("change", tr("Editar", "Edit")),
        ("delete", tr("Eliminar", "Delete")),
    ]
)

APP_SECTION_METADATA = {
    "auth": {
        "title": tr("Acessos e segurança", "Access and security"),
        "description": tr(
            "Controlo de utilizadores, perfis e permissões do sistema.",
            "Control of users, roles, and system permissions.",
        ),
        "order": 10,
    },
    "accounts": {
        "title": tr("Organização e preferências", "Organization and preferences"),
        "description": tr(
            "Sucursais, preferências globais e definições complementares das contas.",
            "Branches, global preferences, and complementary account settings.",
        ),
        "order": 20,
    },
    "clinic": {
        "title": tr("Operação clínica", "Clinical operations"),
        "description": tr(
            "Permissões ligadas aos módulos operacionais da clínica.",
            "Permissions tied to the clinic operational modules.",
        ),
        "order": 30,
    },
    "admin": {
        "title": tr("Auditoria técnica", "Technical audit"),
        "description": tr(
            "Registos técnicos e logs do painel administrativo.",
            "Technical records and admin panel logs.",
        ),
        "order": 40,
    },
    "contenttypes": {
        "title": tr("Infra-estrutura Django", "Django infrastructure"),
        "description": tr("Permissões internas do framework.", "Internal framework permissions."),
        "order": 50,
    },
    "sessions": {
        "title": tr("Sessões", "Sessions"),
        "description": tr(
            "Controlo técnico de sessões de autenticação.",
            "Technical control of authentication sessions.",
        ),
        "order": 60,
    },
}

DEFAULT_ROLE_PERMISSION_MAP = {
    "Administrador do Sistema": {
        "add_user",
        "change_user",
        "delete_user",
        "view_user",
        "add_group",
        "change_group",
        "delete_group",
        "view_group",
        "add_permission",
        "change_permission",
        "delete_permission",
        "view_permission",
        "view_userprofile",
        "change_userprofile",
        "add_branch",
        "change_branch",
        "delete_branch",
        "view_branch",
        "add_systempreference",
        "change_systempreference",
        "delete_systempreference",
        "view_systempreference",
        "add_hospital",
        "change_hospital",
        "delete_hospital",
        "view_hospital",
        "add_especialidade",
        "change_especialidade",
        "delete_especialidade",
        "view_especialidade",
        "add_medico",
        "change_medico",
        "delete_medico",
        "view_medico",
        "add_paciente",
        "change_paciente",
        "delete_paciente",
        "view_paciente",
        "add_agendamento",
        "change_agendamento",
        "delete_agendamento",
        "view_agendamento",
        "add_consulta",
        "change_consulta",
        "delete_consulta",
        "view_consulta",
        "add_horariotrabalho",
        "change_horariotrabalho",
        "delete_horariotrabalho",
        "view_horariotrabalho",
        "add_medicamento",
        "change_medicamento",
        "delete_medicamento",
        "view_medicamento",
        "add_departamento",
        "change_departamento",
        "delete_departamento",
        "view_departamento",
    },
    "Gestor da Clínica / Sucursal": {
        "view_user",
        "change_user",
        "view_userprofile",
        "change_userprofile",
        "view_group",
        "view_branch",
        "add_branch",
        "change_branch",
        "view_systempreference",
        "change_systempreference",
        "view_hospital",
        "change_hospital",
        "view_especialidade",
        "add_especialidade",
        "change_especialidade",
        "view_medico",
        "add_medico",
        "change_medico",
        "view_paciente",
        "add_paciente",
        "change_paciente",
        "view_agendamento",
        "add_agendamento",
        "change_agendamento",
        "view_consulta",
        "view_horariotrabalho",
        "add_horariotrabalho",
        "change_horariotrabalho",
        "view_medicamento",
        "view_departamento",
        "add_departamento",
        "change_departamento",
    },
    "Recepcionista": {
        "view_paciente",
        "add_paciente",
        "change_paciente",
        "view_agendamento",
        "add_agendamento",
        "change_agendamento",
        "view_medico",
        "view_horariotrabalho",
        "view_especialidade",
        "view_hospital",
        "view_branch",
    },
    "Médico": {
        "view_paciente",
        "view_agendamento",
        "change_agendamento",
        "view_consulta",
        "add_consulta",
        "change_consulta",
        "view_medico",
        "view_horariotrabalho",
        "view_especialidade",
        "view_branch",
    },
    "Enfermeiro(a)": {
        "view_paciente",
        "view_agendamento",
        "change_agendamento",
        "view_consulta",
        "view_medico",
        "view_horariotrabalho",
    },
    "Farmacêutico / Stock": {
        "view_medicamento",
        "add_medicamento",
        "change_medicamento",
        "view_hospital",
        "view_branch",
    },
    "Financeiro / Caixa": {
        "view_paciente",
        "view_agendamento",
        "view_consulta",
        "view_hospital",
    },
    "Auditor / Direcção": {
        "view_user",
        "view_group",
        "view_permission",
        "view_branch",
        "view_systempreference",
        "view_hospital",
        "view_especialidade",
        "view_medico",
        "view_paciente",
        "view_agendamento",
        "view_consulta",
        "view_horariotrabalho",
        "view_medicamento",
        "view_departamento",
    },
}


def visible_users_queryset():
    return User.objects.exclude(is_superuser=True)


def is_system_permission(permission: Permission) -> bool:
    model_name = permission.content_type.model
    system_codenames = {f"{action}_{model_name}" for action in SYSTEM_PERMISSION_ACTIONS}
    return permission.codename in system_codenames


def describe_permission_scope(permission: Permission) -> str:
    return f"{permission.content_type.app_label}.{permission.content_type.model}"


def get_permission_action(permission: Permission) -> str | None:
    model_name = permission.content_type.model
    for action in STANDARD_PERMISSION_LABELS:
        if permission.codename == f"{action}_{model_name}":
            return action
    return None


def get_permission_group_label(permission: Permission) -> str:
    model_class = permission.content_type.model_class()
    if model_class is not None:
        return model_class._meta.verbose_name_plural.title()
    return permission.content_type.model.replace("_", " ").title()


def build_permission_matrix(selected_permission_ids=None):
    selected_permission_ids = {int(value) for value in (selected_permission_ids or [])}
    permissions = Permission.objects.select_related("content_type").order_by(
        "content_type__app_label",
        "content_type__model",
        "codename",
    )

    section_map = OrderedDict()

    for permission in permissions:
        app_label = permission.content_type.app_label
        section_meta = APP_SECTION_METADATA.get(
            app_label,
            {
                "title": app_label.replace("_", " ").title(),
                "description": tr(
                    "Permissões agrupadas automaticamente por módulo.",
                    "Permissions grouped automatically by module.",
                ),
                "order": 999,
            },
        )
        section = section_map.setdefault(
            app_label,
            {
                "app_label": app_label,
                "title": section_meta["title"],
                "description": section_meta["description"],
                "order": section_meta["order"],
                "models": OrderedDict(),
            },
        )

        model_key = permission.content_type.model
        model_group = section["models"].setdefault(
            model_key,
            {
                "key": model_key,
                "label": get_permission_group_label(permission),
                "scope": describe_permission_scope(permission),
                "actions": OrderedDict((action, None) for action in STANDARD_PERMISSION_LABELS),
                "extras": [],
            },
        )

        action = get_permission_action(permission)
        entry = {
            "id": permission.id,
            "name": permission.name,
            "codename": permission.codename,
            "checked": permission.id in selected_permission_ids,
        }

        if action:
            model_group["actions"][action] = entry
        else:
            model_group["extras"].append(entry)

    ordered_sections = sorted(section_map.values(), key=lambda item: (item["order"], item["title"]))
    for section in ordered_sections:
        section_models = []
        for model in section["models"].values():
            model["action_items"] = [
                {
                    "key": action,
                    "label": label,
                    "permission": model["actions"][action],
                }
                for action, label in STANDARD_PERMISSION_LABELS.items()
            ]
            section_models.append(model)
        section["models"] = section_models

    return ordered_sections


def sync_default_roles():
    permissions_by_codename = {
        permission.codename: permission
        for permission in Permission.objects.select_related("content_type").all()
    }

    for role_name, codenames in DEFAULT_ROLE_PERMISSION_MAP.items():
        group, created = Group.objects.get_or_create(name=role_name)

        matched_permissions = [
            permissions_by_codename[codename]
            for codename in sorted(codenames)
            if codename in permissions_by_codename
        ]
        if created:
            group.permissions.set(matched_permissions)
            continue

        existing_ids = set(group.permissions.values_list("id", flat=True))
        missing_permissions = [
            permission for permission in matched_permissions if permission.id not in existing_ids
        ]
        if missing_permissions:
            group.permissions.add(*missing_permissions)
