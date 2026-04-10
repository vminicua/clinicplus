from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.models import Group
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from accounts.forms import BranchForm, ClinicForm, MeasurementUnitForm, PaymentMethodForm, UserForm
from accounts.models import Branch, Clinic, MeasurementUnit, PaymentMethod, SystemPreference
from accounts.ui import LANGUAGE_SESSION_KEY, resolve_language_for_request
from clinic.models import Departamento, Especialidade, Medico


User = get_user_model()


class SystemPreferenceTests(TestCase):
    def test_get_solo_uses_metical_by_default(self):
        preferences = SystemPreference.get_solo()

        self.assertEqual(preferences.default_currency, "MZN")
        self.assertEqual(preferences.default_language, "pt")
        self.assertEqual(preferences.vat_rate, 16)
        self.assertEqual(SystemPreference.objects.count(), 1)

    def test_resolve_language_prefers_user_profile_over_session(self):
        user = User.objects.create_user(username="maria", password="segredo123")
        user.profile.preferred_language = "en"
        user.profile.save()

        request = RequestFactory().get("/")
        request.user = user
        request.session = {LANGUAGE_SESSION_KEY: "pt"}

        self.assertEqual(resolve_language_for_request(request), "en")

    def test_measurement_unit_form_normalizes_code(self):
        form = MeasurementUnitForm(
            data={
                "code": " Caixa ",
                "name": "Caixa",
                "abbreviation": "cx",
                "description": "Unidade usada para embalagens fechadas.",
                "sort_order": 20,
                "is_active": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        unit = form.save()

        self.assertEqual(unit.code, "caixa")
        self.assertEqual(unit.abbreviation, "cx")

    def test_measurement_unit_list_renders(self):
        client = Client()
        user = User.objects.create_user(username="prefs", password="123456")
        user.user_permissions.add(
            Permission.objects.get(codename="view_measurementunit"),
        )
        MeasurementUnit.objects.create(code="frasco", name="Frasco", abbreviation="fr", sort_order=15)
        client.force_login(user)

        response = client.get(reverse("accounts:measurement_unit_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Frasco")

    def test_payment_method_form_normalizes_code(self):
        form = PaymentMethodForm(
            data={
                "code": " M-Pesa ",
                "name": "M-Pesa",
                "category": "mobile_money",
                "provider": "Vodacom M-Pesa",
                "description": "Carteira móvel.",
                "sort_order": 20,
                "is_active": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        method = form.save()

        self.assertEqual(method.code, "m-pesa")
        self.assertEqual(method.provider, "Vodacom M-Pesa")

    def test_payment_method_list_renders(self):
        client = Client()
        user = User.objects.create_user(username="prefs_pay", password="123456")
        user.user_permissions.add(
            Permission.objects.get(codename="view_paymentmethod"),
        )
        PaymentMethod.objects.create(code="cash", name="Dinheiro", category="cash", sort_order=10)
        client.force_login(user)

        response = client.get(reverse("accounts:payment_method_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dinheiro")


class UserBranchValidationTests(TestCase):
    def test_clinic_form_creates_parent_clinic(self):
        form = ClinicForm(
            data={
                "name": "Clinic Plus",
                "legal_name": "Clinic Plus, Lda",
                "nuit": "400000001",
                "city": "Maputo",
                "province": "Maputo Cidade",
                "country": "Moçambique",
                "address": "Av. Eduardo Mondlane",
                "phone": "840000001",
                "email": "geral@clinicplus.test",
                "is_active": "on",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        clinic = form.save()

        self.assertEqual(clinic.name, "Clinic Plus")
        self.assertTrue(clinic.is_active)

    def test_branch_form_prefills_single_active_clinic(self):
        clinic = Clinic.objects.create(name="Clinic Plus", is_active=True)

        form = BranchForm()

        self.assertEqual(form.fields["clinic"].initial, clinic)

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

    def test_user_form_creates_clinical_profile_for_doctor_role(self):
        branch = Branch.objects.create(name="Central", code="CEN")
        specialty = Especialidade.objects.create(name="Ginecologista")
        department = Departamento.objects.create(name="Ginecologia", branch=branch)
        doctor_role = Group.objects.create(name="Médico")

        form = UserForm(
            data={
                "username": "dra_sara",
                "first_name": "Sara",
                "last_name": "Mabunda",
                "email": "sara@example.com",
                "password1": "SenhaSegura123",
                "password2": "SenhaSegura123",
                "preferred_language": "pt",
                "is_active": "on",
                "groups": [str(doctor_role.pk)],
                "assigned_branches": [str(branch.pk)],
                "default_branch": str(branch.pk),
                "medical_specialty": str(specialty.pk),
                "medical_department": str(department.pk),
                "medical_crm": "CRM-GIN-001",
                "medical_phone": "840123456",
                "medical_bio": "Especialista em saúde da mulher.",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        clinical_profile = Medico.objects.get(user=user)

        self.assertEqual(clinical_profile.especialidade, specialty)
        self.assertEqual(clinical_profile.departamento, department)
        self.assertEqual(clinical_profile.crm, "CRM-GIN-001")

    def test_user_form_requires_doctor_role_for_clinical_profile(self):
        branch = Branch.objects.create(name="Central", code="CEN")
        specialty = Especialidade.objects.create(name="Pediatra")

        form = UserForm(
            data={
                "username": "joana",
                "first_name": "Joana",
                "last_name": "Tembe",
                "email": "joana@example.com",
                "password1": "SenhaSegura123",
                "password2": "SenhaSegura123",
                "preferred_language": "pt",
                "is_active": "on",
                "assigned_branches": [str(branch.pk)],
                "default_branch": str(branch.pk),
                "medical_specialty": str(specialty.pk),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("groups", form.errors)
