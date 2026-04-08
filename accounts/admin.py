from django.contrib import admin

from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "preferred_language", "updated_at")
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email")
    list_filter = ("preferred_language", "updated_at")

