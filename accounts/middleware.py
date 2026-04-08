from django.utils import translation

from .ui import resolve_branch_for_request, resolve_language_for_request


class ClinicLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.clinic_current_branch = resolve_branch_for_request(request)
        language_code = resolve_language_for_request(request)
        translation.activate(language_code)
        request.LANGUAGE_CODE = language_code

        response = self.get_response(request)
        translation.deactivate()
        response.headers.setdefault("Content-Language", language_code)
        return response
