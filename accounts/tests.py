from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from accounts.forms import UserForm
from accounts.models import Branch, SystemPreference
from accounts.ui import LANGUAGE_SESSION_KEY, resolve_language_for_request


User = get_user_model()


class SystemPreferenceTests(TestCase):
    def test_get_solo_uses_metical_by_default(self):
        preferences = SystemPreference.get_solo()

        self.assertEqual(preferences.default_currency, "MZN")
        self.assertEqual(preferences.default_language, "pt")
        self.assertEqual(SystemPreference.objects.count(), 1)

    def test_resolve_language_prefers_user_profile_over_session(self):
        user = User.objects.create_user(username="maria", password="segredo123")
        user.profile.preferred_language = "en"
        user.profile.save()

        request = RequestFactory().get("/")
        request.user = user
        request.session = {LANGUAGE_SESSION_KEY: "pt"}

        self.assertEqual(resolve_language_for_request(request), "en")


class UserBranchValidationTests(TestCase):
    def test_default_branch_must_be_inside_assigned_branches(self):
        default_branch = Branch.objects.create(name="Maputo", code="MAP")
        other_branch = Branch.objects.create(name="Matola", code="MAT")

        form = UserForm(
            data={
                "username": "joao",
                "first_name": "Joao",
                "last_name": "Mucavele",
                "email": "joao@example.com",
                "password1": "SenhaSegura123",
                "password2": "SenhaSegura123",
                "preferred_language": "pt",
                "is_active": "on",
                "assigned_branches": [str(other_branch.pk)],
                "default_branch": str(default_branch.pk),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("default_branch", form.errors)
