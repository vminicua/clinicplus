from django.contrib.auth.models import Permission


SYSTEM_PERMISSION_ACTIONS = ("add", "change", "delete", "view")


def is_system_permission(permission: Permission) -> bool:
    model_name = permission.content_type.model
    system_codenames = {f"{action}_{model_name}" for action in SYSTEM_PERMISSION_ACTIONS}
    return permission.codename in system_codenames


def describe_permission_scope(permission: Permission) -> str:
    return f"{permission.content_type.app_label}.{permission.content_type.model}"

