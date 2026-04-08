from django import template

from accounts.utils import describe_permission_scope, is_system_permission


register = template.Library()


@register.filter
def permission_scope(permission):
    return describe_permission_scope(permission)


@register.filter
def permission_kind(permission):
    return "Sistema" if is_system_permission(permission) else "Personalizada"


@register.filter
def permission_is_system(permission):
    return is_system_permission(permission)

