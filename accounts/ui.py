from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from .models import SystemPreference


LANGUAGE_SESSION_KEY = "clinic_language"


def get_supported_languages() -> set[str]:
    return {code for code, _label in settings.LANGUAGES}


def normalize_language(language_code: str | None) -> str:
    if language_code in get_supported_languages():
        return language_code
    return settings.LANGUAGE_CODE


def get_system_preferences():
    try:
        return SystemPreference.get_solo()
    except (OperationalError, ProgrammingError):
        return None


def get_system_default_language() -> str:
    preferences = get_system_preferences()
    if preferences:
        return normalize_language(preferences.default_language)
    return settings.LANGUAGE_CODE


def resolve_language_for_request(request) -> str:
    session_language = None
    if hasattr(request, "session"):
        session_language = request.session.get(LANGUAGE_SESSION_KEY)

    if getattr(request, "user", None) and request.user.is_authenticated:
        profile = getattr(request.user, "profile", None)
        if profile and profile.preferred_language:
            return normalize_language(profile.preferred_language)

    if session_language:
        return normalize_language(session_language)

    return get_system_default_language()


def ui_text(request, portuguese: str, english: str) -> str:
    language_code = normalize_language(getattr(request, "LANGUAGE_CODE", None))
    if language_code == "en":
        return english
    return portuguese
