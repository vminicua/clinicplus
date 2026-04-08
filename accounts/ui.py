from django.conf import settings
from django.db.utils import OperationalError, ProgrammingError

from .models import Branch, SystemPreference
from .i18n import normalize_language, translate_catalog


LANGUAGE_SESSION_KEY = "clinic_language"
BRANCH_SESSION_KEY = "clinic_branch_id"


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
    return translate_catalog(portuguese, english)


def available_branches_for_user(user):
    if not getattr(user, "is_authenticated", False):
        return Branch.objects.none()

    queryset = Branch.objects.filter(is_active=True).order_by("name")
    if user.is_superuser:
        return queryset

    profile = getattr(user, "profile", None)
    if profile is None:
        return Branch.objects.none()
    return queryset.filter(user_profiles=profile).distinct()


def resolve_branch_for_request(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return None

    available_branches = list(available_branches_for_user(request.user))
    if not available_branches:
        return None

    selected_branch_id = None
    if hasattr(request, "session"):
        selected_branch_id = request.session.get(BRANCH_SESSION_KEY)

    for branch in available_branches:
        if branch.id == selected_branch_id:
            return branch

    profile = getattr(request.user, "profile", None)
    if profile and profile.default_branch_id:
        for branch in available_branches:
            if branch.id == profile.default_branch_id:
                if hasattr(request, "session"):
                    request.session[BRANCH_SESSION_KEY] = branch.id
                return branch

    branch = available_branches[0]
    if hasattr(request, "session"):
        request.session[BRANCH_SESSION_KEY] = branch.id
    return branch


def branch_label(branch) -> str:
    if branch is None:
        return ""
    return f"{branch.name} ({branch.code})"


def filter_users_for_branch(queryset, request):
    branch = getattr(request, "clinic_current_branch", None)
    if branch is None:
        return queryset
    return queryset.filter(profile__assigned_branches=branch).distinct()
