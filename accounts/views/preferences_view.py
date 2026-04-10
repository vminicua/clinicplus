from django.contrib import messages
from django.db.models import Count
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from accounts.forms import MeasurementUnitForm, PaymentMethodForm, SystemPreferenceForm
from accounts.models import MeasurementUnit, PaymentMethod, SystemPreference, UserProfile
from accounts.ui import (
    BRANCH_SESSION_KEY,
    LANGUAGE_SESSION_KEY,
    available_branches_for_user,
    normalize_language,
    ui_text,
)
from clinic.models import Consumivel, Medicamento

from .base_view import AppPermissionMixin, ClinicPageMixin, ModalFormMixin


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
            {
                "id": "pref-taxes",
                "title": ui_text(self.request, "Facturação", "Billing"),
                "description": ui_text(
                    self.request,
                    "Configure a taxa de IVA usada por defeito nos recibos e vendas da farmácia.",
                    "Configure the default VAT rate used in pharmacy receipts and sales.",
                ),
                "fields": [form["vat_rate"]],
            },
            {
                "id": "pref-patients",
                "title": ui_text(self.request, "Pacientes", "Patients"),
                "description": ui_text(
                    self.request,
                    "Controle o prefixo do código visível usado na ficha e nas listagens dos pacientes.",
                    "Control the visible code prefix used in patient records and listings.",
                ),
                "fields": [form["patient_code_prefix"]],
            },
        ]
        context["submit_label"] = ui_text(self.request, "Guardar preferências", "Save preferences")
        context["measurement_units_url"] = reverse("accounts:measurement_unit_list")
        context["payment_methods_url"] = reverse("accounts:payment_method_list")
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


class MeasurementUnitListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = MeasurementUnit
    template_name = "accounts/preferences/units/list.html"
    context_object_name = "units"
    permission_required = "accounts.view_measurementunit"
    segment = "preference_units"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Unidades", "Units")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Catálogo de unidades usadas no inventário, compras e formulários clínicos.",
            "Catalog of units used across inventory, purchasing, and clinical forms.",
        )

    def get_queryset(self):
        return MeasurementUnit.objects.order_by("sort_order", "name", "code")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        medication_usage = dict(
            Medicamento.objects.values("unidade_medida")
            .annotate(total=Count("id"))
            .values_list("unidade_medida", "total")
        )
        consumable_usage = dict(
            Consumivel.objects.values("unidade_medida")
            .annotate(total=Count("id"))
            .values_list("unidade_medida", "total")
        )
        units = list(context["units"])
        for unit in units:
            unit.linked_medications = medication_usage.get(unit.code, 0)
            unit.linked_consumables = consumable_usage.get(unit.code, 0)
            unit.total_usage = unit.linked_medications + unit.linked_consumables

        context["units"] = units
        context["total_units"] = len(units)
        context["active_units"] = sum(1 for unit in units if unit.is_active)
        context["units_in_use"] = sum(1 for unit in units if unit.total_usage > 0)
        context["custom_units"] = sum(1 for unit in units if unit.sort_order >= 100)
        return context


class MeasurementUnitCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = MeasurementUnit
    form_class = MeasurementUnitForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:measurement_unit_list")
    permission_required = "accounts.add_measurementunit"
    segment = "preference_units"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Nova unidade", "New unit")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe uma nova unidade de medida para o inventário e catálogo clínico.",
            "Register a new measurement unit for inventory and clinical catalog usage.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar unidade", "Create unit")
        context["form_description"] = ui_text(
            self.request,
            "Depois de criada, esta unidade passa a aparecer nos formulários de medicamentos e consumíveis.",
            "Once created, this unit will appear in medication and consumable forms.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar unidade", "Save unit")
        context["cancel_url"] = reverse("accounts:measurement_unit_list")
        context["wide_fields"] = {"description"}
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Unidade criada com sucesso.", "Unit created successfully.")


class MeasurementUnitUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = MeasurementUnit.objects.all()
    form_class = MeasurementUnitForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:measurement_unit_list")
    permission_required = "accounts.change_measurementunit"
    segment = "preference_units"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar unidade", "Edit unit")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize nome, abreviatura e disponibilidade desta unidade no sistema.",
            "Update the name, abbreviation, and availability of this unit in the system.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar unidade", "Edit unit")
        context["form_description"] = ui_text(
            self.request,
            "As alterações reflectem-se imediatamente nos formulários que usam esta unidade.",
            "Changes are immediately reflected in forms that use this unit.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar unidade", "Update unit")
        context["cancel_url"] = reverse("accounts:measurement_unit_list")
        context["wide_fields"] = {"description"}
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Unidade actualizada com sucesso.", "Unit updated successfully.")


class PaymentMethodListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = PaymentMethod
    template_name = "accounts/preferences/payment_methods/list.html"
    context_object_name = "payment_methods"
    permission_required = "accounts.view_paymentmethod"
    segment = "preference_payment_methods"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Métodos de pagamento", "Payment methods")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Catálogo de métodos aceites no caixa, farmácia e futuras cobranças do sistema.",
            "Catalog of methods accepted at checkout, pharmacy, and future billing flows.",
        )

    def get_queryset(self):
        return PaymentMethod.objects.order_by("sort_order", "name", "code")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        payment_methods = list(context["payment_methods"])
        context["payment_methods"] = payment_methods
        context["total_methods"] = len(payment_methods)
        context["active_methods"] = sum(1 for method in payment_methods if method.is_active)
        context["mobile_wallet_methods"] = sum(
            1 for method in payment_methods if method.category == PaymentMethod.CategoryChoices.MOBILE_MONEY
        )
        context["banking_methods"] = sum(
            1
            for method in payment_methods
            if method.category in {PaymentMethod.CategoryChoices.CARD, PaymentMethod.CategoryChoices.BANK_TRANSFER}
        )
        return context


class PaymentMethodCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = PaymentMethod
    form_class = PaymentMethodForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:payment_method_list")
    permission_required = "accounts.add_paymentmethod"
    segment = "preference_payment_methods"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo método de pagamento", "New payment method")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe um método que poderá ser usado na farmácia e em futuras cobranças.",
            "Register a method that can be used in pharmacy sales and future billing.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar método de pagamento", "Create payment method")
        context["form_description"] = ui_text(
            self.request,
            "Métodos activos aparecem nos formulários de venda e nos recibos emitidos.",
            "Active methods appear in sales forms and issued receipts.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar método", "Save method")
        context["cancel_url"] = reverse("accounts:payment_method_list")
        context["wide_fields"] = {"description"}
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Método de pagamento criado com sucesso.", "Payment method created successfully.")


class PaymentMethodUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = PaymentMethod.objects.all()
    form_class = PaymentMethodForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("accounts:payment_method_list")
    permission_required = "accounts.change_paymentmethod"
    segment = "preference_payment_methods"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar método de pagamento", "Edit payment method")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize nome, categoria e disponibilidade operacional deste método.",
            "Update the name, category, and operational availability of this method.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar método de pagamento", "Edit payment method")
        context["form_description"] = ui_text(
            self.request,
            "As alterações reflectem-se imediatamente no caixa da farmácia e nos recibos seguintes.",
            "Changes are immediately reflected in the pharmacy checkout and future receipts.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar método", "Update method")
        context["cancel_url"] = reverse("accounts:payment_method_list")
        context["wide_fields"] = {"description"}
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Método de pagamento actualizado com sucesso.", "Payment method updated successfully.")


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


class BranchSwitchView(View):
    def post(self, request):
        if not request.user.is_authenticated:
            return redirect("clinic:login")

        branch_id = request.POST.get("branch_id")
        branch = None
        for available_branch in available_branches_for_user(request.user):
            if str(available_branch.pk) == str(branch_id):
                branch = available_branch
                break

        if branch is None:
            messages.error(
                request,
                ui_text(
                    request,
                    "Não foi possível seleccionar esta sucursal.",
                    "We could not select this branch.",
                ),
            )
            return redirect(request.POST.get("next") or reverse_lazy("clinic:index"))

        request.session[BRANCH_SESSION_KEY] = branch.pk
        if not request.user.is_superuser:
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            profile.default_branch = branch
            profile.save(update_fields=["default_branch", "updated_at"])

        messages.success(
            request,
            ui_text(
                request,
                "Sucursal activa alterada para %(branch)s.",
                "Active branch changed to %(branch)s.",
            )
            % {"branch": branch.name},
        )
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse_lazy("clinic:index")
        return redirect(next_url)
