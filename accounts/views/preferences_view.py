from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import UpdateView

from accounts.forms import SystemPreferenceForm
from accounts.models import SystemPreference, UserProfile
from accounts.ui import LANGUAGE_SESSION_KEY, normalize_language, ui_text

from .base_view import AppPermissionMixin, ClinicPageMixin


class SystemPreferenceView(AppPermissionMixin, ClinicPageMixin, UpdateView):
    form_class = SystemPreferenceForm
    template_name = "accounts/preferences/system.html"
    success_url = reverse_lazy("accounts:system_preferences")
    permission_required = ("accounts.view_systempreference", "accounts.change_systempreference")
    segment = "preferences"

    def get_object(self, queryset=None):
        return SystemPreference.get_solo()

    def get_page_title(self) -> str:
        return ui_text(self.request, "Preferências", "Preferences")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Centralize definições globais do sistema em grupos recolhidos por defeito.",
            "Centralize global system settings in groups collapsed by default.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context["form"]
        context["preference_groups"] = [
            {
                "id": "pref-language",
                "title": ui_text(self.request, "Idioma", "Language"),
                "description": ui_text(
                    self.request,
                    "Define o idioma base do sistema para novos acessos.",
                    "Defines the system default language for new sessions.",
                ),
                "fields": [form["default_language"]],
            },
            {
                "id": "pref-currency",
                "title": ui_text(self.request, "Moeda", "Currency"),
                "description": ui_text(
                    self.request,
                    "Escolha a moeda principal usada pelo sistema.",
                    "Choose the primary currency used across the system.",
                ),
                "fields": [form["default_currency"]],
            },
        ]
        context["submit_label"] = ui_text(self.request, "Guardar preferências", "Save preferences")
        return context

    def form_valid(self, form):
        form.save()
        messages.success(
            self.request,
            ui_text(
                self.request,
                "Preferências do sistema guardadas com sucesso.",
                "System preferences saved successfully.",
            ),
        )
        return redirect(self.get_success_url())


class LanguageSwitchView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return redirect("clinic:login")

        language_code = normalize_language(request.POST.get("language"))
        request.session[LANGUAGE_SESSION_KEY] = language_code

        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.preferred_language = language_code
        profile.save(update_fields=["preferred_language", "updated_at"])

        if language_code == "en":
            messages.success(request, "Language updated successfully.")
        else:
            messages.success(request, "Idioma actualizado com sucesso.")

        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse_lazy("clinic:index")
        return redirect(next_url)
