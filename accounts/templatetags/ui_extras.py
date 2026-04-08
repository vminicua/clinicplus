from django import template

from accounts.ui import ui_text


register = template.Library()


@register.simple_tag(takes_context=True)
def ui(context, portuguese: str, english: str | None = None) -> str:
    request = context.get("request")
    if request is None:
        return portuguese
    if english is None:
        english = portuguese
    return ui_text(request, portuguese, english)
