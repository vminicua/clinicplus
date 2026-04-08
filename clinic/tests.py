from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Branch, SystemPreference
from clinic.forms import PatientForm
from clinic.models import Agendamento, Consulta, Hospital, Medico, Paciente


class PatientFormTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name="Clinic Plus Central",
            code="CPC",
            city="Maputo",
        )

    def test_patient_form_creates_linked_active_user_with_optional_postal_and_emergency_phone(self):
        form = PatientForm(
            data={
                "first_name": "Ana",
                "last_name": "Mabote",
                "email": "ana@example.com",
                "branch": self.branch.pk,
                "cpf": "AB-12345",
                "date_of_birth": "1995-02-10",
                "gender": "F",
                "phone": "840111111",
                "address": "Bairro Central",
                "city": "Maputo",
                "country": "Moçambique",
                "state": "Maputo",
                "emergency_contact": "Carlos Mabote",
                "allergies": "Pólen",
                "medical_history": "Sem antecedentes graves.",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        patient = form.save()

        self.assertEqual(patient.user.first_name, "Ana")
        self.assertEqual(patient.user.last_name, "Mabote")
        self.assertEqual(patient.user.username, "pac_ab12345")
        self.assertFalse(patient.user.has_usable_password())
        self.assertTrue(patient.user.is_active)
        self.assertEqual(patient.branch, self.branch)
        self.assertIsNone(patient.hospital)
        self.assertEqual(patient.country, "Moçambique")
        self.assertEqual(patient.zip_code, "")
        self.assertEqual(patient.emergency_phone, "")


class PatientViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.hospital = Hospital.objects.create(
            name="Clinic Plus Central",
            email="central@clinic.test",
            phone="840000000",
            address="Av. da Saúde",
            city="Maputo",
            state="Maputo",
            zip_code="1100",
        )
        self.branch = Branch.objects.create(name="Clinic Plus Baixa", code="CPB", city="Maputo")
        self.user = User.objects.create_user(username="gestor", password="123456")
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_paciente"),
            Permission.objects.get(codename="view_consulta"),
            Permission.objects.get(codename="change_paciente"),
        )
        self.client.force_login(self.user)
        self.preferences = SystemPreference.get_solo()
        self.preferences.patient_code_prefix = "TEST000"
        self.preferences.save(update_fields=["patient_code_prefix", "updated_at"])

    def create_patient(self, document="DOC12345"):
        patient_user = User.objects.create(username=f"user_{document.lower()}", first_name="Maria", last_name="Silva")
        return Paciente.objects.create(
            user=patient_user,
            branch=self.branch,
            cpf=document,
            phone="840333333",
            date_of_birth="1990-01-01",
            gender="F",
            address="Rua 1",
            city="Maputo",
            country="Moçambique",
            state="Maputo",
            zip_code="",
            emergency_contact="José Silva",
            emergency_phone="",
            medical_history="Sem observações.",
            allergies="",
        )

    def test_toggle_patient_to_inactive(self):
        patient = self.create_patient("STAT1234")

        response = self.client.post(reverse("clinic:patient_toggle_status", args=[patient.pk]))

        self.assertEqual(response.status_code, 200)
        patient.refresh_from_db()
        patient.user.refresh_from_db()
        self.assertFalse(patient.is_active)
        self.assertFalse(patient.user.is_active)

    def test_toggle_patient_back_to_active(self):
        patient = self.create_patient("ACT12345")
        patient.is_active = False
        patient.save(update_fields=["is_active"])
        patient.user.is_active = False
        patient.user.save(update_fields=["is_active"])

        response = self.client.post(reverse("clinic:patient_toggle_status", args=[patient.pk]))

        self.assertEqual(response.status_code, 200)
        patient.refresh_from_db()
        patient.user.refresh_from_db()
        self.assertTrue(patient.is_active)
        self.assertTrue(patient.user.is_active)

    def test_history_detail_view_renders_consultation_data(self):
        patient = self.create_patient("CONS1234")
        doctor_user = User.objects.create(username="doctor2", first_name="Paulo", last_name="Clínico")
        doctor = Medico.objects.create(
            user=doctor_user,
            hospital=self.hospital,
            especialidade=None,
            crm="CRM002",
            phone="840666666",
        )
        appointment = Agendamento.objects.create(
            paciente=patient,
            medico=doctor,
            hospital=self.hospital,
            data="2026-04-02",
            hora="10:00",
            motivo="Febre persistente",
            status="concluido",
            notas="Paciente orientado a retornar se necessário.",
        )
        Consulta.objects.create(
            agendamento=appointment,
            diagnostico="Infecção respiratória ligeira",
            prescricao="Paracetamol e repouso",
            notas_medico="Monitorar evolução por 48 horas.",
        )

        response = self.client.get(reverse("clinic:patient_history_detail", args=[patient.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Infecção respiratória ligeira")
        self.assertContains(response, patient.full_name)

    def test_patient_list_shows_prefixed_id(self):
        patient = self.create_patient("LIST1234")

        response = self.client.get(reverse("clinic:patient_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.preferences.format_patient_code(patient.pk))

    def test_patient_pdf_download_uses_weasyprint(self):
        patient = self.create_patient("PDF12345")

        response = self.client.get(reverse("clinic:patient_pdf", args=[patient.pk]))
        pdf_bytes = b"".join(response.streaming_content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(self.preferences.format_patient_code(patient.pk).lower(), response["Content-Disposition"])
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
