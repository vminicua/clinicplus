import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, time, timedelta
from io import BytesIO
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Count, Exists, F, Max, OuterRef, Prefetch, Q, Sum
from django.db.models.functions import Coalesce
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, TemplateView, UpdateView

from accounts.models import Branch, Clinic
from accounts.ui import (
    BRANCH_SESSION_KEY,
    LANGUAGE_SESSION_KEY,
    available_branches_for_user,
    cached_available_branches_for_request,
    get_system_preferences,
    get_system_default_language,
    ui_text,
)
from accounts.views.base_view import AppPermissionMixin, ClinicPageMixin, ModalDetailMixin, ModalFormMixin
from clinic.forms import (
    AppointmentForm,
    ConsumableForm,
    ConsumableStockForm,
    ConsultationForm,
    InventoryMovementForm,
    MedicationForm,
    MedicationStockForm,
    PharmacyCartItemForm,
    PharmacyCheckoutForm,
    DepartmentForm,
    PatientForm,
    SpecialtyForm,
    WarehouseForm,
    WorkScheduleBatchCreateForm,
    WorkScheduleForm,
)
from clinic.models import (
    Agendamento,
    Armazem,
    Consulta,
    Consumivel,
    Departamento,
    Especialidade,
    EstoqueConsumivel,
    EstoqueMedicamento,
    HorarioTrabalho,
    Medicamento,
    Medico,
    MovimentoInventario,
    Paciente,
    PharmacySale,
    PharmacySaleItem,
)


logger = logging.getLogger(__name__)
PHARMACY_CART_SESSION_KEY = "pharmacy_sale_cart"
MONEY_QUANTIZER = Decimal("0.01")


def get_patient_code_prefix() -> str:
    preferences = get_system_preferences()
    if preferences and preferences.patient_code_prefix:
        return preferences.patient_code_prefix
    return "PCCP000"


def format_patient_code(patient_id: int, prefix: str | None = None) -> str:
    return f"{prefix or get_patient_code_prefix()}{patient_id}"


def attach_patient_codes(patients, prefix: str | None = None):
    patient_prefix = prefix or get_patient_code_prefix()
    for patient in patients:
        patient.display_code = format_patient_code(patient.pk, patient_prefix)
    return patients


def patient_queryset():
    return Paciente.objects.select_related("user", "hospital", "branch").annotate(
        total_appointments=Count("agendamentos", distinct=True),
        total_consultations=Count("agendamentos__consulta", distinct=True),
        last_appointment=Max("agendamentos__data"),
        last_consultation_at=Max("agendamentos__consulta__data_consulta"),
    )


def patient_history_queryset():
    history_entries = Prefetch(
        "agendamentos",
        queryset=Agendamento.objects.select_related(
            "medico__user",
            "medico__especialidade",
            "medico__departamento",
            "medico__departamento__branch",
            "branch",
            "consulta",
            "hospital",
        ).order_by("-data", "-hora"),
        to_attr="history_entries",
    )
    return patient_queryset().prefetch_related(history_entries)


def work_schedule_queryset():
    today = timezone.localdate()
    return (
        HorarioTrabalho.objects.select_related(
            "user",
            "branch",
            "user__medico__especialidade",
            "user__medico__departamento",
            "user__medico__departamento__branch",
            "user__medico__hospital",
        )
        .annotate(
            appointments_today=Count(
                "user__medico__agendamentos",
                filter=Q(user__medico__agendamentos__data=today),
                distinct=True,
            ),
            future_appointments=Count(
                "user__medico__agendamentos",
                filter=Q(user__medico__agendamentos__data__gte=today),
                distinct=True,
            ),
            last_appointment_date=Max("user__medico__agendamentos__data"),
        )
        .order_by("weekday", "start_time", "user__first_name", "user__last_name", "user__username")
    )


def appointment_queryset():
    return (
        Agendamento.objects.select_related(
            "paciente__user",
            "paciente__branch",
            "medico__user",
            "medico__especialidade",
            "medico__departamento",
            "medico__departamento__branch",
            "branch",
            "consulta",
            "hospital",
        )
        .annotate(
            has_consultation=Exists(
                Consulta.objects.filter(agendamento=OuterRef("pk"))
            )
        )
        .order_by("data", "hora", "paciente__user__first_name", "paciente__user__last_name")
    )


def appointment_professional_schedule_queryset():
    return work_schedule_queryset().filter(is_active=True, accepts_appointments=True)


def structure_filter_branches_for_request(request):
    scoped_queryset = available_branches_for_user(request.user)
    if scoped_queryset.exists():
        return list(scoped_queryset)
    return list(Branch.objects.filter(is_active=True).order_by("name"))


def default_structure_branch_id(request, filter_branches):
    current_branch = getattr(request, "clinic_current_branch", None)
    if current_branch and any(branch.pk == current_branch.pk for branch in filter_branches):
        return current_branch.pk
    return filter_branches[0].pk if filter_branches else None


def inventory_filter_branches_for_request(request):
    if request is not None:
        cached_branches = getattr(request, "_inventory_filter_branches_cache", None)
        if cached_branches is not None:
            return cached_branches

    if request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        scoped_branches = cached_available_branches_for_request(request)
        if scoped_branches:
            if request is not None:
                request._inventory_filter_branches_cache = scoped_branches
            return scoped_branches

    branches = list(Branch.objects.filter(is_active=True).order_by("name"))
    if request is not None:
        request._inventory_filter_branches_cache = branches
    return branches


def inventory_visible_warehouses_for_request(request):
    if request is not None:
        cached_queryset = getattr(request, "_inventory_visible_warehouses_cache", None)
        if cached_queryset is not None:
            return cached_queryset

    queryset = (
        Armazem.objects.select_related("branch")
        .filter(branch__in=inventory_filter_branches_for_request(request))
        .order_by("branch__name", "name")
    )
    if request is not None:
        request._inventory_visible_warehouses_cache = queryset
    return queryset


def medication_stock_queryset(request=None):
    return (
        EstoqueMedicamento.objects.select_related("armazem__branch", "medicamento")
        .filter(armazem__in=inventory_visible_warehouses_for_request(request))
        .order_by("medicamento__name", "armazem__branch__name", "armazem__name")
    )


def consumable_stock_queryset(request=None):
    return (
        EstoqueConsumivel.objects.select_related("armazem__branch", "consumivel")
        .filter(armazem__in=inventory_visible_warehouses_for_request(request))
        .order_by("consumivel__name", "armazem__branch__name", "armazem__name")
    )


def medication_catalog_queryset(request=None):
    visible_warehouses = inventory_visible_warehouses_for_request(request)
    scoped_stock = EstoqueMedicamento.objects.select_related("armazem__branch").filter(
        armazem__in=visible_warehouses
    )
    return (
        Medicamento.objects.prefetch_related(
            Prefetch(
                "estoques",
                queryset=scoped_stock.order_by("armazem__branch__name", "armazem__name"),
                to_attr="inventory_stock_entries",
            )
        )
        .annotate(
            visible_stock_lines=Count(
                "estoques",
                filter=Q(estoques__armazem__in=visible_warehouses),
                distinct=True,
            ),
            visible_branch_count=Count(
                "estoques__armazem__branch",
                filter=Q(estoques__armazem__in=visible_warehouses),
                distinct=True,
            ),
            visible_low_stock_lines=Count(
                "estoques",
                filter=Q(
                    estoques__armazem__in=visible_warehouses,
                    estoques__stock_minimo__gt=0,
                    estoques__quantidade__lte=F("estoques__stock_minimo"),
                ),
                distinct=True,
            ),
            visible_total_stock=Coalesce(
                Sum("estoques__quantidade", filter=Q(estoques__armazem__in=visible_warehouses)),
                0,
            ),
        )
        .order_by("name", "dosagem")
    )


def consumable_catalog_queryset(request=None):
    visible_warehouses = inventory_visible_warehouses_for_request(request)
    scoped_stock = EstoqueConsumivel.objects.select_related("armazem__branch").filter(
        armazem__in=visible_warehouses
    )
    return (
        Consumivel.objects.prefetch_related(
            Prefetch(
                "estoques",
                queryset=scoped_stock.order_by("armazem__branch__name", "armazem__name"),
                to_attr="inventory_stock_entries",
            )
        )
        .annotate(
            visible_stock_lines=Count(
                "estoques",
                filter=Q(estoques__armazem__in=visible_warehouses),
                distinct=True,
            ),
            visible_branch_count=Count(
                "estoques__armazem__branch",
                filter=Q(estoques__armazem__in=visible_warehouses),
                distinct=True,
            ),
            visible_low_stock_lines=Count(
                "estoques",
                filter=Q(
                    estoques__armazem__in=visible_warehouses,
                    estoques__stock_minimo__gt=0,
                    estoques__quantidade__lte=F("estoques__stock_minimo"),
                ),
                distinct=True,
            ),
            visible_total_stock=Coalesce(
                Sum("estoques__quantidade", filter=Q(estoques__armazem__in=visible_warehouses)),
                0,
            ),
        )
        .order_by("name")
    )


def inventory_movement_queryset(request=None):
    return (
        MovimentoInventario.objects.select_related(
            "armazem__branch",
            "medicamento",
            "consumivel",
            "created_by",
        )
        .filter(armazem__in=inventory_visible_warehouses_for_request(request))
        .order_by("-created_at", "-id")
    )


class AnyPermissionRequiredMixin(AppPermissionMixin):
    permission_options = ()

    def has_permission(self):
        if self.permission_options:
            return self.request.user.is_authenticated and any(
                self.request.user.has_perm(codename) for codename in self.permission_options
            )
        return super().has_permission()


class InventoryFormRequestMixin:
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs


def inventory_branch_filter_context(request):
    filter_branches = inventory_filter_branches_for_request(request)
    return {
        "filter_branches": filter_branches,
        "active_branch_filter_id": default_structure_branch_id(request, filter_branches),
    }


def quantize_money(value):
    return Decimal(value or 0).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def is_ajax_request(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def local_day_bounds(target_date):
    start = timezone.make_aware(datetime.combine(target_date, time.min))
    end = start + timedelta(days=1)
    return start, end


def scope_queryset_to_branch(queryset, branch, lookup="branch"):
    if branch is None:
        return queryset
    return queryset.filter(**{lookup: branch})


def dashboard_schedule_capacity_slots(schedule):
    if not schedule.accepts_appointments or schedule.slot_minutes <= 0:
        return 0

    reference_date = timezone.localdate()
    start_at = datetime.combine(reference_date, schedule.start_time)
    end_at = datetime.combine(reference_date, schedule.end_time)
    duration_minutes = max(int((end_at - start_at).total_seconds() // 60), 0)

    if schedule.break_start and schedule.break_end:
        break_start_at = datetime.combine(reference_date, schedule.break_start)
        break_end_at = datetime.combine(reference_date, schedule.break_end)
        duration_minutes -= max(int((break_end_at - break_start_at).total_seconds() // 60), 0)

    return max(duration_minutes // schedule.slot_minutes, 0)


def dashboard_event_time_label(timestamp, today=None):
    if timestamp is None:
        return ""

    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp)

    localized_timestamp = timezone.localtime(timestamp)
    reference_date = today or timezone.localdate()
    if localized_timestamp.date() == reference_date:
        return localized_timestamp.strftime("%H:%M")
    return localized_timestamp.strftime("%d/%m %H:%M")


def dashboard_appointment_status_meta(request, status):
    status_map = {
        "agendado": {
            "label": ui_text(request, "Agendado", "Scheduled"),
            "tone": "info",
            "icon": "event_available",
        },
        "concluido": {
            "label": ui_text(request, "Concluído", "Completed"),
            "tone": "success",
            "icon": "task_alt",
        },
        "cancelado": {
            "label": ui_text(request, "Cancelado", "Cancelled"),
            "tone": "danger",
            "icon": "event_busy",
        },
        "nao_compareceu": {
            "label": ui_text(request, "Não compareceu", "No-show"),
            "tone": "warning",
            "icon": "person_off",
        },
    }
    return status_map.get(
        status,
        {
            "label": status.replace("_", " ").title(),
            "tone": "secondary",
            "icon": "event_note",
        },
    )


def pharmacy_vat_rate():
    preferences = get_system_preferences()
    if preferences is None:
        return Decimal("16.00")
    return quantize_money(preferences.vat_rate)


def empty_pharmacy_cart():
    return {
        "warehouse_id": None,
        "items": [],
    }


def get_pharmacy_cart(request):
    raw_cart = request.session.get(PHARMACY_CART_SESSION_KEY) if hasattr(request, "session") else None
    if not isinstance(raw_cart, dict):
        return empty_pharmacy_cart()

    normalized_items = []
    for item in raw_cart.get("items", []):
        try:
            item_id = int(item.get("item_id"))
            quantity = int(item.get("quantity"))
        except (TypeError, ValueError):
            continue
        item_type = item.get("item_type")
        if item_type not in {
            PharmacySaleItem.ItemTypeChoices.MEDICAMENTO,
            PharmacySaleItem.ItemTypeChoices.CONSUMIVEL,
        }:
            continue
        if quantity <= 0:
            continue
        normalized_items.append(
            {
                "item_type": item_type,
                "item_id": item_id,
                "quantity": quantity,
                "item_name": item.get("item_name", ""),
                "sku": item.get("sku", ""),
                "unit_label": item.get("unit_label", ""),
                "unit_price": item.get("unit_price", "0.00"),
            }
        )

    warehouse_id = raw_cart.get("warehouse_id")
    try:
        warehouse_id = int(warehouse_id) if warehouse_id else None
    except (TypeError, ValueError):
        warehouse_id = None

    return {
        "warehouse_id": warehouse_id,
        "items": normalized_items,
    }


def save_pharmacy_cart(request, cart):
    if hasattr(request, "session"):
        request.session[PHARMACY_CART_SESSION_KEY] = cart
        request.session.modified = True


def clear_pharmacy_cart(request):
    if hasattr(request, "session"):
        request.session.pop(PHARMACY_CART_SESSION_KEY, None)
        request.session.modified = True


def pharmacy_sale_queryset(request=None):
    return (
        PharmacySale.objects.select_related(
            "branch",
            "warehouse",
            "payment_method",
            "patient__user",
            "sold_by",
            "reversed_by",
        )
        .prefetch_related("items")
        .filter(branch__in=inventory_filter_branches_for_request(request))
        .order_by("-sold_at", "-id")
    )


def reverse_pharmacy_sale(*, sale, performed_by, reversal_status, request, reason=""):
    if sale.status != PharmacySale.StatusChoices.COMPLETED:
        raise ValidationError(
            ui_text(
                request,
                "Só vendas concluídas podem ser canceladas ou devolvidas.",
                "Only completed sales can be cancelled or returned.",
            )
        )

    with transaction.atomic():
        locked_sale = (
            PharmacySale.objects.select_for_update()
            .select_related("warehouse", "branch", "payment_method", "patient__user", "sold_by")
            .prefetch_related("items")
            .get(pk=sale.pk)
        )
        if locked_sale.status != PharmacySale.StatusChoices.COMPLETED:
            raise ValidationError(
                ui_text(
                    request,
                    "Esta venda já foi alterada por outro utilizador.",
                    "This sale was already changed by another user.",
                )
            )

        reversal_suffix = "CANCEL" if reversal_status == PharmacySale.StatusChoices.CANCELLED else "RETURN"
        reversal_note_label = (
            ui_text(request, "Cancelamento da venda", "Sale cancellation")
            if reversal_status == PharmacySale.StatusChoices.CANCELLED
            else ui_text(request, "Devolução da venda", "Sale return")
        )

        for item in locked_sale.items.select_related("medicamento", "consumivel"):
            if item.item_type == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO:
                stock_entry, _created = EstoqueMedicamento.objects.select_for_update().get_or_create(
                    armazem=locked_sale.warehouse,
                    medicamento=item.medicamento,
                )
            else:
                stock_entry, _created = EstoqueConsumivel.objects.select_for_update().get_or_create(
                    armazem=locked_sale.warehouse,
                    consumivel=item.consumivel,
                )

            stock_before = stock_entry.quantidade
            stock_after = stock_before + item.quantity
            stock_entry.quantidade = stock_after
            stock_entry.last_counted_at = timezone.localdate()
            stock_entry.save(update_fields=["quantidade", "last_counted_at", "updated_at"])

            movement_kwargs = {
                "armazem": locked_sale.warehouse,
                "item_type": item.item_type,
                "movement_type": MovimentoInventario.MovementTypeChoices.ENTRADA,
                "quantity": item.quantity,
                "stock_before": stock_before,
                "stock_after": stock_after,
                "unit_cost": item.unit_price,
                "reference": f"{locked_sale.sale_number}-{reversal_suffix}",
                "notes": ui_text(
                    request,
                    f"{reversal_note_label} para {locked_sale.customer_display_name}",
                    f"{reversal_note_label} for {locked_sale.customer_display_name}",
                ),
                "created_by": performed_by,
            }
            if item.item_type == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO:
                movement_kwargs["medicamento"] = item.medicamento
            else:
                movement_kwargs["consumivel"] = item.consumivel
            MovimentoInventario.objects.create(**movement_kwargs)

        locked_sale.status = reversal_status
        locked_sale.reversed_by = performed_by
        locked_sale.reversed_at = timezone.now()
        locked_sale.reversal_reason = reason
        locked_sale.save(update_fields=["status", "reversed_by", "reversed_at", "reversal_reason", "updated_at"])
        return locked_sale


def resolve_pharmacy_cart(request):
    cart = get_pharmacy_cart(request)
    vat_rate = pharmacy_vat_rate()
    visible_warehouses = list(inventory_visible_warehouses_for_request(request))
    warehouse_map = {warehouse.pk: warehouse for warehouse in visible_warehouses}
    warehouse = warehouse_map.get(cart.get("warehouse_id")) if cart.get("warehouse_id") else None
    resolved_items = []
    subtotal = Decimal("0.00")
    tax_amount = Decimal("0.00")
    total_amount = Decimal("0.00")

    for raw_item in cart.get("items", []):
        item_type = raw_item["item_type"]
        item_id = raw_item["item_id"]
        quantity = raw_item["quantity"]
        item = None
        item_name = raw_item.get("item_name") or (
            ui_text(request, "Medicamento removido", "Deleted medication")
            if item_type == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO
            else ui_text(request, "Consumível removido", "Deleted consumable")
        )
        unit_label = raw_item.get("unit_label") or "un"
        unit_price = quantize_money(raw_item.get("unit_price") or 0)
        item_sku = raw_item.get("sku") or ""

        line_total = quantize_money(unit_price * quantity)
        if vat_rate > 0:
            line_tax_amount = quantize_money(line_total * vat_rate / (Decimal("100") + vat_rate))
        else:
            line_tax_amount = Decimal("0.00")
        line_subtotal = quantize_money(line_total - line_tax_amount)

        subtotal += line_subtotal
        tax_amount += line_tax_amount
        total_amount += line_total

        resolved_items.append(
            {
                "key": f"{item_type}:{item_id}",
                "item_type": item_type,
                "item_id": item_id,
                "item": item,
                "item_name": item_name,
                "sku": item_sku,
                "unit_label": unit_label,
                "quantity": quantity,
                "unit_price": unit_price,
                "line_subtotal": line_subtotal,
                "line_tax_amount": line_tax_amount,
                "line_total": line_total,
                "available_quantity": None,
                "has_stock_issue": False,
            }
        )

    return {
        "cart": cart,
        "cart_warehouse": warehouse,
        "cart_items": resolved_items,
        "cart_item_count": sum(item["quantity"] for item in resolved_items),
        "cart_subtotal": quantize_money(subtotal),
        "cart_tax_rate": vat_rate,
        "cart_tax_amount": quantize_money(tax_amount),
        "cart_total_amount": quantize_money(total_amount),
        "cart_has_stock_issue": False,
    }


def build_pharmacy_selector_payload(request, cart):
    visible_warehouses = list(inventory_visible_warehouses_for_request(request))
    reserved_quantities = {}
    for line in cart.get("items", []):
        key = f"{line['item_type']}:{line['item_id']}"
        reserved_quantities[key] = reserved_quantities.get(key, 0) + int(line.get("quantity", 0) or 0)

    payload = {
        "currency": get_system_preferences().default_currency or "MZN",
        "labels": {
            "selectWarehouse": ui_text(
                request,
                "Seleccione primeiro o armazém para orientar a escolha do produto.",
                "Select the warehouse first to guide the product choice.",
            ),
            "selectProduct": ui_text(
                request,
                "Seleccione o produto para ver preço e stock disponível.",
                "Select a product to see its price and available stock.",
            ),
            "outOfStock": ui_text(
                request,
                "Sem stock disponível neste armazém.",
                "No stock available in this warehouse.",
            ),
            "available": ui_text(request, "Disponível", "Available"),
            "reserved": ui_text(request, "Já no carrinho", "Already in cart"),
            "canAdd": ui_text(request, "Pode adicionar até", "You can add up to"),
            "unitPrice": ui_text(request, "Preço unitário", "Unit price"),
        },
        "items": {
            PharmacySaleItem.ItemTypeChoices.MEDICAMENTO: {},
            PharmacySaleItem.ItemTypeChoices.CONSUMIVEL: {},
        },
    }

    medication_stock = (
        EstoqueMedicamento.objects.select_related("medicamento", "armazem")
        .filter(armazem__in=visible_warehouses, medicamento__is_active=True)
        .order_by("medicamento__name", "armazem__name")
    )
    for stock_entry in medication_stock:
        item = stock_entry.medicamento
        item_payload = payload["items"][PharmacySaleItem.ItemTypeChoices.MEDICAMENTO].setdefault(
            str(item.pk),
            {
                "label": f"{item.display_name} · {item.dosagem}" if item.dosagem else item.display_name,
                "unit_label": item.unidade_medida or "un",
                "unit_price": f"{quantize_money(item.preco):.2f}",
                "warehouses": {},
            },
        )
        reserved = reserved_quantities.get(f"{PharmacySaleItem.ItemTypeChoices.MEDICAMENTO}:{item.pk}", 0)
        item_payload["warehouses"][str(stock_entry.armazem_id)] = {
            "available": int(stock_entry.quantidade),
            "reserved": int(reserved),
            "remaining": max(int(stock_entry.quantidade) - int(reserved), 0),
        }

    consumable_stock = (
        EstoqueConsumivel.objects.select_related("consumivel", "armazem")
        .filter(armazem__in=visible_warehouses, consumivel__is_active=True)
        .order_by("consumivel__name", "armazem__name")
    )
    for stock_entry in consumable_stock:
        item = stock_entry.consumivel
        item_payload = payload["items"][PharmacySaleItem.ItemTypeChoices.CONSUMIVEL].setdefault(
            str(item.pk),
            {
                "label": item.display_name,
                "unit_label": item.unidade_medida or "un",
                "unit_price": f"{quantize_money(item.preco_referencia):.2f}",
                "warehouses": {},
            },
        )
        reserved = reserved_quantities.get(f"{PharmacySaleItem.ItemTypeChoices.CONSUMIVEL}:{item.pk}", 0)
        item_payload["warehouses"][str(stock_entry.armazem_id)] = {
            "available": int(stock_entry.quantidade),
            "reserved": int(reserved),
            "remaining": max(int(stock_entry.quantidade) - int(reserved), 0),
        }

    return payload


def pharmacy_item_info_payload(request, *, warehouse, item_type, item_id):
    cart = get_pharmacy_cart(request)
    reserved_quantity = sum(
        int(line.get("quantity", 0) or 0)
        for line in cart.get("items", [])
        if line.get("item_type") == item_type and str(line.get("item_id")) == str(item_id)
    )

    if item_type == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO:
        item = get_object_or_404(Medicamento.objects.filter(is_active=True), pk=item_id)
        stock_entry = get_object_or_404(EstoqueMedicamento, armazem=warehouse, medicamento=item)
        item_label = f"{item.display_name} · {item.dosagem}" if item.dosagem else item.display_name
        unit_price = quantize_money(item.preco)
        unit_label = item.unidade_medida or "un"
    else:
        item = get_object_or_404(Consumivel.objects.filter(is_active=True), pk=item_id)
        stock_entry = get_object_or_404(EstoqueConsumivel, armazem=warehouse, consumivel=item)
        item_label = item.display_name
        unit_price = quantize_money(item.preco_referencia)
        unit_label = item.unidade_medida or "un"

    available_quantity = int(stock_entry.quantidade)
    remaining_quantity = max(available_quantity - reserved_quantity, 0)
    preferences = get_system_preferences()
    return {
        "item_label": item_label,
        "unit_label": unit_label,
        "unit_price": f"{unit_price:.2f}",
        "available_quantity": available_quantity,
        "reserved_quantity": reserved_quantity,
        "remaining_quantity": remaining_quantity,
        "currency": (preferences.default_currency if preferences else "MZN") or "MZN",
    }


def build_appointment_professionals(schedule_list, reference_date):
    registry = {}
    for schedule in schedule_list:
        entry = registry.setdefault(
            schedule.user_id,
            {
                "user_id": schedule.user_id,
                "professional_name": schedule.professional_name,
                "username": schedule.user.username,
                "role_labels": [],
                "branch_names": [],
                "schedule_blocks": 0,
                "linked_medico": schedule.linked_medico is not None,
                "doctor_badge": "",
                "appointments_today": schedule.appointments_today or 0,
                "future_appointments": schedule.future_appointments or 0,
                "is_on_duty_today": False,
            },
        )
        role_label = schedule.get_role_display()
        if role_label not in entry["role_labels"]:
            entry["role_labels"].append(role_label)
        if schedule.branch.name not in entry["branch_names"]:
            entry["branch_names"].append(schedule.branch.name)
        entry["schedule_blocks"] += 1
        entry["is_on_duty_today"] = entry["is_on_duty_today"] or schedule.applies_to_date(reference_date)
        entry["appointments_today"] = max(entry["appointments_today"], schedule.appointments_today or 0)
        entry["future_appointments"] = max(entry["future_appointments"], schedule.future_appointments or 0)

        if schedule.linked_medico is not None and not entry["doctor_badge"]:
            badge_parts = []
            if schedule.linked_medico.especialidade_id:
                badge_parts.append(schedule.linked_medico.especialidade.name)
            if schedule.linked_medico.departamento_id:
                badge_parts.append(schedule.linked_medico.departamento.name)
            if schedule.linked_medico.crm:
                badge_parts.append(schedule.linked_medico.crm)
            entry["doctor_badge"] = " · ".join(part for part in badge_parts if part)

    return sorted(registry.values(), key=lambda item: item["professional_name"].lower())


def serialize_work_schedule(schedule):
    linked_medico = schedule.linked_medico
    next_shift_date = schedule.next_occurrence_date()
    return {
        "id": schedule.pk,
        "professional_name": schedule.professional_name,
        "user_id": schedule.user_id,
        "username": schedule.user.username,
        "email": schedule.user.email,
        "role": schedule.role,
        "role_label": schedule.get_role_display(),
        "weekday": schedule.weekday,
        "weekday_label": schedule.get_weekday_display(),
        "shift_name": schedule.display_shift_name,
        "start_time": schedule.start_time.strftime("%H:%M"),
        "end_time": schedule.end_time.strftime("%H:%M"),
        "break_start": schedule.break_start.strftime("%H:%M") if schedule.break_start else "",
        "break_end": schedule.break_end.strftime("%H:%M") if schedule.break_end else "",
        "break_label": schedule.break_label,
        "slot_minutes": schedule.slot_minutes,
        "valid_from": schedule.valid_from.isoformat(),
        "valid_until": schedule.valid_until.isoformat() if schedule.valid_until else None,
        "accepts_appointments": schedule.accepts_appointments,
        "is_active": schedule.is_active,
        "notes": schedule.display_notes,
        "branch_id": schedule.branch_id,
        "branch_name": schedule.branch.name,
        "appointments_today": schedule.appointments_today or 0,
        "future_appointments": schedule.future_appointments or 0,
        "linked_doctor": linked_medico is not None,
        "doctor_badge": (
            " · ".join(
                part
                for part in [
                    linked_medico.especialidade.name if linked_medico and linked_medico.especialidade_id else "",
                    linked_medico.departamento.name if linked_medico and linked_medico.departamento_id else "",
                    linked_medico.crm if linked_medico else "",
                ]
                if part
            )
            if linked_medico is not None
            else ""
        ),
        "next_occurrence_date": next_shift_date.isoformat() if next_shift_date else None,
        "detail_url": reverse("clinic:work_schedule_detail", args=[schedule.pk]),
        "edit_url": reverse("clinic:work_schedule_update", args=[schedule.pk]),
        "toggle_url": reverse("clinic:work_schedule_toggle_status", args=[schedule.pk]),
    }


def custom_login(request):
    """Tela de login personalizada para a aplicação Clinic"""
    if request.user.is_authenticated:
        return redirect('clinic:index')

    next_url = request.POST.get('next') or request.GET.get('next') or ''
    context = {
        'next_url': next_url,
    }

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''

        try:
            user = authenticate(request, username=username, password=password)

            if user is not None:
                login(request, user)
        except DatabaseError:
            logger.exception(
                "Falha de banco/tunel durante o login do usuario '%s'.",
                username or '<vazio>',
            )
            messages.error(
                request,
                ui_text(
                    request,
                    'Nao foi possivel conectar ao banco de dados. Verifique o tunel SSH/MySQL e tente novamente.',
                    'The database connection is unavailable. Check the SSH/MySQL tunnel and try again.',
                ),
            )
            return render(request, 'clinic/login.html', context, status=503)

        if user is not None:
            profile = getattr(user, "profile", None)
            request.session[LANGUAGE_SESSION_KEY] = (
                profile.preferred_language if profile and profile.preferred_language else get_system_default_language()
            )
            available_branches = list(available_branches_for_user(user))
            if available_branches:
                if profile and profile.default_branch_id:
                    selected_branch = next(
                        (branch for branch in available_branches if branch.id == profile.default_branch_id),
                        available_branches[0],
                    )
                else:
                    selected_branch = available_branches[0]
                request.session[BRANCH_SESSION_KEY] = selected_branch.id
            messages.success(
                request,
                ui_text(
                    request,
                    'Bem-vindo, %(user)s!',
                    'Welcome, %(user)s!',
                )
                % {"user": user.get_full_name() or user.username},
            )
            return redirect(next_url or 'clinic:index')
        else:
            messages.error(
                request,
                ui_text(request, 'Credenciais inválidas. Tente novamente.', 'Invalid credentials. Please try again.'),
            )

    return render(request, 'clinic/login.html', context)


def custom_logout(request):
    """Logout personalizado"""
    logout(request)
    messages.info(
        request,
        ui_text(request, 'Sessão terminada com sucesso.', 'You have been signed out successfully.'),
    )
    return redirect('clinic:login')


@login_required(login_url='clinic:login')
def dashboard(request):
    greeting_name = request.user.get_short_name() or request.user.first_name or request.user.username
    current_branch = getattr(request, "clinic_current_branch", None)
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    month_start = today.replace(day=1)
    today_start, today_end = local_day_bounds(today)
    month_start_dt, _month_end_dt = local_day_bounds(month_start)
    visible_branches = cached_available_branches_for_request(request)
    preferences = get_system_preferences()
    currency = (preferences.default_currency if preferences else "MZN") or "MZN"

    patients_qs = scope_queryset_to_branch(patient_queryset(), current_branch)
    appointments_qs = scope_queryset_to_branch(appointment_queryset(), current_branch)
    consultations_qs = scope_queryset_to_branch(
        Consulta.objects.select_related(
            "agendamento__paciente__user",
            "agendamento__medico__user",
            "agendamento__medico__especialidade",
            "agendamento__medico__departamento",
        ),
        current_branch,
        lookup="agendamento__branch",
    )
    doctor_schedules_qs = scope_queryset_to_branch(
        work_schedule_queryset().filter(role=HorarioTrabalho.RoleChoices.MEDICO),
        current_branch,
    )
    sales_qs = pharmacy_sale_queryset(request)
    if current_branch is not None:
        sales_qs = sales_qs.filter(branch=current_branch)

    active_patients = patients_qs.filter(is_active=True)
    total_patients = active_patients.count()
    new_patients_this_month = patients_qs.filter(created_at__date__gte=month_start).count()

    appointments_today_qs = appointments_qs.filter(data=today)
    appointments_this_month_qs = appointments_qs.filter(data__year=today.year, data__month=today.month)
    completed_appointments_month = appointments_this_month_qs.filter(status="concluido").count()
    appointments_today = appointments_today_qs.count()
    appointments_this_month = appointments_this_month_qs.count()
    pending_appointments = appointments_qs.filter(status="agendado", data__gte=today).count()

    consultations_today_qs = consultations_qs.filter(data_consulta__gte=today_start, data_consulta__lt=today_end)
    consultations_month_qs = consultations_qs.filter(data_consulta__gte=month_start_dt, data_consulta__lt=today_end)
    completed_consultations_today = consultations_today_qs.count()
    completed_consultations_month = consultations_month_qs.count()

    completed_rate = (
        round((completed_appointments_month / appointments_this_month) * 100)
        if appointments_this_month
        else 0
    )
    consultation_capture_rate = (
        round((completed_consultations_month / completed_appointments_month) * 100)
        if completed_appointments_month
        else 0
    )

    today_doctor_schedules = list(
        doctor_schedules_qs.filter(
            is_active=True,
            accepts_appointments=True,
            weekday=today.weekday(),
            valid_from__lte=today,
        )
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
        .order_by("start_time", "user__first_name", "user__last_name", "user__username")
    )
    doctors_on_duty_today = len({schedule.user_id for schedule in today_doctor_schedules})
    today_capacity_slots = sum(dashboard_schedule_capacity_slots(schedule) for schedule in today_doctor_schedules)
    daily_capacity = round((appointments_today / today_capacity_slots) * 100) if today_capacity_slots else 0

    total_doctors = len(
        (
            set(appointments_qs.values_list("medico_id", flat=True))
            | set(doctor_schedules_qs.values_list("user__medico__id", flat=True))
        )
        - {None}
    )

    completed_sales_today = list(
        sales_qs.filter(
            status=PharmacySale.StatusChoices.COMPLETED,
            sold_at__gte=today_start,
            sold_at__lt=today_end,
        )
    )
    completed_sales_month = list(
        sales_qs.filter(
            status=PharmacySale.StatusChoices.COMPLETED,
            sold_at__gte=month_start_dt,
            sold_at__lt=today_end,
        )
    )
    revenue_today = quantize_money(sum(sale.total_amount for sale in completed_sales_today))
    revenue_month = quantize_money(sum(sale.total_amount for sale in completed_sales_month))
    sales_this_month = len(completed_sales_month)

    future_appointments = list(appointments_qs.filter(data__gte=today).order_by("data", "hora")[:5])
    if len(future_appointments) < 5:
        fallback_appointments = list(
            appointments_qs.filter(data__lt=today).order_by("-data", "-hora")[: 5 - len(future_appointments)]
        )
        recent_appointment_objects = future_appointments + fallback_appointments
    else:
        recent_appointment_objects = future_appointments

    recent_appointments = []
    for appointment in recent_appointment_objects:
        status_meta = dashboard_appointment_status_meta(request, appointment.status)
        specialty = (
            appointment.medico.especialidade.display_name
            if appointment.medico.especialidade_id
            else (
                appointment.medico.departamento.display_name
                if appointment.medico.departamento_id
                else ui_text(request, "Clínica geral", "General clinic")
            )
        )
        recent_appointments.append(
            {
                "patient": appointment.paciente.full_name,
                "doctor": appointment.medico.user.get_full_name() or appointment.medico.user.username,
                "specialty": specialty,
                "time": f"{appointment.data:%d/%m} · {appointment.hora:%H:%M}"
                if appointment.data != today
                else f"{appointment.hora:%H:%M}",
                "status": status_meta["label"],
                "tone": status_meta["tone"],
            }
        )

    weekly_top_doctors = (
        appointments_qs.filter(data__range=(week_start, week_end))
        .order_by()
        .values(
            "medico_id",
            "medico__user__first_name",
            "medico__user__last_name",
            "medico__user__username",
            "medico__especialidade__name",
            "medico__departamento__name",
        )
        .annotate(
            appointments=Count("id"),
            completed=Count("id", filter=Q(status="concluido")),
            consultations=Count("consulta", distinct=True),
        )
        .order_by("-appointments", "-completed", "medico__user__first_name", "medico__user__last_name")[:5]
    )
    top_doctors = []
    for item in weekly_top_doctors:
        doctor_name = " ".join(
            part
            for part in [item["medico__user__first_name"], item["medico__user__last_name"]]
            if part
        ) or item["medico__user__username"]
        specialty_name = (
            item["medico__especialidade__name"]
            or item["medico__departamento__name"]
            or ui_text(request, "Sem especialidade", "No specialty")
        )
        top_doctors.append(
            {
                "name": doctor_name,
                "specialty": specialty_name,
                "appointments": item["appointments"],
                "completed": item["completed"],
                "consultations": item["consultations"],
            }
        )

    timeline_entries = []
    timeline_window_start = today - timedelta(days=6)
    timeline_window_start_dt, _timeline_window_end = local_day_bounds(timeline_window_start)

    recent_consultations = consultations_qs.filter(data_consulta__gte=timeline_window_start_dt).order_by("-data_consulta")[:4]
    for consultation in recent_consultations:
        timeline_entries.append(
            {
                "title": ui_text(
                    request,
                    "Consulta registada para %(patient)s",
                    "Consultation recorded for %(patient)s",
                )
                % {"patient": consultation.agendamento.paciente.full_name},
                "time": dashboard_event_time_label(consultation.data_consulta, today=today),
                "icon": "stethoscope",
                "tone": "success",
                "sort_at": consultation.data_consulta,
            }
        )

    recent_sales = sales_qs.filter(sold_at__gte=timeline_window_start_dt).order_by("-sold_at")[:4]
    for sale in recent_sales:
        sale_event_timestamp = sale.sold_at if sale.status == PharmacySale.StatusChoices.COMPLETED else (sale.reversed_at or sale.sold_at)
        title = (
            ui_text(
                request,
                "Venda %(sale)s concluída para %(customer)s",
                "Sale %(sale)s completed for %(customer)s",
            )
            if sale.status == PharmacySale.StatusChoices.COMPLETED
            else ui_text(
                request,
                "Venda %(sale)s revertida para %(customer)s",
                "Sale %(sale)s reversed for %(customer)s",
            )
        )
        timeline_entries.append(
            {
                "title": title
                % {
                    "sale": sale.sale_number or f"#{sale.pk}",
                    "customer": sale.customer_display_name,
                },
                "time": dashboard_event_time_label(sale_event_timestamp, today=today),
                "icon": "payments",
                "tone": "primary" if sale.status == PharmacySale.StatusChoices.COMPLETED else "warning",
                "sort_at": sale_event_timestamp,
            }
        )

    recent_timeline_appointments = appointments_qs.filter(data__gte=timeline_window_start).order_by("-data", "-hora")[:4]
    for appointment in recent_timeline_appointments:
        appointment_moment = timezone.make_aware(datetime.combine(appointment.data, appointment.hora))
        status_meta = dashboard_appointment_status_meta(request, appointment.status)
        timeline_entries.append(
            {
                "title": ui_text(
                    request,
                    "%(status)s de %(patient)s com %(doctor)s",
                    "%(status)s for %(patient)s with %(doctor)s",
                )
                % {
                    "status": status_meta["label"],
                    "patient": appointment.paciente.full_name,
                    "doctor": appointment.medico.user.get_full_name() or appointment.medico.user.username,
                },
                "time": dashboard_event_time_label(appointment_moment, today=today),
                "icon": status_meta["icon"],
                "tone": status_meta["tone"],
                "sort_at": appointment_moment,
            }
        )

    timeline_events = [
        {
            "title": item["title"],
            "time": item["time"],
            "icon": item["icon"],
            "tone": item["tone"],
        }
        for item in sorted(timeline_entries, key=lambda entry: entry["sort_at"], reverse=True)[:6]
    ]

    weekly_chart_labels = []
    weekly_chart_appointments = []
    weekly_chart_revenue = []
    for offset in range(7):
        target_date = week_start + timedelta(days=offset)
        period_start, period_end = local_day_bounds(target_date)
        weekly_chart_labels.append(target_date.strftime("%d/%m"))
        weekly_chart_appointments.append(appointments_qs.filter(data=target_date).count())
        weekly_chart_revenue.append(
            float(
                quantize_money(
                    sum(
                        sale.total_amount
                        for sale in sales_qs.filter(
                            status=PharmacySale.StatusChoices.COMPLETED,
                            sold_at__gte=period_start,
                            sold_at__lt=period_end,
                        )
                    )
                )
            )
        )

    if pending_appointments:
        shift_focus_text = ui_text(
            request,
            "%(count)s marcações agendadas ainda aguardam atendimento ou confirmação operacional.",
            "%(count)s scheduled bookings are still waiting for operational handling or follow-up.",
        ) % {"count": pending_appointments}
    elif daily_capacity >= 85:
        shift_focus_text = ui_text(
            request,
            "A capacidade do dia está elevada; vale acompanhar encaixes e atrasos.",
            "Today's capacity is running high; keep an eye on walk-ins and delays.",
        )
    else:
        shift_focus_text = ui_text(
            request,
            "Operação estável, com agenda e faturação a evoluírem dentro do esperado.",
            "Operations are stable, with scheduling and billing moving as expected.",
        )

    context = {
        'segment': 'dashboard',
        'meta_title': ui_text(request, 'Clinic Plus | Painel de Operacoes', 'Clinic Plus | Operations dashboard'),
        'page_title': ui_text(request, 'Painel de Operacoes', 'Operations dashboard'),
        'page_subtitle': ui_text(
            request,
            'Uma visao mais limpa da agenda, do atendimento e da saude financeira da clinica.',
            'A cleaner overview of scheduling, patient flow, and clinic financial health.',
        ),
        'branch_scope_label': (
            (
                ui_text(
                    request,
                    'Sucursal activa: %(branch)s',
                    'Active branch: %(branch)s',
                )
                % {"branch": current_branch.name}
            )
            if current_branch else ""
        ),
        'current_date': today,
        'greeting_name': greeting_name,
        'current_branch': current_branch,
        'currency': currency,
        'total_clinics': Clinic.objects.filter(is_active=True).count(),
        'visible_branches_count': len(visible_branches) if visible_branches else Branch.objects.filter(is_active=True).count(),
        'total_doctors': total_doctors,
        'doctors_on_duty_today': doctors_on_duty_today,
        'total_patients': total_patients,
        'new_patients_this_month': new_patients_this_month,
        'appointments_this_month': appointments_this_month,
        'completed_appointments_month': completed_appointments_month,
        'completed_consultations_month': completed_consultations_month,
        'completed_consultations_today': completed_consultations_today,
        'appointments_today': appointments_today,
        'revenue_today': revenue_today,
        'revenue_month': revenue_month,
        'completed_rate': completed_rate,
        'consultation_capture_rate': consultation_capture_rate,
        'daily_capacity': daily_capacity,
        'today_capacity_slots': today_capacity_slots,
        'pending_appointments': pending_appointments,
        'sales_this_month': sales_this_month,
        'service_mix': [
            {'label': ui_text(request, 'Agenda concluída no mês', 'Monthly schedule completed'), 'value': completed_rate, 'tone': 'success'},
            {'label': ui_text(request, 'Capacidade ocupada hoje', 'Capacity occupied today'), 'value': daily_capacity, 'tone': 'info'},
            {'label': ui_text(request, 'Consultas registadas no mês', 'Monthly recorded consultations'), 'value': consultation_capture_rate, 'tone': 'warning'},
        ],
        'shift_focus_text': shift_focus_text,
        'timeline_events': timeline_events,
        'top_doctors': top_doctors,
        'recent_appointments': recent_appointments,
        'weekly_chart_labels': weekly_chart_labels,
        'weekly_chart_appointments': weekly_chart_appointments,
        'weekly_chart_revenue': weekly_chart_revenue,
    }

    return render(request, 'clinic/index.html', context)


class PatientListView(AppPermissionMixin, ClinicPageMixin, ListView):
    template_name = "clinic/patients/list.html"
    context_object_name = "patients"
    permission_required = "clinic.view_paciente"
    segment = "patients"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Pacientes", "Patients")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Cadastro central de pacientes com acesso rápido à ficha, edição e histórico clínico.",
            "Central patient registry with quick access to records, editing, and clinical history.",
        )

    def get_queryset(self):
        return patient_queryset().order_by("user__first_name", "user__last_name", "cpf")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = patient_queryset()
        month_start = timezone.localdate().replace(day=1)
        patient_code_prefix = get_patient_code_prefix()
        attach_patient_codes(context["patients"], patient_code_prefix)
        context["total_patients"] = base_queryset.count()
        context["active_patients"] = base_queryset.filter(is_active=True).count()
        context["inactive_patients"] = base_queryset.filter(is_active=False).count()
        context["patients_with_history"] = base_queryset.filter(agendamentos__isnull=False).distinct().count()
        context["new_patients_this_month"] = base_queryset.filter(created_at__date__gte=month_start).count()
        return context


class PatientDetailView(AppPermissionMixin, ModalDetailMixin, ClinicPageMixin, DetailView):
    template_name = "clinic/patients/detail.html"
    permission_required = "clinic.view_paciente"
    segment = "patients"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Detalhes do paciente", "Patient details")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Resumo completo da ficha, contactos e sinais clínicos registados.",
            "Complete overview of the record, contacts, and registered clinical notes.",
        )

    def get_queryset(self):
        return patient_history_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        history_entries = list(getattr(self.object, "history_entries", []))
        latest_consultation = next(
            (entry for entry in history_entries if getattr(entry, "consulta", None)),
            None,
        )
        context["patient_code"] = format_patient_code(self.object.pk)
        context["detail_partial"] = "clinic/patients/includes/detail_content.html"
        context["modal_heading"] = self.object.full_name
        context["modal_description"] = ui_text(
            self.request,
            "Dados pessoais, emergência, alergias e resumo do histórico mais recente.",
            "Personal data, emergency information, allergies, and a summary of the most recent history.",
        )
        context["recent_history_entries"] = history_entries[:4]
        context["latest_consultation"] = latest_consultation
        return context


class PatientCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Paciente
    form_class = PatientForm
    template_name = "clinic/patients/form.html"
    modal_template_name = "clinic/patients/modal_form.html"
    success_url = reverse_lazy("clinic:patient_list")
    permission_required = "clinic.add_paciente"
    segment = "patients"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo paciente", "New patient")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe dados pessoais, contacto e informação clínica inicial do paciente.",
            "Register personal data, contact details, and the patient's initial clinical information.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar paciente", "Create patient")
        context["form_description"] = ui_text(
            self.request,
            "Preencha a ficha base do paciente para começar a acompanhar atendimentos e histórico.",
            "Fill in the patient's base record to start tracking visits and history.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar paciente", "Save patient")
        context["cancel_url"] = reverse("clinic:patient_list")
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_success_message(self) -> str:
        return ui_text(self.request, "Paciente criado com sucesso.", "Patient created successfully.")


class PatientUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    form_class = PatientForm
    template_name = "clinic/patients/form.html"
    modal_template_name = "clinic/patients/modal_form.html"
    success_url = reverse_lazy("clinic:patient_list")
    permission_required = "clinic.change_paciente"
    segment = "patients"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar paciente", "Edit patient")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize os dados cadastrais e o resumo clínico do paciente seleccionado.",
            "Update the selected patient's registration data and clinical summary.",
        )

    def get_queryset(self):
        return patient_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar paciente", "Edit patient")
        context["form_description"] = ui_text(
            self.request,
            "Revise contactos, documentos e observações clínicas sempre que houver mudanças.",
            "Review contacts, documents, and clinical notes whenever there are changes.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar paciente", "Update patient")
        context["cancel_url"] = reverse("clinic:patient_detail", args=[self.object.pk])
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_success_message(self) -> str:
        return ui_text(self.request, "Paciente actualizado com sucesso.", "Patient updated successfully.")


class PatientToggleStatusView(AppPermissionMixin, View):
    permission_required = "clinic.change_paciente"
    login_url = "clinic:login"

    @transaction.atomic
    def post(self, request, pk):
        patient = get_object_or_404(patient_queryset(), pk=pk)
        patient.is_active = not patient.is_active
        patient.save(update_fields=["is_active", "updated_at"])
        if patient.user.is_active != patient.is_active:
            patient.user.is_active = patient.is_active
            patient.user.save(update_fields=["is_active"])

        return JsonResponse(
            {
                "success": True,
                "message": ui_text(
                    request,
                    "Paciente %(patient)s %(status)s com sucesso.",
                    "Patient %(patient)s %(status)s successfully.",
                )
                % {
                    "patient": patient.full_name,
                    "status": ui_text(
                        request,
                        "activado" if patient.is_active else "desactivado",
                        "activated" if patient.is_active else "deactivated",
                    ),
                },
                "redirect_url": reverse("clinic:patient_list"),
            }
        )


class PatientPdfDownloadView(AppPermissionMixin, View):
    permission_required = "clinic.view_paciente"
    login_url = "clinic:login"

    def get(self, request, pk):
        patient = get_object_or_404(patient_history_queryset(), pk=pk)
        history_entries = list(getattr(patient, "history_entries", []))
        latest_consultation = next(
            (entry for entry in history_entries if getattr(entry, "consulta", None)),
            None,
        )

        html = render_to_string(
            "clinic/patients/record_pdf.html",
            {
                "patient": patient,
                "patient_code": format_patient_code(patient.pk),
                "history_entries": history_entries[:8],
                "latest_consultation": latest_consultation,
                "generated_at": timezone.localtime(),
                "request": request,
            },
            request=request,
        )

        from weasyprint import HTML

        pdf_bytes = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
        filename = f"ficha-paciente-{format_patient_code(patient.pk).lower()}.pdf"
        return FileResponse(
            BytesIO(pdf_bytes),
            as_attachment=True,
            filename=filename,
            content_type="application/pdf",
        )


class PatientHistoryListView(AppPermissionMixin, ClinicPageMixin, ListView):
    template_name = "clinic/patients/history_list.html"
    context_object_name = "patients"
    permission_required = ("clinic.view_paciente", "clinic.view_consulta")
    segment = "patient_history"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Histórico de pacientes", "Patient history")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Escolha um paciente para abrir toda a linha do tempo de consultas, notas e prescrições.",
            "Choose a patient to open the full timeline of visits, notes, and prescriptions.",
        )

    def get_queryset(self):
        return (
            patient_queryset()
            .filter(agendamentos__isnull=False)
            .distinct()
            .order_by("-last_consultation_at", "-last_appointment", "user__first_name", "user__last_name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = patient_queryset()
        attach_patient_codes(context["patients"])
        context["patients_with_history"] = self.object_list.count()
        context["patients_with_consultations"] = base_queryset.filter(agendamentos__consulta__isnull=False).distinct().count()
        context["total_consultations"] = Consulta.objects.count()
        context["appointments_today"] = Agendamento.objects.filter(data=timezone.localdate()).count()
        return context


class PatientHistoryDetailView(AppPermissionMixin, ClinicPageMixin, DetailView):
    template_name = "clinic/patients/history_detail.html"
    permission_required = ("clinic.view_paciente", "clinic.view_consulta")
    segment = "patient_history"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Histórico do paciente", "Patient history")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Linha do tempo clínica completa com consultas, prescrições e notas associadas.",
            "Complete clinical timeline with visits, prescriptions, and associated notes.",
        )

    def get_queryset(self):
        return patient_history_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        history_entries = list(getattr(self.object, "history_entries", []))
        consultation_entries = [entry for entry in history_entries if getattr(entry, "consulta", None)]
        latest_consultation = consultation_entries[0] if consultation_entries else None
        context["patient_code"] = format_patient_code(self.object.pk)
        context["history_entries"] = history_entries
        context["consultation_entries"] = consultation_entries
        context["latest_consultation"] = latest_consultation
        context["completed_appointments"] = sum(1 for entry in history_entries if entry.status == "concluido")
        context["cancel_url"] = reverse("clinic:patient_history_list")
        return context


class AppointmentListView(AppPermissionMixin, ClinicPageMixin, ListView):
    template_name = "clinic/appointments/list.html"
    context_object_name = "appointments"
    permission_required = "clinic.view_agendamento"
    segment = "appointments"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Marcações", "Bookings")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Acompanhe as marcações previstas, concluídas e com consulta associada num único painel.",
            "Track scheduled, completed, and consultation-linked bookings from a single workspace.",
        )

    def get_queryset(self):
        return appointment_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        base_queryset = appointment_queryset()
        context["total_appointments"] = base_queryset.count()
        context["appointments_today"] = base_queryset.filter(data=today).count()
        context["appointments_this_week"] = base_queryset.filter(data__range=(week_start, week_end)).count()
        context["completed_appointments"] = base_queryset.filter(status="concluido").count()
        context["appointments_with_consultation"] = base_queryset.filter(has_consultation=True).count()
        context["pending_appointments"] = base_queryset.filter(status="agendado", data__gte=today).count()
        context["workspace_heading"] = ui_text(self.request, "Painel de marcações", "Bookings workspace")
        context["workspace_description"] = ui_text(
            self.request,
            "Veja rapidamente paciente, profissional, horário e se já existe consulta registada para cada marcação.",
            "Quickly review the patient, professional, time slot, and whether a visit has already been recorded for each booking.",
        )
        context["primary_action_url"] = reverse("clinic:appointment_agenda")
        context["primary_action_label"] = ui_text(self.request, "Abrir agenda", "Open agenda")
        context["show_primary_action"] = True
        return context


class AppointmentCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Agendamento
    form_class = AppointmentForm
    template_name = "clinic/appointments/form.html"
    modal_template_name = "clinic/appointments/modal_form.html"
    success_url = reverse_lazy("clinic:appointment_list")
    permission_required = "clinic.add_agendamento"
    segment = "appointments"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Nova marcação", "New booking")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe uma nova marcação ligando paciente, profissional clínico, sucursal, data e hora.",
            "Register a new booking linking patient, clinical professional, branch, date, and time.",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar marcação", "Create booking")
        context["form_description"] = ui_text(
            self.request,
            "Escolha o paciente, o profissional clínico, a sucursal e a janela exacta da marcação.",
            "Choose the patient, clinical professional, branch, and the exact booking slot.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar marcação", "Save booking")
        context["cancel_url"] = reverse("clinic:appointment_list")
        context["wide_fields"] = ("motivo", "notas")
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Marcação criada com sucesso.", "Booking created successfully.")


class AppointmentConsultationView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Consulta
    form_class = ConsultationForm
    template_name = "clinic/consultations/form.html"
    modal_template_name = "clinic/consultations/modal_form.html"
    success_url = reverse_lazy("clinic:appointment_list")
    segment = "appointments"

    def dispatch(self, request, *args, **kwargs):
        self.appointment = get_object_or_404(appointment_queryset(), pk=kwargs["appointment_pk"])
        self.existing_consultation = getattr(self.appointment, "consulta", None)

        if (
            self.existing_consultation is None
            and self.appointment.status in {"cancelado", "nao_compareceu"}
        ):
            messages.error(
                request,
                ui_text(
                    request,
                    "Não é possível registar consulta para uma marcação cancelada ou marcada como não compareceu.",
                    "It is not possible to record a consultation for a cancelled or no-show booking.",
                ),
            )
            return redirect("clinic:appointment_list")

        return super().dispatch(request, *args, **kwargs)

    def get_permission_required(self):
        if self.existing_consultation is not None:
            return ("clinic.change_consulta",)
        return ("clinic.add_consulta",)

    def get_page_title(self) -> str:
        if self.existing_consultation is not None:
            return ui_text(self.request, "Actualizar consulta", "Update consultation")
        return ui_text(self.request, "Registar consulta", "Register consultation")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Complete o desfecho clínico da marcação com diagnóstico, prescrição e notas.",
            "Complete the clinical outcome of the booking with diagnosis, prescription, and notes.",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.existing_consultation is not None:
            kwargs["instance"] = self.existing_consultation
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["appointment"] = self.appointment
        context["form_title"] = (
            ui_text(self.request, "Editar consulta", "Edit consultation")
            if self.existing_consultation is not None
            else ui_text(self.request, "Nova consulta", "New consultation")
        )
        context["form_description"] = ui_text(
            self.request,
            "A consulta fica associada a esta marcação e conclui o atendimento clínico.",
            "The consultation is linked to this booking and closes the clinical encounter.",
        )
        context["submit_label"] = (
            ui_text(self.request, "Actualizar consulta", "Update consultation")
            if self.existing_consultation is not None
            else ui_text(self.request, "Guardar consulta", "Save consultation")
        )
        context["cancel_url"] = reverse("clinic:appointment_list")
        context["wide_fields"] = ("diagnostico", "prescricao", "notas_medico")
        return context

    def get_success_message(self) -> str:
        if self.existing_consultation is not None:
            return ui_text(self.request, "Consulta actualizada com sucesso.", "Consultation updated successfully.")
        return ui_text(self.request, "Consulta registada com sucesso.", "Consultation recorded successfully.")

    def form_valid(self, form):
        consultation = form.save(commit=False)
        consultation.agendamento = self.appointment
        consultation.save()
        self.object = consultation

        if self.appointment.status != "concluido":
            self.appointment.status = "concluido"
            self.appointment.save(update_fields=["status", "updated_at"])

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


class AppointmentAgendaView(AppPermissionMixin, ClinicPageMixin, TemplateView):
    template_name = "clinic/appointments/agenda.html"
    permission_required = "clinic.view_agendamento"
    segment = "appointment_agenda"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Agenda de consultas", "Consultation agenda")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Seleccione um profissional e acompanhe a semana completa de consultas agendadas para ele.",
            "Select a professional and follow the full week of consultations booked for them.",
        )

    def _parse_anchor_date(self):
        raw_value = (self.request.GET.get("date") or "").strip()
        if not raw_value:
            return timezone.localdate()
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            return timezone.localdate()

    def _selected_professional(self, professionals):
        raw_value = (self.request.GET.get("professional") or "").strip()
        try:
            selected_user_id = int(raw_value)
        except (TypeError, ValueError):
            selected_user_id = None

        for professional in professionals:
            if professional["user_id"] == selected_user_id:
                return professional
        return professionals[0] if professionals else None

    def _build_agenda_url(self, *, professional_id, anchor_date):
        params = {}
        if professional_id:
            params["professional"] = professional_id
        if anchor_date:
            params["date"] = anchor_date.isoformat()
        base_url = reverse("clinic:appointment_agenda")
        return f"{base_url}?{urlencode(params)}" if params else base_url

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        anchor_date = self._parse_anchor_date()
        week_start = anchor_date - timedelta(days=anchor_date.weekday())
        week_end = week_start + timedelta(days=6)
        professional_schedules = list(appointment_professional_schedule_queryset())
        professionals = build_appointment_professionals(professional_schedules, today)
        selected_professional = self._selected_professional(professionals)
        selected_schedules = (
            [schedule for schedule in professional_schedules if schedule.user_id == selected_professional["user_id"]]
            if selected_professional
            else []
        )
        selected_user_id = selected_professional["user_id"] if selected_professional else None
        selected_medico = (
            Medico.objects.select_related("user", "especialidade", "departamento", "departamento__branch", "hospital")
            .filter(user_id=selected_user_id)
            .first()
            if selected_user_id
            else None
        )
        weekly_appointments = (
            list(
                appointment_queryset().filter(
                    medico__user_id=selected_user_id,
                    data__range=(week_start, week_end),
                )
            )
            if selected_user_id is not None
            else []
        )
        appointments_by_day = {}
        for appointment in weekly_appointments:
            appointments_by_day.setdefault(appointment.data, []).append(appointment)

        week_days = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            day_schedules = sorted(
                [schedule for schedule in selected_schedules if schedule.applies_to_date(day)],
                key=lambda schedule: (schedule.start_time, schedule.end_time),
            )
            week_days.append(
                {
                    "date": day,
                    "is_today": day == today,
                    "is_anchor": day == anchor_date,
                    "schedule_blocks": day_schedules,
                    "appointments": appointments_by_day.get(day, []),
                }
            )

        professional_id = selected_user_id
        upcoming_appointments = (
            list(
                appointment_queryset()
                .filter(medico__user_id=selected_user_id, data__gte=today)
                .order_by("data", "hora")[:8]
            )
            if selected_user_id is not None
            else []
        )

        context["professionals"] = professionals
        context["selected_professional"] = selected_professional
        context["selected_professional_id"] = professional_id
        context["selected_professional_schedules"] = sorted(
            selected_schedules,
            key=lambda schedule: (schedule.weekday, schedule.start_time, schedule.end_time),
        )
        context["selected_medico"] = selected_medico
        context["selected_date"] = anchor_date.isoformat()
        context["week_start"] = week_start
        context["week_end"] = week_end
        context["week_days"] = week_days
        context["weekly_appointment_count"] = len(weekly_appointments)
        context["upcoming_appointments"] = upcoming_appointments
        context["prev_week_url"] = self._build_agenda_url(
            professional_id=professional_id,
            anchor_date=week_start - timedelta(days=7),
        )
        context["next_week_url"] = self._build_agenda_url(
            professional_id=professional_id,
            anchor_date=week_start + timedelta(days=7),
        )
        context["today_week_url"] = self._build_agenda_url(
            professional_id=professional_id,
            anchor_date=today,
        )
        context["list_url"] = reverse("clinic:appointment_list")
        context["create_url"] = reverse("clinic:appointment_create")
        return context


class SpecialtyListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = Especialidade
    template_name = "clinic/structure/specialties/list.html"
    context_object_name = "specialties"
    permission_required = "clinic.view_especialidade"
    segment = "specialties"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Especialidades", "Specialties")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Defina as designações clínicas usadas nos perfis dos médicos.",
            "Define the clinical designations used in doctor profiles.",
        )

    def get_queryset(self):
        return (
            Especialidade.objects.annotate(doctor_count=Count("medico"))
            .prefetch_related(
                Prefetch(
                    "medico_set",
                    queryset=Medico.objects.select_related("departamento__branch"),
                    to_attr="linked_doctors",
                )
            )
            .order_by("name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filter_branches = structure_filter_branches_for_request(self.request)
        active_branch_filter_id = default_structure_branch_id(self.request, filter_branches)
        branch_name_fallback = [branch.name for branch in filter_branches]
        specialties = context["specialties"]
        for specialty in specialties:
            linked_branch_names = sorted(
                {
                    doctor.departamento.branch.name
                    for doctor in getattr(specialty, "linked_doctors", [])
                    if doctor.departamento_id and doctor.departamento.branch_id
                }
            )
            effective_branch_names = linked_branch_names or branch_name_fallback
            specialty.branch_names = effective_branch_names
            specialty.branch_names_label = " · ".join(effective_branch_names) if effective_branch_names else ""

        base_queryset = self.get_queryset()
        context["total_specialties"] = base_queryset.count()
        context["specialties_with_doctors"] = base_queryset.filter(doctor_count__gt=0).count()
        context["total_linked_doctors"] = Medico.objects.filter(especialidade__isnull=False).count()
        context["filter_branches"] = filter_branches
        context["active_branch_filter_id"] = active_branch_filter_id
        return context


class SpecialtyCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Especialidade
    form_class = SpecialtyForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:specialty_list")
    permission_required = "clinic.add_especialidade"
    segment = "specialties"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Nova especialidade", "New specialty")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe uma nova designação clínica para os profissionais.",
            "Register a new clinical designation for professionals.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar especialidade", "Create specialty")
        context["form_description"] = ui_text(
            self.request,
            "Esta especialidade poderá ser atribuída aos médicos e aparecerá nas consultas e agendas.",
            "This specialty can be assigned to doctors and will appear in consultations and agendas.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar especialidade", "Save specialty")
        context["cancel_url"] = reverse("clinic:specialty_list")
        context["wide_fields"] = ("description",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Especialidade criada com sucesso.", "Specialty created successfully.")


class SpecialtyUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = Especialidade.objects.all()
    form_class = SpecialtyForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:specialty_list")
    permission_required = "clinic.change_especialidade"
    segment = "specialties"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar especialidade", "Edit specialty")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize a designação e a descrição desta especialidade.",
            "Update the designation and description of this specialty.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar especialidade", "Edit specialty")
        context["form_description"] = ui_text(
            self.request,
            "As alterações reflectem-se imediatamente nos perfis clínicos associados.",
            "Changes are immediately reflected in the linked clinical profiles.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar especialidade", "Update specialty")
        context["cancel_url"] = reverse("clinic:specialty_list")
        context["wide_fields"] = ("description",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Especialidade actualizada com sucesso.", "Specialty updated successfully.")


class DepartmentListView(AppPermissionMixin, ClinicPageMixin, ListView):
    model = Departamento
    template_name = "clinic/structure/departments/list.html"
    context_object_name = "departments"
    permission_required = "clinic.view_departamento"
    segment = "departments"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Departamentos", "Departments")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Organize os serviços clínicos por sucursal e defina o responsável de cada área.",
            "Organize clinical services by branch and define the lead for each area.",
        )

    def get_queryset(self):
        return (
            Departamento.objects.select_related(
                "branch",
                "hospital",
                "responsavel__user",
                "responsavel__especialidade",
            )
            .annotate(doctor_count=Count("medicos"))
            .order_by("branch__name", "name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = self.get_queryset()
        context["total_departments"] = base_queryset.count()
        context["departments_with_lead"] = base_queryset.filter(responsavel__isnull=False).count()
        context["departments_with_doctors"] = base_queryset.filter(doctor_count__gt=0).count()
        context["filter_branches"] = structure_filter_branches_for_request(self.request)
        context["active_branch_filter_id"] = default_structure_branch_id(
            self.request,
            context["filter_branches"],
        )
        return context


class DepartmentCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Departamento
    form_class = DepartmentForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:department_list")
    permission_required = "clinic.add_departamento"
    segment = "departments"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo departamento", "New department")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Crie um serviço clínico por sucursal e defina a liderança médica.",
            "Create a clinical service by branch and define the medical lead.",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar departamento", "Create department")
        context["form_description"] = ui_text(
            self.request,
            "Use departamentos para estruturar áreas como Ginecologia, Pediatria ou Ortopedia.",
            "Use departments to structure areas such as Gynecology, Pediatrics, or Orthopedics.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar departamento", "Save department")
        context["cancel_url"] = reverse("clinic:department_list")
        context["wide_fields"] = ("descricao",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Departamento criado com sucesso.", "Department created successfully.")


class DepartmentUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = Departamento.objects.select_related("branch", "responsavel__user")
    form_class = DepartmentForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:department_list")
    permission_required = "clinic.change_departamento"
    segment = "departments"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar departamento", "Edit department")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize o serviço, a sucursal e o responsável desta área clínica.",
            "Update the service, branch, and lead of this clinical area.",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar departamento", "Edit department")
        context["form_description"] = ui_text(
            self.request,
            "As alterações reflectem-se nos perfis médicos e na leitura operacional da sucursal.",
            "Changes are reflected in doctor profiles and the branch operational view.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar departamento", "Update department")
        context["cancel_url"] = reverse("clinic:department_list")
        context["wide_fields"] = ("descricao",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Departamento actualizado com sucesso.", "Department updated successfully.")


class InventoryOverviewView(AnyPermissionRequiredMixin, ClinicPageMixin, TemplateView):
    template_name = "clinic/inventory/overview.html"
    permission_options = (
        "clinic.view_armazem",
        "clinic.view_medicamento",
        "clinic.view_estoquemedicamento",
        "clinic.view_consumivel",
        "clinic.view_estoqueconsumivel",
        "clinic.view_movimentoinventario",
    )
    segment = "inventory"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Inventário", "Inventory")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Central de stock clínico com armazéns, níveis mínimos, consumíveis e movimentos.",
            "Clinical stock hub with warehouses, minimum levels, consumables, and movements.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        warehouses = inventory_visible_warehouses_for_request(self.request).annotate(
            medication_lines=Count("estoque_medicamentos", distinct=True),
            consumable_lines=Count("estoque_consumiveis", distinct=True),
            movement_count=Count("movimentos", distinct=True),
            low_medication_lines=Count(
                "estoque_medicamentos",
                filter=Q(
                    estoque_medicamentos__stock_minimo__gt=0,
                    estoque_medicamentos__quantidade__lte=F("estoque_medicamentos__stock_minimo"),
                ),
                distinct=True,
            ),
            low_consumable_lines=Count(
                "estoque_consumiveis",
                filter=Q(
                    estoque_consumiveis__stock_minimo__gt=0,
                    estoque_consumiveis__quantidade__lte=F("estoque_consumiveis__stock_minimo"),
                ),
                distinct=True,
            ),
        )
        medication_stock = medication_stock_queryset(self.request)
        consumable_stock = consumable_stock_queryset(self.request)
        recent_movements = inventory_movement_queryset(self.request)

        warehouse_list = list(warehouses)
        for warehouse in warehouse_list:
            warehouse.low_stock_total = warehouse.low_medication_lines + warehouse.low_consumable_lines

        context["total_warehouses"] = len(warehouse_list)
        context["medication_catalog_size"] = Medicamento.objects.filter(is_active=True).count()
        context["consumable_catalog_size"] = Consumivel.objects.filter(is_active=True).count()
        context["total_medication_units"] = medication_stock.aggregate(total=Sum("quantidade")).get("total") or 0
        context["total_consumable_units"] = consumable_stock.aggregate(total=Sum("quantidade")).get("total") or 0
        context["low_stock_alerts"] = (
            medication_stock.filter(stock_minimo__gt=0, quantidade__lte=F("stock_minimo")).count()
            + consumable_stock.filter(stock_minimo__gt=0, quantidade__lte=F("stock_minimo")).count()
        )
        context["warehouses"] = warehouse_list[:6]
        context["medication_alerts"] = medication_stock.filter(
            stock_minimo__gt=0,
            quantidade__lte=F("stock_minimo"),
        )[:8]
        context["consumable_alerts"] = consumable_stock.filter(
            stock_minimo__gt=0,
            quantidade__lte=F("stock_minimo"),
        )[:8]
        context["recent_movements"] = recent_movements[:8]
        return context


class WarehouseListView(AnyPermissionRequiredMixin, ClinicPageMixin, ListView):
    template_name = "clinic/inventory/warehouses/list.html"
    context_object_name = "warehouses"
    permission_options = ("clinic.view_armazem", "clinic.add_armazem", "clinic.change_armazem")
    segment = "inventory_warehouses"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Armazéns", "Warehouses")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Organize os pontos de armazenamento por sucursal e acompanhe cobertura e responsável.",
            "Organize storage points by branch and track coverage and manager.",
        )

    def get_queryset(self):
        return inventory_visible_warehouses_for_request(self.request).annotate(
            medication_lines=Count("estoque_medicamentos", distinct=True),
            consumable_lines=Count("estoque_consumiveis", distinct=True),
            movement_count=Count("movimentos", distinct=True),
            low_medication_lines=Count(
                "estoque_medicamentos",
                filter=Q(
                    estoque_medicamentos__stock_minimo__gt=0,
                    estoque_medicamentos__quantidade__lte=F("estoque_medicamentos__stock_minimo"),
                ),
                distinct=True,
            ),
            low_consumable_lines=Count(
                "estoque_consumiveis",
                filter=Q(
                    estoque_consumiveis__stock_minimo__gt=0,
                    estoque_consumiveis__quantidade__lte=F("estoque_consumiveis__stock_minimo"),
                ),
                distinct=True,
            ),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        warehouses = list(context["warehouses"])
        for warehouse in warehouses:
            warehouse.low_stock_total = warehouse.low_medication_lines + warehouse.low_consumable_lines
        context["warehouses"] = warehouses
        context["total_warehouses"] = len(warehouses)
        context["active_warehouses"] = sum(1 for warehouse in warehouses if warehouse.is_active)
        context["warehouses_with_alerts"] = sum(1 for warehouse in warehouses if warehouse.low_stock_total > 0)
        context["total_stock_lines"] = sum(
            warehouse.medication_lines + warehouse.consumable_lines for warehouse in warehouses
        )
        context.update(inventory_branch_filter_context(self.request))
        return context


class WarehouseCreateView(
    AnyPermissionRequiredMixin,
    InventoryFormRequestMixin,
    ModalFormMixin,
    ClinicPageMixin,
    CreateView,
):
    model = Armazem
    form_class = WarehouseForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:warehouse_list")
    permission_options = ("clinic.add_armazem",)
    segment = "inventory_warehouses"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo armazém", "New warehouse")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Crie um ponto de armazenamento operacional para controlar o stock da sucursal.",
            "Create an operational storage point to control branch stock.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar armazém", "Create warehouse")
        context["form_description"] = ui_text(
            self.request,
            "Armazéns podem representar farmácia, economato, laboratório ou apoio clínico.",
            "Warehouses can represent pharmacy, storeroom, laboratory, or clinical support.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar armazém", "Save warehouse")
        context["cancel_url"] = reverse("clinic:warehouse_list")
        context["wide_fields"] = ("description",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Armazém criado com sucesso.", "Warehouse created successfully.")


class WarehouseUpdateView(
    AnyPermissionRequiredMixin,
    InventoryFormRequestMixin,
    ModalFormMixin,
    ClinicPageMixin,
    UpdateView,
):
    form_class = WarehouseForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:warehouse_list")
    permission_options = ("clinic.change_armazem",)
    segment = "inventory_warehouses"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar armazém", "Edit warehouse")

    def get_queryset(self):
        return inventory_visible_warehouses_for_request(self.request)

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize a localização, sucursal e responsabilidade operacional deste armazém.",
            "Update the location, branch, and operational ownership of this warehouse.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar armazém", "Edit warehouse")
        context["form_description"] = ui_text(
            self.request,
            "As alterações reflectem-se nas listas de stock e movimentos ligados a este armazém.",
            "Changes are reflected in stock and movement lists linked to this warehouse.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar armazém", "Update warehouse")
        context["cancel_url"] = reverse("clinic:warehouse_list")
        context["wide_fields"] = ("description",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Armazém actualizado com sucesso.", "Warehouse updated successfully.")


class MedicationCatalogListView(AnyPermissionRequiredMixin, ClinicPageMixin, ListView):
    template_name = "clinic/inventory/medications/catalog.html"
    context_object_name = "catalog_items"
    permission_options = (
        "clinic.view_medicamento",
        "clinic.add_medicamento",
        "clinic.change_medicamento",
        "clinic.view_estoquemedicamento",
        "clinic.add_estoquemedicamento",
        "clinic.change_estoquemedicamento",
    )
    segment = "inventory_medication_catalog"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Catálogo de medicamentos", "Medication catalog")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Gira a ficha base dos medicamentos antes de os distribuir por armazéns e movimentos.",
            "Manage the base medication record before distributing it across warehouses and movements.",
        )

    def get_queryset(self):
        return medication_catalog_queryset(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        catalog_items = list(context["catalog_items"])
        for item in catalog_items:
            branch_names = []
            for entry in getattr(item, "inventory_stock_entries", []):
                branch_name = entry.armazem.branch.name
                if branch_name not in branch_names:
                    branch_names.append(branch_name)
            item.coverage_summary = ", ".join(branch_names) if branch_names else ui_text(
                self.request,
                "Sem stock distribuído",
                "No distributed stock",
            )
        context["catalog_items"] = catalog_items
        context["total_items"] = len(catalog_items)
        context["active_items"] = sum(1 for item in catalog_items if item.is_active)
        context["inactive_items"] = sum(1 for item in catalog_items if not item.is_active)
        context["items_without_stock"] = sum(1 for item in catalog_items if item.visible_stock_lines == 0)
        context["items_with_alerts"] = sum(1 for item in catalog_items if item.visible_low_stock_lines > 0)
        context["total_units"] = sum(item.visible_total_stock or 0 for item in catalog_items)
        context.update(inventory_branch_filter_context(self.request))
        return context


class MedicationListView(AnyPermissionRequiredMixin, ClinicPageMixin, ListView):
    template_name = "clinic/inventory/medications/list.html"
    context_object_name = "stock_entries"
    permission_options = (
        "clinic.view_estoquemedicamento",
        "clinic.view_medicamento",
        "clinic.add_estoquemedicamento",
        "clinic.change_estoquemedicamento",
    )
    segment = "inventory_medications"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Stock de medicamentos", "Medication stock")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Controle níveis de stock, mínimos e reposição por armazém sem misturar com o catálogo.",
            "Track stock levels, minimums, and replenishment by warehouse without mixing with the catalog.",
        )

    def get_queryset(self):
        return medication_stock_queryset(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = self.get_queryset()
        catalog_queryset = Medicamento.objects.order_by("name", "dosagem")
        context["total_stock_lines"] = base_queryset.count()
        context["catalog_medications"] = catalog_queryset.count()
        context["catalog_without_stock"] = catalog_queryset.filter(estoques__isnull=True).distinct().count()
        context["low_stock_lines"] = base_queryset.filter(
            stock_minimo__gt=0,
            quantidade__lte=F("stock_minimo"),
        ).count()
        context["out_of_stock_lines"] = base_queryset.filter(quantidade=0).count()
        context["total_units"] = base_queryset.aggregate(total=Sum("quantidade")).get("total") or 0
        context.update(inventory_branch_filter_context(self.request))
        return context


class MedicationCreateView(
    AnyPermissionRequiredMixin,
    InventoryFormRequestMixin,
    ModalFormMixin,
    ClinicPageMixin,
    CreateView,
):
    model = EstoqueMedicamento
    form_class = MedicationStockForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:medication_list")
    permission_options = ("clinic.add_estoquemedicamento",)
    segment = "inventory_medications"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo stock de medicamento", "New medication stock line")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Associe um medicamento a um armazém com níveis operacionais de stock.",
            "Link a medication to a warehouse with operational stock levels.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar stock de medicamento", "Create medication stock line")
        context["form_description"] = ui_text(
            self.request,
            "Use esta ficha para definir stock actual, mínimo, reposição e máximo por armazém.",
            "Use this form to define current, minimum, reorder, and maximum stock per warehouse.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar stock", "Save stock line")
        context["cancel_url"] = reverse("clinic:medication_list")
        context["wide_fields"] = ("observacoes",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Stock de medicamento criado com sucesso.", "Medication stock line created successfully.")


class MedicationUpdateView(
    AnyPermissionRequiredMixin,
    InventoryFormRequestMixin,
    ModalFormMixin,
    ClinicPageMixin,
    UpdateView,
):
    form_class = MedicationStockForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:medication_list")
    permission_options = ("clinic.change_estoquemedicamento",)
    segment = "inventory_medications"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar stock de medicamento", "Edit medication stock line")

    def get_queryset(self):
        return medication_stock_queryset(self.request)

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize quantidades e níveis operacionais para este medicamento neste armazém.",
            "Update quantities and operating levels for this medication in this warehouse.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar stock de medicamento", "Edit medication stock line")
        context["form_description"] = ui_text(
            self.request,
            "As alterações reflectem-se imediatamente no resumo do inventário e nos alertas de stock mínimo.",
            "Changes are immediately reflected in the inventory summary and low stock alerts.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar stock", "Update stock line")
        context["cancel_url"] = reverse("clinic:medication_list")
        context["wide_fields"] = ("observacoes",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Stock de medicamento actualizado com sucesso.", "Medication stock line updated successfully.")


class MedicationCatalogCreateView(AnyPermissionRequiredMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Medicamento
    form_class = MedicationForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:medication_catalog_list")
    permission_options = ("clinic.add_medicamento",)
    segment = "inventory_medication_catalog"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo item do catálogo", "New catalog item")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Crie a ficha base do medicamento para depois usá-lo nos armazéns e movimentos.",
            "Create the base medication record before using it in warehouses and movements.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar medicamento", "Create medication")
        context["form_description"] = ui_text(
            self.request,
            "O catálogo guarda nome, composição, dosagem, unidade e preço de referência.",
            "The catalog stores the name, composition, dosage, unit, and reference price.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar medicamento", "Save medication")
        context["cancel_url"] = reverse("clinic:medication_catalog_list")
        context["wide_fields"] = ("descricao",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Medicamento criado com sucesso.", "Medication created successfully.")


class MedicationCatalogUpdateView(AnyPermissionRequiredMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = Medicamento.objects.all()
    form_class = MedicationForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:medication_catalog_list")
    permission_options = ("clinic.change_medicamento",)
    segment = "inventory_medication_catalog"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar catálogo do medicamento", "Edit medication catalog")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize a ficha base do medicamento sem mexer nos níveis de stock por armazém.",
            "Update the base medication record without changing warehouse stock levels.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar medicamento", "Edit medication")
        context["form_description"] = ui_text(
            self.request,
            "Use esta edição quando precisar corrigir o catálogo e não apenas um armazém específico.",
            "Use this edit when you need to correct the catalog, not just one specific warehouse.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar medicamento", "Update medication")
        context["cancel_url"] = reverse("clinic:medication_catalog_list")
        context["wide_fields"] = ("descricao",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Medicamento actualizado com sucesso.", "Medication updated successfully.")


class ConsumableCatalogListView(AnyPermissionRequiredMixin, ClinicPageMixin, ListView):
    template_name = "clinic/inventory/consumables/catalog.html"
    context_object_name = "catalog_items"
    permission_options = (
        "clinic.view_consumivel",
        "clinic.add_consumivel",
        "clinic.change_consumivel",
        "clinic.view_estoqueconsumivel",
        "clinic.add_estoqueconsumivel",
        "clinic.change_estoqueconsumivel",
    )
    segment = "inventory_consumable_catalog"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Catálogo de consumíveis", "Consumable catalog")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Gira os materiais base da clínica antes de os lançar em armazéns e movimentos.",
            "Manage the clinic's base materials before assigning them to warehouses and movements.",
        )

    def get_queryset(self):
        return consumable_catalog_queryset(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        catalog_items = list(context["catalog_items"])
        for item in catalog_items:
            branch_names = []
            for entry in getattr(item, "inventory_stock_entries", []):
                branch_name = entry.armazem.branch.name
                if branch_name not in branch_names:
                    branch_names.append(branch_name)
            item.coverage_summary = ", ".join(branch_names) if branch_names else ui_text(
                self.request,
                "Sem stock distribuído",
                "No distributed stock",
            )
        context["catalog_items"] = catalog_items
        context["total_items"] = len(catalog_items)
        context["active_items"] = sum(1 for item in catalog_items if item.is_active)
        context["inactive_items"] = sum(1 for item in catalog_items if not item.is_active)
        context["items_without_stock"] = sum(1 for item in catalog_items if item.visible_stock_lines == 0)
        context["items_with_alerts"] = sum(1 for item in catalog_items if item.visible_low_stock_lines > 0)
        context["total_units"] = sum(item.visible_total_stock or 0 for item in catalog_items)
        context.update(inventory_branch_filter_context(self.request))
        return context


class ConsumableListView(AnyPermissionRequiredMixin, ClinicPageMixin, ListView):
    template_name = "clinic/inventory/consumables/list.html"
    context_object_name = "stock_entries"
    permission_options = (
        "clinic.view_estoqueconsumivel",
        "clinic.view_consumivel",
        "clinic.add_estoqueconsumivel",
        "clinic.change_estoqueconsumivel",
    )
    segment = "inventory_consumables"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Consumíveis", "Consumables")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Controle consumíveis clínicos e laboratoriais por armazém, mínimos e reposição.",
            "Track clinical and laboratory consumables by warehouse, minimums, and replenishment.",
        )

    def get_queryset(self):
        return consumable_stock_queryset(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = self.get_queryset()
        catalog_queryset = Consumivel.objects.order_by("name")
        context["total_stock_lines"] = base_queryset.count()
        context["catalog_consumables"] = catalog_queryset.count()
        context["catalog_without_stock"] = catalog_queryset.filter(estoques__isnull=True).distinct().count()
        context["low_stock_lines"] = base_queryset.filter(
            stock_minimo__gt=0,
            quantidade__lte=F("stock_minimo"),
        ).count()
        context["out_of_stock_lines"] = base_queryset.filter(quantidade=0).count()
        context["total_units"] = base_queryset.aggregate(total=Sum("quantidade")).get("total") or 0
        context.update(inventory_branch_filter_context(self.request))
        return context


class ConsumableCreateView(
    AnyPermissionRequiredMixin,
    InventoryFormRequestMixin,
    ModalFormMixin,
    ClinicPageMixin,
    CreateView,
):
    model = EstoqueConsumivel
    form_class = ConsumableStockForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:consumable_list")
    permission_options = ("clinic.add_estoqueconsumivel",)
    segment = "inventory_consumables"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo stock de consumível", "New consumable stock line")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Associe um consumível a um armazém com níveis mínimos e ponto de reposição.",
            "Link a consumable to a warehouse with minimum levels and reorder point.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar stock de consumível", "Create consumable stock line")
        context["form_description"] = ui_text(
            self.request,
            "Use esta ficha para materiais como luvas, seringas, pensos, máscaras ou kits.",
            "Use this form for items such as gloves, syringes, dressings, masks, or kits.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar stock", "Save stock line")
        context["cancel_url"] = reverse("clinic:consumable_list")
        context["wide_fields"] = ("observacoes",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Stock de consumível criado com sucesso.", "Consumable stock line created successfully.")


class ConsumableUpdateView(
    AnyPermissionRequiredMixin,
    InventoryFormRequestMixin,
    ModalFormMixin,
    ClinicPageMixin,
    UpdateView,
):
    form_class = ConsumableStockForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:consumable_list")
    permission_options = ("clinic.change_estoqueconsumivel",)
    segment = "inventory_consumables"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar stock de consumível", "Edit consumable stock line")

    def get_queryset(self):
        return consumable_stock_queryset(self.request)

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize níveis operacionais e observações deste consumível por armazém.",
            "Update operating levels and notes for this consumable per warehouse.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar stock de consumível", "Edit consumable stock line")
        context["form_description"] = ui_text(
            self.request,
            "As alterações entram imediatamente nos alertas e relatórios operacionais do inventário.",
            "Changes are immediately reflected in inventory alerts and operating reports.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar stock", "Update stock line")
        context["cancel_url"] = reverse("clinic:consumable_list")
        context["wide_fields"] = ("observacoes",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Stock de consumível actualizado com sucesso.", "Consumable stock line updated successfully.")


class ConsumableCatalogCreateView(AnyPermissionRequiredMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = Consumivel
    form_class = ConsumableForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:consumable_catalog_list")
    permission_options = ("clinic.add_consumivel",)
    segment = "inventory_consumable_catalog"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo consumível", "New consumable")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Crie um item de catálogo para materiais usados nas consultas, enfermagem e apoio.",
            "Create a catalog item for materials used in consultations, nursing, and support.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar consumível", "Create consumable")
        context["form_description"] = ui_text(
            self.request,
            "O catálogo de consumíveis guarda o item base antes de o distribuir por armazéns.",
            "The consumable catalog stores the base item before distributing it across warehouses.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar consumível", "Save consumable")
        context["cancel_url"] = reverse("clinic:consumable_catalog_list")
        context["wide_fields"] = ("descricao",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Consumível criado com sucesso.", "Consumable created successfully.")


class ConsumableCatalogUpdateView(AnyPermissionRequiredMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    queryset = Consumivel.objects.all()
    form_class = ConsumableForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:consumable_catalog_list")
    permission_options = ("clinic.change_consumivel",)
    segment = "inventory_consumable_catalog"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar consumível", "Edit consumable")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Actualize a ficha base do consumível sem mexer nos níveis de stock dos armazéns.",
            "Update the base consumable record without changing warehouse stock levels.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar consumível", "Edit consumable")
        context["form_description"] = ui_text(
            self.request,
            "Use esta edição para corrigir nome, unidade, preço de referência ou descrição do catálogo.",
            "Use this edit to correct the catalog name, unit, reference price, or description.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar consumível", "Update consumable")
        context["cancel_url"] = reverse("clinic:consumable_catalog_list")
        context["wide_fields"] = ("descricao",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Consumível actualizado com sucesso.", "Consumable updated successfully.")


class InventoryMovementListView(AnyPermissionRequiredMixin, ClinicPageMixin, ListView):
    template_name = "clinic/inventory/movements/list.html"
    context_object_name = "movements"
    permission_options = (
        "clinic.view_movimentoinventario",
        "clinic.add_movimentoinventario",
        "clinic.change_movimentoinventario",
    )
    segment = "inventory_movements"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Movimentos", "Movements")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe entradas, saídas e ajustes para manter rastreabilidade completa do inventário.",
            "Record entries, exits, and adjustments to keep a complete inventory trace.",
        )

    def get_queryset(self):
        return inventory_movement_queryset(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        base_queryset = self.get_queryset()
        context["total_movements"] = base_queryset.count()
        context["entry_movements"] = base_queryset.filter(
            movement_type=MovimentoInventario.MovementTypeChoices.ENTRADA
        ).count()
        context["exit_movements"] = base_queryset.filter(
            movement_type=MovimentoInventario.MovementTypeChoices.SAIDA
        ).count()
        context["adjustment_movements"] = base_queryset.filter(
            movement_type=MovimentoInventario.MovementTypeChoices.AJUSTE
        ).count()
        context.update(inventory_branch_filter_context(self.request))
        return context


class InventoryMovementCreateView(
    AnyPermissionRequiredMixin,
    InventoryFormRequestMixin,
    ModalFormMixin,
    ClinicPageMixin,
    CreateView,
):
    model = MovimentoInventario
    form_class = InventoryMovementForm
    template_name = "accounts/shared/form.html"
    modal_template_name = "accounts/shared/modal_form.html"
    success_url = reverse_lazy("clinic:inventory_movement_list")
    permission_options = ("clinic.add_movimentoinventario",)
    segment = "inventory_movements"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo movimento", "New movement")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Lance entrada, saída ou ajuste e actualize o stock automaticamente.",
            "Register an entry, exit, or adjustment and update stock automatically.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Criar movimento de inventário", "Create inventory movement")
        context["form_description"] = ui_text(
            self.request,
            "Cada movimento grava o stock anterior, o stock resultante e a referência operacional.",
            "Each movement stores the previous stock, resulting stock, and operational reference.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar movimento", "Save movement")
        context["cancel_url"] = reverse("clinic:inventory_movement_list")
        context["wide_fields"] = ("notes",)
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Movimento registado com sucesso.", "Movement recorded successfully.")

    def form_valid(self, form):
        self.object = form.save(user=self.request.user)
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


class PharmacySaleListView(AnyPermissionRequiredMixin, ClinicPageMixin, ListView):
    template_name = "clinic/pharmacy/sales/list.html"
    context_object_name = "sales"
    permission_options = ("clinic.view_pharmacysale",)
    segment = "pharmacy_sales"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Farmácia", "Pharmacy")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Vendas da farmácia com total facturado, IVA incluído, método de pagamento e recibo.",
            "Pharmacy sales with billed totals, included VAT, payment method, and receipt.",
        )

    def get_queryset(self):
        return pharmacy_sale_queryset(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sales = list(context["sales"])
        context["sales"] = sales
        context["total_sales"] = len(sales)
        context["sales_today"] = sum(
            1 for sale in sales if timezone.localtime(sale.sold_at).date() == timezone.localdate()
        )
        context["revenue_total"] = quantize_money(
            sum(sale.total_amount for sale in sales if sale.status == PharmacySale.StatusChoices.COMPLETED)
        )
        context["tax_total"] = quantize_money(
            sum(sale.tax_amount for sale in sales if sale.status == PharmacySale.StatusChoices.COMPLETED)
        )
        context.update(inventory_branch_filter_context(self.request))
        return context


class PharmacyDailyReportView(AnyPermissionRequiredMixin, ClinicPageMixin, TemplateView):
    template_name = "clinic/pharmacy/reports/daily.html"
    permission_options = ("clinic.view_pharmacysale",)
    segment = "pharmacy_reports"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Caixa diário", "Daily cash report")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Fecho diário da farmácia com vendas, IVA incluído, reversões e quebra por método de pagamento.",
            "Daily pharmacy cash close with sales, included VAT, reversals, and payment-method breakdown.",
        )

    def parse_requested_date(self, raw_value):
        if not raw_value:
            return None
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            return None

    def get_selected_period(self):
        requested_single_date = self.parse_requested_date(self.request.GET.get("date"))
        requested_start_date = self.parse_requested_date(self.request.GET.get("start_date"))
        requested_end_date = self.parse_requested_date(self.request.GET.get("end_date"))
        fallback_date = requested_single_date or timezone.localdate()

        start_date = requested_start_date or requested_end_date or fallback_date
        end_date = requested_end_date or requested_start_date or fallback_date

        if start_date > end_date:
            start_date, end_date = end_date, start_date

        return start_date, end_date

    def build_report_context(self):
        cached_context = getattr(self, "_pharmacy_daily_report_context", None)
        if cached_context is not None:
            return cached_context

        start_date, end_date = self.get_selected_period()
        visible_sales = pharmacy_sale_queryset(self.request)
        period_start, _period_start_end = local_day_bounds(start_date)
        _period_end_start, period_end = local_day_bounds(end_date)
        sales_for_day = visible_sales.filter(sold_at__gte=period_start, sold_at__lt=period_end)
        reversals_for_day = visible_sales.filter(reversed_at__gte=period_start, reversed_at__lt=period_end).exclude(
            status=PharmacySale.StatusChoices.COMPLETED
        )

        completed_sales = list(sales_for_day.filter(status=PharmacySale.StatusChoices.COMPLETED))
        reversals = list(reversals_for_day)
        sold_total = quantize_money(sum(sale.total_amount for sale in completed_sales))
        sold_base = quantize_money(sum(sale.subtotal for sale in completed_sales))
        sold_tax = quantize_money(sum(sale.tax_amount for sale in completed_sales))
        reversed_total = quantize_money(sum(sale.total_amount for sale in reversals))
        net_cash_total = quantize_money(sold_total - reversed_total)

        payment_breakdown = []
        payment_methods = {}
        for sale in completed_sales:
            key = sale.payment_method_id or f"manual-{sale.payment_method_id}"
            entry = payment_methods.setdefault(
                key,
                {
                    "method_name": sale.payment_method.name if sale.payment_method_id else ui_text(
                        self.request,
                        "Sem método",
                        "No method",
                    ),
                    "sales_count": 0,
                    "gross_total": Decimal("0.00"),
                    "tax_total": Decimal("0.00"),
                    "reversal_total": Decimal("0.00"),
                },
            )
            entry["sales_count"] += 1
            entry["gross_total"] += sale.total_amount
            entry["tax_total"] += sale.tax_amount

        for sale in reversals:
            key = sale.payment_method_id or f"manual-{sale.payment_method_id}"
            entry = payment_methods.setdefault(
                key,
                {
                    "method_name": sale.payment_method.name if sale.payment_method_id else ui_text(
                        self.request,
                        "Sem método",
                        "No method",
                    ),
                    "sales_count": 0,
                    "gross_total": Decimal("0.00"),
                    "tax_total": Decimal("0.00"),
                    "reversal_total": Decimal("0.00"),
                },
            )
            entry["reversal_total"] += sale.total_amount

        for entry in payment_methods.values():
            entry["gross_total"] = quantize_money(entry["gross_total"])
            entry["tax_total"] = quantize_money(entry["tax_total"])
            entry["reversal_total"] = quantize_money(entry["reversal_total"])
            entry["net_total"] = quantize_money(entry["gross_total"] - entry["reversal_total"])
            payment_breakdown.append(entry)

        payment_breakdown.sort(key=lambda item: item["method_name"].lower())

        selected_day_count = (end_date - start_date).days + 1
        is_single_day = start_date == end_date
        period_label = (
            start_date.strftime("%d/%m/%Y")
            if is_single_day
            else f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"
        )
        export_query = urlencode(
            {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "export": "pdf",
            }
        )

        context = {
            "selected_date": start_date,
            "selected_start_date": start_date,
            "selected_end_date": end_date,
            "selected_day_count": selected_day_count,
            "selected_period_label": period_label,
            "is_single_day": is_single_day,
            "sales_for_day": completed_sales[:12],
            "reversals_for_day": reversals[:12],
            "report_sales": completed_sales,
            "report_reversals": reversals,
            "sold_count": len(completed_sales),
            "reversed_count": len(reversals),
            "sold_total": sold_total,
            "sold_base": sold_base,
            "sold_tax": sold_tax,
            "reversed_total": reversed_total,
            "net_cash_total": net_cash_total,
            "payment_breakdown": payment_breakdown,
            "report_export_query": export_query,
        }
        self._pharmacy_daily_report_context = context
        return context

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self.build_report_context())
        return context

    def render_pdf_response(self):
        report_context = self.build_report_context()
        html = render_to_string(
            "clinic/pharmacy/reports/daily_pdf.html",
            {
                **report_context,
                "generated_at": timezone.localtime(),
                "current_branch": getattr(self.request, "clinic_current_branch", None),
                "system_preferences": get_system_preferences(),
                "request": self.request,
            },
            request=self.request,
        )

        from weasyprint import HTML

        pdf_bytes = HTML(string=html, base_url=self.request.build_absolute_uri("/")).write_pdf()
        if report_context["is_single_day"]:
            filename_suffix = report_context["selected_start_date"].isoformat()
        else:
            filename_suffix = (
                f"{report_context['selected_start_date'].isoformat()}-a-"
                f"{report_context['selected_end_date'].isoformat()}"
            )
        filename = f"caixa-farmacia-{filename_suffix}.pdf"
        return FileResponse(
            BytesIO(pdf_bytes),
            as_attachment=True,
            filename=filename,
            content_type="application/pdf",
        )

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "pdf":
            return self.render_pdf_response()
        return super().get(request, *args, **kwargs)


class PharmacySaleCreateView(AnyPermissionRequiredMixin, ClinicPageMixin, TemplateView):
    template_name = "clinic/pharmacy/sales/cart.html"
    permission_options = ("clinic.add_pharmacysale",)
    segment = "pharmacy_sales"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Nova venda", "New sale")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Monte o carrinho, escolha o método de pagamento e facture a saída a partir de um armazém.",
            "Build the cart, choose the payment method, and invoice the sale from a warehouse.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cart_state = resolve_pharmacy_cart(self.request)
        context.update(cart_state)
        context["pharmacy_selector_payload"] = build_pharmacy_selector_payload(self.request, cart_state["cart"])
        context.setdefault(
            "add_form",
            PharmacyCartItemForm(request=self.request, cart_snapshot=cart_state["cart"]),
        )
        context.setdefault(
            "checkout_form",
            PharmacyCheckoutForm(request=self.request),
        )
        context["cart_has_items"] = bool(cart_state["cart_items"])
        return context

    def render_cart_page(self, request, *, add_form=None, checkout_form=None, status=200):
        self.request = request
        context = self.get_context_data(
            add_form=add_form or PharmacyCartItemForm(request=request, cart_snapshot=get_pharmacy_cart(request)),
            checkout_form=checkout_form or PharmacyCheckoutForm(request=request),
        )
        return self.render_to_response(context, status=status)

    def render_cart_ajax(
        self,
        request,
        *,
        add_form=None,
        status=200,
        message="",
        refresh_add_form=False,
    ):
        cart_state = resolve_pharmacy_cart(request)
        context = {
            **cart_state,
            "cart_has_items": bool(cart_state["cart_items"]),
            "system_preferences": get_system_preferences(),
        }
        payload = {
            "stats_html": render_to_string(
                "clinic/pharmacy/sales/includes/stats_cards.html",
                context,
            ),
            "cart_card_html": render_to_string(
                "clinic/pharmacy/sales/includes/cart_card.html",
                context,
                request=request,
            ),
            "checkout_summary_html": render_to_string(
                "clinic/pharmacy/sales/includes/checkout_summary.html",
                context,
            ),
            "message": message,
            "selector_payload": build_pharmacy_selector_payload(request, cart_state["cart"]),
        }
        if refresh_add_form:
            context["add_form"] = add_form or PharmacyCartItemForm(
                request=request,
                cart_snapshot=cart_state["cart"],
            )
            payload["add_form_html"] = render_to_string(
                "clinic/pharmacy/sales/includes/add_form_card.html",
                context,
                request=request,
            )
        return JsonResponse(payload, status=status)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("cart_action") or request.POST.get("action") or "add_item"
        if action == "add_item":
            return self.handle_add_item(request)
        if action == "remove_item":
            return self.handle_remove_item(request)
        if action == "clear_cart":
            return self.handle_clear_cart(request)
        if action == "finalize":
            return self.handle_finalize(request)
        return redirect("clinic:pharmacy_sale_create")

    def handle_add_item(self, request):
        cart = get_pharmacy_cart(request)
        form = PharmacyCartItemForm(request.POST, request=request, cart_snapshot=cart)
        if not form.is_valid():
            if is_ajax_request(request):
                return self.render_cart_ajax(
                    request,
                    add_form=form,
                    status=422,
                    refresh_add_form=True,
                )
            return self.render_cart_page(request, add_form=form, checkout_form=PharmacyCheckoutForm(request=request), status=422)

        warehouse = form.cleaned_data["warehouse"]
        item_type = form.cleaned_data["item_type"]
        item = (
            form.cleaned_data["medicamento"]
            if item_type == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO
            else form.cleaned_data["consumivel"]
        )
        quantity = form.cleaned_data["quantity"]

        if not cart.get("warehouse_id"):
            cart["warehouse_id"] = warehouse.pk

        item_payload = {
            "item_name": item.display_name,
            "sku": item.sku or "",
            "unit_label": (item.unidade_medida or "un") if item_type == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO else (item.unidade_medida or "un"),
            "unit_price": f"{quantize_money(item.preco if item_type == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO else item.preco_referencia):.2f}",
        }

        merged = False
        for line in cart["items"]:
            if line["item_type"] == item_type and int(line["item_id"]) == item.pk:
                line["quantity"] += quantity
                line.update(item_payload)
                merged = True
                break
        if not merged:
            cart["items"].append(
                {
                    "item_type": item_type,
                    "item_id": item.pk,
                    "quantity": quantity,
                    **item_payload,
                }
            )

        save_pharmacy_cart(request, cart)
        if is_ajax_request(request):
            return self.render_cart_ajax(
                request,
                message=ui_text(
                    request,
                    "Item adicionado ao carrinho.",
                    "Item added to the cart.",
                ),
            )
        messages.success(
            request,
            ui_text(
                request,
                "Item adicionado ao carrinho com sucesso.",
                "Item added to the cart successfully.",
            ),
        )
        return redirect("clinic:pharmacy_sale_create")

    def handle_remove_item(self, request):
        line_key = request.POST.get("line_key", "")
        cart = get_pharmacy_cart(request)
        cart["items"] = [
            line
            for line in cart["items"]
            if f"{line['item_type']}:{line['item_id']}" != line_key
        ]
        if not cart["items"]:
            cart["warehouse_id"] = None
        save_pharmacy_cart(request, cart)
        if is_ajax_request(request):
            return self.render_cart_ajax(
                request,
                message=ui_text(
                    request,
                    "Item removido do carrinho.",
                    "Item removed from the cart.",
                ),
            )
        messages.success(
            request,
            ui_text(
                request,
                "Item removido do carrinho.",
                "Item removed from the cart.",
            ),
        )
        return redirect("clinic:pharmacy_sale_create")

    def handle_clear_cart(self, request):
        clear_pharmacy_cart(request)
        if is_ajax_request(request):
            return self.render_cart_ajax(
                request,
                message=ui_text(
                    request,
                    "Carrinho limpo com sucesso.",
                    "Cart cleared successfully.",
                ),
                refresh_add_form=True,
            )
        messages.success(
            request,
            ui_text(
                request,
                "Carrinho limpo com sucesso.",
                "Cart cleared successfully.",
            ),
        )
        return redirect("clinic:pharmacy_sale_create")

    def handle_finalize(self, request):
        cart_state = resolve_pharmacy_cart(request)
        checkout_form = PharmacyCheckoutForm(request.POST, request=request)
        if not cart_state["cart_items"]:
            checkout_form.add_error(
                None,
                ui_text(
                    request,
                    "Adicione pelo menos um item ao carrinho antes de facturar.",
                    "Add at least one item to the cart before billing.",
                ),
            )
        if cart_state["cart_warehouse"] is None:
            checkout_form.add_error(
                None,
                ui_text(
                    request,
                    "Seleccione um armazém válido para esta venda.",
                    "Select a valid warehouse for this sale.",
                ),
            )
        if cart_state["cart_has_stock_issue"]:
            checkout_form.add_error(
                None,
                ui_text(
                    request,
                    "Há itens no carrinho sem stock suficiente. Ajuste as quantidades antes de finalizar.",
                    "There are cart items without enough stock. Adjust the quantities before finalizing.",
                ),
            )
        if not checkout_form.is_valid():
            return self.render_cart_page(
                request,
                add_form=PharmacyCartItemForm(request=request, cart_snapshot=cart_state["cart"]),
                checkout_form=checkout_form,
                status=422,
            )

        warehouse = cart_state["cart_warehouse"]
        patient = checkout_form.cleaned_data.get("patient")
        customer_name = checkout_form.cleaned_data.get("customer_name") or (patient.full_name if patient else "")
        payment_method = checkout_form.cleaned_data["payment_method"]
        notes = checkout_form.cleaned_data.get("notes", "")

        try:
            with transaction.atomic():
                stock_operations = []
                for entry in cart_state["cart_items"]:
                    if entry["item_type"] == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO:
                        stock_entry = (
                            EstoqueMedicamento.objects.select_for_update()
                            .select_related("medicamento", "armazem__branch")
                            .filter(armazem=warehouse, medicamento_id=entry["item_id"])
                            .first()
                        )
                    else:
                        stock_entry = (
                            EstoqueConsumivel.objects.select_for_update()
                            .select_related("consumivel", "armazem__branch")
                            .filter(armazem=warehouse, consumivel_id=entry["item_id"])
                            .first()
                        )

                    if stock_entry is None:
                        raise ValidationError(
                            ui_text(
                                request,
                                f"O item {entry['item_name']} deixou de estar disponível neste armazém.",
                                f"The item {entry['item_name']} is no longer available in this warehouse.",
                            )
                        )
                    if entry["quantity"] > stock_entry.quantidade:
                        raise ValidationError(
                            ui_text(
                                request,
                                f"O stock de {entry['item_name']} já não cobre a quantidade pedida.",
                                f"The stock for {entry['item_name']} no longer covers the requested quantity.",
                            )
                        )
                    stock_operations.append((entry, stock_entry))

                sale = PharmacySale.objects.create(
                    branch=warehouse.branch,
                    warehouse=warehouse,
                    patient=patient,
                    customer_name=customer_name,
                    payment_method=payment_method,
                    subtotal=cart_state["cart_subtotal"],
                    tax_rate=cart_state["cart_tax_rate"],
                    tax_amount=cart_state["cart_tax_amount"],
                    total_amount=cart_state["cart_total_amount"],
                    notes=notes,
                    sold_by=request.user,
                )

                for entry, stock_entry in stock_operations:
                    stock_before = stock_entry.quantidade
                    stock_after = stock_before - entry["quantity"]
                    related_item = (
                        stock_entry.medicamento
                        if entry["item_type"] == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO
                        else stock_entry.consumivel
                    )

                    PharmacySaleItem.objects.create(
                        sale=sale,
                        item_type=entry["item_type"],
                        medicamento=related_item if entry["item_type"] == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO else None,
                        consumivel=related_item if entry["item_type"] == PharmacySaleItem.ItemTypeChoices.CONSUMIVEL else None,
                        item_name=entry["item_name"],
                        sku=entry["sku"],
                        unit_label=entry["unit_label"],
                        quantity=entry["quantity"],
                        unit_price=entry["unit_price"],
                        line_subtotal=entry["line_subtotal"],
                        line_tax_amount=entry["line_tax_amount"],
                        line_total=entry["line_total"],
                    )

                    stock_entry.quantidade = stock_after
                    stock_entry.last_counted_at = timezone.localdate()
                    stock_entry.save(update_fields=["quantidade", "last_counted_at", "updated_at"])

                    movement_kwargs = {
                        "armazem": warehouse,
                        "item_type": entry["item_type"],
                        "movement_type": MovimentoInventario.MovementTypeChoices.SAIDA,
                        "quantity": entry["quantity"],
                        "stock_before": stock_before,
                        "stock_after": stock_after,
                        "unit_cost": entry["unit_price"],
                        "reference": sale.sale_number,
                        "notes": ui_text(
                            request,
                            f"Venda de farmácia para {sale.customer_display_name}",
                            f"Pharmacy sale for {sale.customer_display_name}",
                        ),
                        "created_by": request.user,
                    }
                    if entry["item_type"] == PharmacySaleItem.ItemTypeChoices.MEDICAMENTO:
                        movement_kwargs["medicamento"] = related_item
                    else:
                        movement_kwargs["consumivel"] = related_item
                    MovimentoInventario.objects.create(**movement_kwargs)
        except ValidationError as error:
            checkout_form.add_error(None, error.message if hasattr(error, "message") else str(error))
            return self.render_cart_page(
                request,
                add_form=PharmacyCartItemForm(request=request, cart_snapshot=cart_state["cart"]),
                checkout_form=checkout_form,
                status=422,
            )

        clear_pharmacy_cart(request)
        messages.success(
            request,
            ui_text(
                request,
                "Venda facturada com sucesso. Já pode baixar o recibo.",
                "Sale billed successfully. You can now download the receipt.",
            ),
        )
        return redirect("clinic:pharmacy_sale_detail", pk=sale.pk)


class PharmacySaleDetailView(AnyPermissionRequiredMixin, ClinicPageMixin, DetailView):
    template_name = "clinic/pharmacy/sales/detail.html"
    context_object_name = "sale"
    permission_options = ("clinic.view_pharmacysale",)
    segment = "pharmacy_sales"

    def get_queryset(self):
        return pharmacy_sale_queryset(self.request)

    def get_page_title(self) -> str:
        sale = self.get_object()
        return sale.sale_number

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Resumo da venda facturada, IVA incluído no preço final, estado e atalhos para recibo.",
            "Summary of the billed sale, VAT included in the final price, status, and receipt shortcuts.",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["line_count"] = self.object.items.count()
        context["can_reverse"] = self.object.status == PharmacySale.StatusChoices.COMPLETED
        return context


class PharmacySaleReverseView(AppPermissionMixin, View):
    permission_required = "clinic.change_pharmacysale"
    login_url = "clinic:login"

    def post(self, request, pk, action):
        sale = get_object_or_404(pharmacy_sale_queryset(request), pk=pk)
        if action == "cancel":
            reversal_status = PharmacySale.StatusChoices.CANCELLED
            success_message = ui_text(
                request,
                "Venda cancelada com reposição de stock concluída.",
                "Sale cancelled and stock successfully restored.",
            )
        elif action == "return":
            reversal_status = PharmacySale.StatusChoices.RETURNED
            success_message = ui_text(
                request,
                "Devolução registada com reposição de stock concluída.",
                "Return recorded and stock successfully restored.",
            )
        else:
            return JsonResponse(
                {
                    "message": ui_text(
                        request,
                        "Acção de reversão inválida.",
                        "Invalid reversal action.",
                    )
                },
                status=400,
            )

        try:
            reverse_pharmacy_sale(
                sale=sale,
                performed_by=request.user,
                reversal_status=reversal_status,
                request=request,
            )
        except ValidationError as error:
            return JsonResponse(
                {
                    "message": error.message if hasattr(error, "message") else str(error),
                },
                status=400,
            )

        return JsonResponse(
            {
                "message": success_message,
                "redirect_url": reverse("clinic:pharmacy_sale_detail", args=[sale.pk]),
            }
        )


class PharmacyItemInfoView(AppPermissionMixin, View):
    permission_required = "clinic.add_pharmacysale"
    login_url = "clinic:login"

    def get(self, request):
        warehouse_id = request.GET.get("warehouse")
        item_type = request.GET.get("item_type")
        item_id = request.GET.get("item_id")

        if item_type not in {
            PharmacySaleItem.ItemTypeChoices.MEDICAMENTO,
            PharmacySaleItem.ItemTypeChoices.CONSUMIVEL,
        }:
            return JsonResponse(
                {
                    "message": ui_text(
                        request,
                        "Tipo de item inválido.",
                        "Invalid item type.",
                    )
                },
                status=400,
            )

        if not warehouse_id or not item_id:
            return JsonResponse(
                {
                    "message": ui_text(
                        request,
                        "Seleccione um armazém e um item válidos.",
                        "Select a valid warehouse and item.",
                    )
                },
                status=400,
            )

        warehouse = get_object_or_404(inventory_visible_warehouses_for_request(request), pk=warehouse_id)
        try:
            payload = pharmacy_item_info_payload(
                request,
                warehouse=warehouse,
                item_type=item_type,
                item_id=int(item_id),
            )
        except Exception:
            return JsonResponse(
                {
                    "message": ui_text(
                        request,
                        "Este item não tem stock disponível neste armazém.",
                        "This item has no available stock in this warehouse.",
                    )
                },
                status=404,
            )

        return JsonResponse(payload)


class PharmacySaleReceiptPdfView(AppPermissionMixin, View):
    permission_required = "clinic.view_pharmacysale"
    login_url = "clinic:login"

    def get(self, request, pk):
        sale = get_object_or_404(pharmacy_sale_queryset(request), pk=pk)
        html = render_to_string(
            "clinic/pharmacy/sales/receipt_pdf.html",
            {
                "sale": sale,
                "items": list(sale.items.all()),
                "generated_at": timezone.localtime(),
                "system_preferences": get_system_preferences(),
                "request": request,
            },
            request=request,
        )

        from weasyprint import HTML

        pdf_bytes = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
        filename = f"recibo-farmacia-{sale.sale_number.lower()}.pdf"
        return FileResponse(
            BytesIO(pdf_bytes),
            as_attachment=True,
            filename=filename,
            content_type="application/pdf",
        )


class WorkScheduleListView(AppPermissionMixin, ClinicPageMixin, ListView):
    template_name = "clinic/schedules/list.html"
    context_object_name = "schedules"
    permission_required = "clinic.view_horariotrabalho"
    segment = "schedules"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Horários", "Schedules")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Grade semanal da equipa clínica, independente das marcações, já pronta para sincronizar com agenda.",
            "Weekly team roster, independent from bookings, already prepared to sync with scheduling.",
        )

    def get_queryset(self):
        return work_schedule_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        schedule_list = list(context["schedules"])
        today_schedules = [schedule for schedule in schedule_list if schedule.applies_to_date(today)]
        context["schedules"] = schedule_list
        context["total_schedule_blocks"] = len(schedule_list)
        context["active_schedule_blocks"] = sum(1 for schedule in schedule_list if schedule.is_active)
        context["scheduled_professionals"] = len({schedule.user_id for schedule in schedule_list})
        context["today_on_duty"] = len(today_schedules)
        context["appointment_ready"] = len(
            {schedule.user_id for schedule in schedule_list if schedule.accepts_appointments}
        )
        context["today_schedules"] = sorted(
            today_schedules,
            key=lambda schedule: (schedule.start_time, schedule.professional_name.lower()),
        )[:6]
        context["schedule_calendar_payload"] = [
            serialize_work_schedule(schedule) for schedule in schedule_list
        ]
        context["calendar_anchor_date"] = today.isoformat()
        return context


class WorkScheduleDetailView(AppPermissionMixin, ModalDetailMixin, ClinicPageMixin, DetailView):
    template_name = "clinic/schedules/detail.html"
    permission_required = "clinic.view_horariotrabalho"
    segment = "schedules"
    modal_size = "modal-xl"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Detalhes do horário", "Schedule details")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Resumo operacional do turno, validade, sincronização e ocupação da agenda.",
            "Operational summary of the shift, validity, synchronization, and calendar occupancy.",
        )

    def get_queryset(self):
        return work_schedule_queryset()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        today_appointments = list(self.object.appointment_queryset(on_date=today)[:5])
        upcoming_appointments = list(self.object.appointment_queryset().filter(data__gte=today)[:6])
        context["detail_partial"] = "clinic/schedules/includes/detail_content.html"
        context["modal_heading"] = self.object.professional_name
        context["modal_description"] = ui_text(
            self.request,
            "Turno semanal, dados da sucursal e integração com a agenda clínica.",
            "Weekly shift, branch details, and clinical calendar integration.",
        )
        context["linked_medico"] = self.object.linked_medico
        context["next_shift_date"] = self.object.next_occurrence_date(today)
        context["is_on_duty_today"] = self.object.applies_to_date(today)
        context["today_appointments"] = today_appointments
        context["upcoming_appointments"] = upcoming_appointments
        return context


class WorkScheduleCreateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, CreateView):
    model = HorarioTrabalho
    form_class = WorkScheduleBatchCreateForm
    template_name = "clinic/schedules/form.html"
    modal_template_name = "clinic/schedules/modal_form.html"
    success_url = reverse_lazy("clinic:work_schedule_list")
    permission_required = "clinic.add_horariotrabalho"
    segment = "schedules"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Novo horário", "New schedule")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Registe o horário base de médicos, enfermeiros e outros colaboradores por sucursal.",
            "Register the base schedule of doctors, nurses, and other collaborators by branch.",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form")
        context["form_title"] = ui_text(self.request, "Criar horário", "Create schedule")
        context["form_description"] = ui_text(
            self.request,
            "Crie um ou vários blocos semanais de uma vez, com opção de ajustar horas diferentes por dia.",
            "Create one or multiple weekly blocks at once, with the option to adjust different hours by day.",
        )
        context["submit_label"] = ui_text(self.request, "Guardar horários", "Save schedules")
        context["cancel_url"] = reverse("clinic:work_schedule_list")
        context["schedule_form_mode"] = "batch_create"
        context["weekday_override_groups"] = form.get_weekday_override_groups() if form else []
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Horário criado com sucesso.", "Schedule created successfully.")

    def form_valid(self, form):
        created_schedules = form.save()
        self.object = created_schedules[0] if created_schedules else None
        total_created = len(created_schedules)
        message = (
            ui_text(
                self.request,
                "%(count)s horário criado com sucesso.",
                "%(count)s schedule created successfully.",
            )
            if total_created == 1
            else ui_text(
                self.request,
                "%(count)s horários criados com sucesso.",
                "%(count)s schedules created successfully.",
            )
        ) % {"count": total_created}

        if self.is_modal():
            return JsonResponse(
                {
                    "success": True,
                    "message": message,
                    "reload": True,
                }
            )

        messages.success(self.request, message)
        return redirect(self.get_success_url())


class WorkScheduleUpdateView(AppPermissionMixin, ModalFormMixin, ClinicPageMixin, UpdateView):
    form_class = WorkScheduleForm
    template_name = "clinic/schedules/form.html"
    modal_template_name = "clinic/schedules/modal_form.html"
    success_url = reverse_lazy("clinic:work_schedule_list")
    permission_required = "clinic.change_horariotrabalho"
    segment = "schedules"

    def get_page_title(self) -> str:
        return ui_text(self.request, "Editar horário", "Edit schedule")

    def get_page_subtitle(self) -> str:
        return ui_text(
            self.request,
            "Ajuste dias, intervalos e regras do turno sem perder o histórico da equipa.",
            "Adjust days, intervals, and shift rules without losing team history.",
        )

    def get_queryset(self):
        return work_schedule_queryset()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_title"] = ui_text(self.request, "Editar horário", "Edit schedule")
        context["form_description"] = ui_text(
            self.request,
            "Actualize a escala do profissional seleccionado e mantenha a sincronização preparada para agendamentos.",
            "Update the selected professional's roster and keep synchronization ready for appointments.",
        )
        context["submit_label"] = ui_text(self.request, "Actualizar horário", "Update schedule")
        context["cancel_url"] = reverse("clinic:work_schedule_detail", args=[self.object.pk])
        context["wide_fields"] = "notes"
        context["schedule_form_mode"] = "single_update"
        return context

    def get_success_message(self) -> str:
        return ui_text(self.request, "Horário actualizado com sucesso.", "Schedule updated successfully.")


class WorkScheduleToggleStatusView(AppPermissionMixin, View):
    permission_required = "clinic.change_horariotrabalho"
    login_url = "clinic:login"

    @transaction.atomic
    def post(self, request, pk):
        schedule = get_object_or_404(work_schedule_queryset(), pk=pk)
        schedule.is_active = not schedule.is_active
        schedule.save(update_fields=["is_active", "updated_at"])

        return JsonResponse(
            {
                "success": True,
                "message": ui_text(
                    request,
                    "Horário de %(professional)s %(status)s com sucesso.",
                    "Schedule for %(professional)s %(status)s successfully.",
                )
                % {
                    "professional": schedule.professional_name,
                    "status": ui_text(
                        request,
                        "activado" if schedule.is_active else "desactivado",
                        "activated" if schedule.is_active else "deactivated",
                    ),
                },
                "redirect_url": reverse("clinic:work_schedule_list"),
            }
        )

