from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from django.urls import reverse

from clinic.forms import PatientForm
from clinic.models import Agendamento, Consulta, Hospital, Medico, Paciente


class PatientFormTests(TestCase):
    def setUp(self):
        self.hospital = Hospital.objects.create(
            name="Clinic Plus Central",
            email="central@clinic.test",
            phone="840000000",
            address="Av. da Saúde",
            city="Maputo",
            state="Maputo",
            zip_code="1100",
        )

    def test_patient_form_creates_linked_inactive_user(self):
        form = PatientForm(
            data={
                "first_name": "Ana",
                "last_name": "Mabote",
                "email": "ana@example.com",
                "hospital": self.hospital.pk,
                "cpf": "AB-12345",
                "date_of_birth": "1995-02-10",
                "gender": "F",
                "phone": "840111111",
                "address": "Bairro Central",
                "city": "Maputo",
                "state": "Maputo",
                "zip_code": "1100",
                "emergency_contact": "Carlos Mabote",
                "emergency_phone": "840222222",
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
        self.assertFalse(patient.user.is_active)


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
        self.user = User.objects.create_user(username="gestor", password="123456")
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_paciente"),
            Permission.objects.get(codename="delete_paciente"),
            Permission.objects.get(codename="view_consulta"),
            Permission.objects.get(codename="change_paciente"),
        )
        self.client.force_login(self.user)

    def create_patient(self, document="DOC12345"):
        patient_user = User.objects.create(username=f"user_{document.lower()}", first_name="Maria", last_name="Silva")
        return Paciente.objects.create(
            user=patient_user,
            hospital=self.hospital,
            cpf=document,
            phone="840333333",
            date_of_birth="1990-01-01",
            gender="F",
            address="Rua 1",
            city="Maputo",
            state="Maputo",
            zip_code="1100",
            emergency_contact="José Silva",
            emergency_phone="840444444",
            medical_history="Sem observações.",
            allergies="",
        )

    def test_delete_patient_without_history(self):
        patient = self.create_patient("DEL12345")

        response = self.client.post(reverse("clinic:patient_delete", args=[patient.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Paciente.objects.filter(pk=patient.pk).exists())
        self.assertFalse(User.objects.filter(username="user_del12345").exists())

    def test_delete_patient_with_history_is_blocked(self):
        patient = self.create_patient("HIS12345")
        doctor_user = User.objects.create(username="doctor1", first_name="Joao", last_name="Medico")
        doctor = Medico.objects.create(
            user=doctor_user,
            hospital=self.hospital,
            especialidade=None,
            crm="CRM001",
            phone="840555555",
        )
        Agendamento.objects.create(
            paciente=patient,
            medico=doctor,
            hospital=self.hospital,
            data="2026-04-01",
            hora="09:00",
            motivo="Consulta de rotina",
            status="agendado",
            notas="",
        )

        response = self.client.post(reverse("clinic:patient_delete", args=[patient.pk]))
        payload = response.json()

        self.assertEqual(response.status_code, 400)
        self.assertIn("histórico clínico", payload["message"])
        self.assertTrue(Paciente.objects.filter(pk=patient.pk).exists())

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
