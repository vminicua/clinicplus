from django.conf import settings
from django.utils.functional import lazy
from django.utils.translation import get_language


def get_supported_languages() -> set[str]:
    return {code for code, _label in settings.LANGUAGES}


def normalize_language(language_code: str | None) -> str:
    if language_code in get_supported_languages():
        return language_code
    return settings.LANGUAGE_CODE


def ui_text_active(portuguese: str, english: str) -> str:
    language_code = normalize_language(get_language())
    if language_code == "en":
        return english
    return portuguese


translate_pair = lazy(ui_text_active, str)
