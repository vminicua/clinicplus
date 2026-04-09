from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Branch, SystemPreference
from clinic.forms import AppointmentForm, DepartmentForm, MedicationForm, PatientForm, SpecialtyForm, WorkScheduleBatchCreateForm, WorkScheduleForm
from clinic.models import Agendamento, Consulta, Departamento, Especialidade, HorarioTrabalho, Hospital, Medicamento, Medico, Paciente


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
            Permission.objects.get(codename="add_consulta"),
            Permission.objects.get(codename="change_consulta"),
            Permission.objects.get(codename="view_agendamento"),
            Permission.objects.get(codename="add_agendamento"),
            Permission.objects.get(codename="change_paciente"),
            Permission.objects.get(codename="view_horariotrabalho"),
            Permission.objects.get(codename="add_horariotrabalho"),
            Permission.objects.get(codename="change_horariotrabalho"),
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
            branch=self.branch,
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

    def test_work_schedule_list_view_renders_schedule_and_sync_info(self):
        today = timezone.localdate()
        doctor_user = User.objects.create(username="doctor_schedule", first_name="Cláudia", last_name="Mucavele")
        doctor = Medico.objects.create(
            user=doctor_user,
            hospital=self.hospital,
            especialidade=None,
            crm="CRM100",
            phone="840777777",
        )
        patient = self.create_patient("SCHD1234")
        schedule = HorarioTrabalho.objects.create(
            user=doctor_user,
            branch=self.branch,
            role=HorarioTrabalho.RoleChoices.MEDICO,
            weekday=today.weekday(),
            start_time="08:00",
            end_time="12:00",
            slot_minutes=30,
            valid_from=today.replace(day=1),
            accepts_appointments=True,
        )
        Agendamento.objects.create(
            paciente=patient,
            medico=doctor,
            branch=self.branch,
            hospital=self.hospital,
            data=today,
            hora="08:30",
            motivo="Consulta de revisão",
        )

        response = self.client.get(reverse("clinic:work_schedule_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, schedule.professional_name)
        self.assertContains(response, "CRM100")
        self.assertContains(response, self.branch.name)

    def test_appointment_list_view_renders_bookings_and_appointments_menu(self):
        today = timezone.localdate()
        patient = self.create_patient("BOOK1234")
        doctor_user = User.objects.create(username="doctor_booking", first_name="Lina", last_name="Matola")
        doctor = Medico.objects.create(
            user=doctor_user,
            hospital=self.hospital,
            especialidade=None,
            crm="CRM200",
            phone="840888888",
        )
        appointment = Agendamento.objects.create(
            paciente=patient,
            medico=doctor,
            branch=self.branch,
            hospital=self.hospital,
            data=today,
            hora="09:00",
            motivo="Consulta de rotina",
            status="agendado",
        )

        response = self.client.get(reverse("clinic:appointment_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consultas")
        self.assertContains(response, reverse("clinic:appointment_list"))
        self.assertContains(response, reverse("clinic:appointment_agenda"))
        self.assertContains(response, reverse("clinic:work_schedule_list"))
        self.assertContains(response, reverse("clinic:appointment_create"))
        self.assertContains(response, appointment.paciente.full_name)
        self.assertContains(response, "Consulta de rotina")

    def test_appointment_create_view_creates_booking_with_doctor_hospital(self):
        today = timezone.localdate()
        patient = self.create_patient("NEWB1234")
        doctor_user = User.objects.create(username="doctor_new_booking", first_name="Tânia", last_name="Mussa")
        doctor = Medico.objects.create(
            user=doctor_user,
            hospital=self.hospital,
            especialidade=None,
            crm="CRM210",
            phone="840121212",
        )
        HorarioTrabalho.objects.create(
            user=doctor_user,
            branch=self.branch,
            role=HorarioTrabalho.RoleChoices.MEDICO,
            weekday=today.weekday(),
            start_time="08:00",
            end_time="17:00",
            slot_minutes=30,
            valid_from=today.replace(day=1),
            accepts_appointments=True,
        )

        response = self.client.post(
            reverse("clinic:appointment_create"),
            data={
                "paciente": patient.pk,
                "doctor_user": doctor_user.pk,
                "branch": self.branch.pk,
                "data": today.isoformat(),
                "hora": "13:00",
                "motivo": "Exame de controlo",
                "status": "agendado",
                "notas": "Trazer resultados anteriores.",
            },
        )

        self.assertEqual(response.status_code, 302)
        appointment = Agendamento.objects.get(medico=doctor, paciente=patient, data=today, hora="13:00")
        self.assertEqual(appointment.branch, self.branch)
        self.assertEqual(appointment.hospital, self.hospital)
        self.assertEqual(appointment.motivo, "Exame de controlo")

    def test_appointment_agenda_view_renders_selected_professional_week(self):
        today = timezone.localdate()
        patient = self.create_patient("AGEN1234")
        doctor_user = User.objects.create(username="doctor_agenda", first_name="Joel", last_name="Muianga")
        doctor = Medico.objects.create(
            user=doctor_user,
            hospital=self.hospital,
            especialidade=None,
            crm="CRM201",
            phone="840999999",
        )
        HorarioTrabalho.objects.create(
            user=doctor_user,
            branch=self.branch,
            role=HorarioTrabalho.RoleChoices.MEDICO,
            weekday=today.weekday(),
            start_time="08:00",
            end_time="12:00",
            slot_minutes=30,
            valid_from=today.replace(day=1),
            accepts_appointments=True,
        )
        appointment = Agendamento.objects.create(
            paciente=patient,
            medico=doctor,
            branch=self.branch,
            hospital=self.hospital,
            data=today,
            hora="11:00",
            motivo="Retorno clínico",
            status="agendado",
        )

        response = self.client.get(
            reverse("clinic:appointment_agenda"),
            {"professional": doctor_user.pk, "date": today.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Agenda semanal")
        self.assertContains(response, doctor_user.get_full_name())
        self.assertContains(response, appointment.paciente.full_name)
        self.assertContains(response, "Retorno clínico")
        self.assertContains(response, "08:00 - 12:00")

    def test_appointment_consultation_view_creates_consultation_and_closes_booking(self):
        today = timezone.localdate()
        patient = self.create_patient("CONS5678")
        doctor_user = User.objects.create(username="doctor_consult", first_name="Berta", last_name="Maloa")
        doctor = Medico.objects.create(
            user=doctor_user,
            hospital=self.hospital,
            especialidade=None,
            crm="CRM230",
            phone="840232323",
        )
        appointment = Agendamento.objects.create(
            paciente=patient,
            medico=doctor,
            branch=self.branch,
            hospital=self.hospital,
            data=today,
            hora="14:00",
            motivo="Consulta clínica",
            status="agendado",
        )

        response = self.client.post(
            reverse("clinic:appointment_consultation", args=[appointment.pk]),
            data={
                "diagnostico": "Gripe simples",
                "prescricao": "Repouso e hidratação",
                "notas_medico": "Rever em 3 dias se persistir.",
            },
        )

        self.assertEqual(response.status_code, 302)
        appointment.refresh_from_db()
        consultation = Consulta.objects.get(agendamento=appointment)
        self.assertEqual(appointment.status, "concluido")
        self.assertEqual(consultation.diagnostico, "Gripe simples")

    def test_toggle_work_schedule_to_inactive(self):
        staff_user = User.objects.create(username="staff_schedule", first_name="Rui", last_name="Mabunda")
        schedule = HorarioTrabalho.objects.create(
            user=staff_user,
            branch=self.branch,
            role=HorarioTrabalho.RoleChoices.ENFERMEIRO,
            weekday=2,
            start_time="07:00",
            end_time="15:00",
            slot_minutes=20,
            valid_from="2026-04-01",
            is_active=True,
        )

        response = self.client.post(reverse("clinic:work_schedule_toggle_status", args=[schedule.pk]))

        self.assertEqual(response.status_code, 200)
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)

    def test_work_schedule_create_view_builds_multiple_days_with_override(self):
        staff_user = User.objects.create(username="multi_schedule", first_name="Marta", last_name="Cuamba")

        response = self.client.post(
            reverse("clinic:work_schedule_create"),
            data={
                "user": staff_user.pk,
                "branch": self.branch.pk,
                "role": HorarioTrabalho.RoleChoices.ENFERMEIRO,
                "shift_name": "Escala inteligente",
                "weekdays": ["0", "2", "4"],
                "start_time": "08:00",
                "end_time": "16:00",
                "break_start": "",
                "break_end": "",
                "customize_day_hours": "on",
                "wednesday_start_time": "10:00",
                "wednesday_end_time": "18:00",
                "valid_from": "2026-04-01",
                "valid_until": "",
                "accepts_appointments": "on",
                "notes": "Cobertura semanal",
            },
        )

        self.assertEqual(response.status_code, 302)
        created = HorarioTrabalho.objects.filter(user=staff_user).order_by("weekday")
        self.assertEqual(created.count(), 3)
        self.assertEqual(created[0].start_time.strftime("%H:%M"), "08:00")
        self.assertEqual(created[1].weekday, HorarioTrabalho.WeekdayChoices.WEDNESDAY)
        self.assertEqual(created[1].start_time.strftime("%H:%M"), "10:00")
        self.assertEqual(created[1].end_time.strftime("%H:%M"), "18:00")


class WorkScheduleFormTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(
            name="Clinic Plus Horários",
            code="CPH",
            city="Maputo",
        )
        self.staff_user = User.objects.create(username="nurse1", first_name="Marta", last_name="Chongo")

    def test_work_schedule_form_rejects_overlapping_active_shift(self):
        HorarioTrabalho.objects.create(
            user=self.staff_user,
            branch=self.branch,
            role=HorarioTrabalho.RoleChoices.ENFERMEIRO,
            weekday=1,
            start_time="08:00",
            end_time="12:00",
            slot_minutes=30,
            valid_from="2026-04-01",
            is_active=True,
        )

        form = WorkScheduleForm(
            data={
                "user": self.staff_user.pk,
                "branch": self.branch.pk,
                "role": HorarioTrabalho.RoleChoices.ENFERMEIRO,
                "shift_name": "Turno da manhã",
                "weekday": 1,
                "start_time": "10:00",
                "end_time": "14:00",
                "break_start": "",
                "break_end": "",
                "slot_minutes": 30,
                "valid_from": "2026-04-01",
                "valid_until": "",
                "accepts_appointments": "",
                "is_active": "on",
                "notes": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("start_time", form.errors)

    def test_batch_create_form_creates_multiple_days_and_uses_base_hours_as_fallback(self):
        form = WorkScheduleBatchCreateForm(
            data={
                "user": self.staff_user.pk,
                "branch": self.branch.pk,
                "role": HorarioTrabalho.RoleChoices.ENFERMEIRO,
                "shift_name": "Turno rotativo",
                "weekdays": ["1", "3"],
                "start_time": "08:00",
                "end_time": "14:00",
                "break_start": "",
                "break_end": "",
                "customize_day_hours": "on",
                "thursday_start_time": "12:00",
                "thursday_end_time": "18:00",
                "valid_from": "2026-04-01",
                "valid_until": "",
                "accepts_appointments": "",
                "notes": "Observação interna",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        created = form.save()

        self.assertEqual(len(created), 2)
        self.assertEqual(created[0].weekday, HorarioTrabalho.WeekdayChoices.TUESDAY)
        self.assertEqual(created[0].start_time.strftime("%H:%M"), "08:00")
        self.assertEqual(created[1].weekday, HorarioTrabalho.WeekdayChoices.THURSDAY)
        self.assertEqual(created[1].start_time.strftime("%H:%M"), "12:00")


class AppointmentFormTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Clinic Plus App", code="CPA", city="Maputo")
        self.hospital = Hospital.objects.create(
            name="Clinic Plus Appointments",
            email="appointments@clinic.test",
            phone="840101010",
            address="Av. Agenda",
            city="Maputo",
            state="Maputo",
            zip_code="1100",
        )
        patient_user = User.objects.create(username="patient_form", first_name="Lídia", last_name="Mabjaia")
        self.patient = Paciente.objects.create(
            user=patient_user,
            branch=self.branch,
            cpf="FORM1234",
            phone="840202020",
            date_of_birth="1992-05-10",
            gender="F",
            address="Rua 3",
            city="Maputo",
            country="Moçambique",
            state="Maputo",
            zip_code="",
            emergency_contact="Contacto",
            emergency_phone="",
            medical_history="",
            allergies="",
        )
        doctor_user = User.objects.create(username="doctor_form", first_name="Arnaldo", last_name="Tembe")
        self.doctor_user = doctor_user
        self.doctor = Medico.objects.create(
            user=doctor_user,
            hospital=self.hospital,
            especialidade=None,
            crm="CRM300",
            phone="840303030",
        )
        HorarioTrabalho.objects.create(
            user=doctor_user,
            branch=self.branch,
            role=HorarioTrabalho.RoleChoices.MEDICO,
            weekday=2,
            start_time="09:00",
            end_time="12:00",
            slot_minutes=30,
            valid_from="2026-04-01",
            accepts_appointments=True,
        )

    def test_appointment_form_medico_field_lists_active_doctors(self):
        form = AppointmentForm()

        self.assertIn(self.doctor.user, form.fields["doctor_user"].queryset)

    def test_appointment_form_assigns_hospital_from_doctor(self):
        form = AppointmentForm(
            data={
                "paciente": self.patient.pk,
                "doctor_user": self.doctor.user.pk,
                "branch": self.branch.pk,
                "data": "2026-04-15",
                "hora": "09:30",
                "motivo": "Consulta geral",
                "status": "agendado",
                "notas": "",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        appointment = form.save()

        self.assertEqual(appointment.branch, self.branch)
        self.assertEqual(appointment.hospital, self.hospital)

    def test_appointment_form_rejects_duplicate_doctor_slot(self):
        Agendamento.objects.create(
            paciente=self.patient,
            medico=self.doctor,
            branch=self.branch,
            hospital=self.hospital,
            data="2026-04-15",
            hora="10:00",
            motivo="Primeira consulta",
        )

        form = AppointmentForm(
            data={
                "paciente": self.patient.pk,
                "doctor_user": self.doctor.user.pk,
                "branch": self.branch.pk,
                "data": "2026-04-15",
                "hora": "10:00",
                "motivo": "Consulta duplicada",
                "status": "agendado",
                "notas": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("hora", form.errors)

    def test_appointment_form_rejects_branch_without_active_schedule(self):
        other_branch = Branch.objects.create(name="Clinic Plus Sul", code="CPS", city="Matola")
        form = AppointmentForm(
            data={
                "paciente": self.patient.pk,
                "doctor_user": self.doctor_user.pk,
                "branch": other_branch.pk,
                "data": "2026-04-15",
                "hora": "09:30",
                "motivo": "Consulta sem agenda",
                "status": "agendado",
                "notas": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("branch", form.errors)

    def test_appointment_form_outside_schedule_reports_next_availability(self):
        today = timezone.localdate()
        days_until_schedule = (HorarioTrabalho.WeekdayChoices.WEDNESDAY - today.weekday()) % 7
        if days_until_schedule == 0:
            days_until_schedule = 7
        requested_date = today + timedelta(days=days_until_schedule)
        next_available_date = requested_date + timedelta(days=7)

        form = AppointmentForm(
            data={
                "paciente": self.patient.pk,
                "doctor_user": self.doctor_user.pk,
                "branch": self.branch.pk,
                "data": requested_date.isoformat(),
                "hora": "13:00",
                "motivo": "Consulta fora do horário",
                "status": "agendado",
                "notas": "",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("hora", form.errors)
        self.assertIn("Próxima disponibilidade", form.errors["hora"][0])
        self.assertIn(next_available_date.strftime("%d/%m/%Y"), form.errors["hora"][0])
        self.assertIn("09:00", form.errors["hora"][0])


class ClinicalStructureTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.branch = Branch.objects.create(name="Clinic Plus Estrutura", code="CPE", city="Maputo")
        self.user = User.objects.create_user(username="estrutura", password="123456")
        self.user.user_permissions.add(
            Permission.objects.get(codename="view_especialidade"),
            Permission.objects.get(codename="add_especialidade"),
            Permission.objects.get(codename="change_especialidade"),
            Permission.objects.get(codename="view_departamento"),
            Permission.objects.get(codename="add_departamento"),
            Permission.objects.get(codename="change_departamento"),
            Permission.objects.get(codename="view_medicamento"),
            Permission.objects.get(codename="add_medicamento"),
            Permission.objects.get(codename="change_medicamento"),
        )
        self.client.force_login(self.user)

    def test_specialty_form_creates_specialty(self):
        form = SpecialtyForm(
            data={
                "name": "Ginecologista",
                "description": "Saúde da mulher.",
                "icon": "female",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        specialty = form.save()
        self.assertEqual(specialty.name, "Ginecologista")

    def test_department_form_creates_department_with_responsavel(self):
        specialty = Especialidade.objects.create(name="Ginecologista")
        doctor_user = User.objects.create(username="doc_struct", first_name="Ana", last_name="Mussa")
        doctor = Medico.objects.create(
            user=doctor_user,
            hospital=None,
            especialidade=specialty,
            crm="CRM-DEP-1",
            phone="840888111",
        )
        HorarioTrabalho.objects.create(
            user=doctor_user,
            branch=self.branch,
            role=HorarioTrabalho.RoleChoices.MEDICO,
            weekday=2,
            start_time="08:00",
            end_time="12:00",
            slot_minutes=30,
            valid_from="2026-04-01",
            accepts_appointments=True,
        )

        form = DepartmentForm(
            data={
                "name": "Ginecologia",
                "branch": self.branch.pk,
                "responsavel_user": doctor_user.pk,
                "descricao": "Serviço de ginecologia.",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        department = form.save()
        doctor.refresh_from_db()
        self.assertEqual(department.responsavel, doctor)
        self.assertEqual(department.branch, self.branch)

    def test_specialty_and_department_lists_render(self):
        specialty = Especialidade.objects.create(name="Pediatra")
        department = Departamento.objects.create(name="Pediatria", branch=self.branch)

        specialty_response = self.client.get(reverse("clinic:specialty_list"))
        department_response = self.client.get(reverse("clinic:department_list"))

        self.assertEqual(specialty_response.status_code, 200)
        self.assertContains(specialty_response, specialty.name)
        self.assertEqual(department_response.status_code, 200)
        self.assertContains(department_response, department.name)

    def test_medication_form_creates_medication(self):
        form = MedicationForm(
            data={
                "name": "Paracetamol",
                "principio_ativo": "Paracetamol",
                "dosagem": "500 mg",
                "quantidade": 24,
                "preco": "12.50",
                "descricao": "Analgésico de referência.",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        medication = form.save()
        self.assertEqual(medication.name, "Paracetamol")

    def test_medication_list_renders(self):
        medication = Medicamento.objects.create(
            name="Ibuprofeno",
            principio_ativo="Ibuprofeno",
            dosagem="400 mg",
            quantidade=12,
            preco="15.00",
        )

        response = self.client.get(reverse("clinic:medication_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, medication.name)
