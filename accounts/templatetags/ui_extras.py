from django import template

from accounts.ui import ui_text


register = template.Library()


@register.simple_tag(takes_context=True)
def ui(context, portuguese: str, english: str) -> str:
    request = context.get("request")
    if request is None:
        return portuguese
    return ui_text(request, portuguese, english)
