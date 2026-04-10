from django.contrib import admin

from .models import Branch, Clinic, MeasurementUnit, PaymentMethod, SystemPreference, UserProfile


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "is_active", "updated_at")
    search_fields = ("name", "legal_name", "city", "email")
    list_filter = ("is_active", "city")


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("name", "clinic", "code", "city", "is_active", "updated_at")
    search_fields = ("name", "clinic__name", "code", "city", "email")
    list_filter = ("clinic", "is_active", "city")


@admin.register(SystemPreference)
class SystemPreferenceAdmin(admin.ModelAdmin):
    list_display = ("default_language", "default_currency", "vat_rate", "updated_at")


@admin.register(MeasurementUnit)
class MeasurementUnitAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "abbreviation", "sort_order", "is_active")
    search_fields = ("code", "name", "abbreviation")
    list_filter = ("is_active",)


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "category", "provider", "sort_order", "is_active")
    search_fields = ("name", "code", "provider")
    list_filter = ("category", "is_active")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "preferred_language", "default_branch", "updated_at")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email")
    list_filter = ("preferred_language", "default_branch", "updated_at")
    filter_horizontal = ("assigned_branches",)
