from django.conf import settings
from django.utils.functional import lazy
from django.utils.translation import get_language, gettext


def get_supported_languages() -> set[str]:
    return {code for code, _label in settings.LANGUAGES}


def normalize_language(language_code: str | None) -> str:
    if language_code in get_supported_languages():
        return language_code
    return settings.LANGUAGE_CODE


def translate_catalog(portuguese: str, english: str | None = None) -> str:
    translated = gettext(portuguese)
    language_code = normalize_language(get_language())

    if english and language_code == "en" and translated == portuguese:
        return english

    return translated


translate_pair = lazy(translate_catalog, str)
