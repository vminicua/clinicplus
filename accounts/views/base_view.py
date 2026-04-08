from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone

from accounts.ui import get_system_preferences


def is_modal_request(request) -> bool:
    return (
        request.GET.get("modal") == "1"
        or request.POST.get("modal") == "1"
        or request.headers.get("x-requested-with") == "XMLHttpRequest"
    )


class AppPermissionMixin(LoginRequiredMixin, PermissionRequiredMixin):
    login_url = "clinic:login"
    raise_exception = False

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.error(
                self.request,
                "Não tem permissões suficientes para aceder a esta área de gestão.",
            )
            return redirect("clinic:index")
        return super().handle_no_permission()


class ClinicPageMixin(LoginRequiredMixin):
    login_url = "clinic:login"
    page_title = ""
    page_subtitle = ""
    segment = ""

    def get_page_title(self) -> str:
        return self.page_title

    def get_page_subtitle(self) -> str:
        return self.page_subtitle

    def get_meta_title(self) -> str:
        page_title = self.get_page_title()
        if not page_title:
            return "Clinic Plus"
        return f"Clinic Plus | {page_title}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        greeting_name = (
            self.request.user.get_short_name()
            or self.request.user.first_name
            or self.request.user.username
        )

        context.setdefault("segment", self.segment)
        context.setdefault("page_title", self.get_page_title())
        context.setdefault("page_subtitle", self.get_page_subtitle())
        context.setdefault("meta_title", self.get_meta_title())
        context.setdefault("current_date", timezone.localdate())
        context.setdefault("greeting_name", greeting_name)
        context.setdefault("current_language", getattr(self.request, "LANGUAGE_CODE", "pt"))
        context.setdefault("system_preferences", get_system_preferences())
        context.setdefault("is_modal_request", is_modal_request(self.request))
        return context


class ModalResponseMixin:
    modal_template_name = ""
    success_message = ""
    modal_size = "modal-lg"

    def is_modal(self) -> bool:
        return is_modal_request(self.request)

    def get_template_names(self):
        if self.is_modal() and self.modal_template_name:
            return [self.modal_template_name]
        return [self.template_name]

    def get_success_message(self) -> str:
        return self.success_message

    def get_modal_title(self) -> str:
        return self.page_title

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("modal_title", self.get_modal_title())
        context.setdefault("modal_size", self.modal_size)
        return context


class ModalFormMixin(ModalResponseMixin):
    error_message = "Revise os campos destacados e tente novamente."

    def form_valid(self, form):
        self.object = form.save()
        if self.is_modal():
            return JsonResponse(
                {
                    "success": True,
                    "message": self.get_success_message(),
                    "reload": True,
                }
            )

        messages.success(self.request, self.get_success_message())
        return redirect(self.get_success_url())

    def form_invalid(self, form):
        if self.is_modal():
            return self.render_to_response(self.get_context_data(form=form), status=422)

        messages.error(self.request, self.error_message)
        return self.render_to_response(self.get_context_data(form=form), status=422)


class ModalDetailMixin(ModalResponseMixin):
    modal_template_name = "accounts/shared/modal_detail.html"
