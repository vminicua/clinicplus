# Referência funcional do projecto Clinic+

Fonte principal: `C:\Users\valdi\Downloads\Clinic++ Estrutura do Sistema.pdf`

Este ficheiro resume a referência usada para orientar o desenvolvimento do projecto. A ideia é manter aqui os pontos que já guiam as decisões técnicas, para podermos consultar rapidamente sem depender de releitura completa do PDF.

## Direcção geral

- Sistema web interno, multiutilizador e multi-sucursal.
- Stack de referência: Django com base relacional e arquitectura preparada para crescer por módulos.
- Idiomas previstos: Português e English.
- O MVP deve começar por autenticação/perfis, sucursais, pacientes, staff e agendamentos.

## Módulo actual em foco

Segundo o PDF, o módulo de autenticação e utilizadores deve cobrir:

- login e logout
- recuperação de senha
- gestão de utilizadores
- grupos/perfis e permissões
- associação do utilizador à sucursal
- selecção de idioma

## Perfis recomendados no documento

- Administrador do Sistema
- Gestor da Clínica / Sucursal
- Recepcionista
- Médico
- Enfermeiro(a)
- Farmacêutico / Responsável de Stock
- Laboratorista / Técnico
- Financeiro / Caixa
- Auditor / Direcção

## Regras de negócio relevantes para esta fase

- Um utilizador pode ter perfil específico e acesso restrito.
- O controlo de acesso deve ser feito por perfil e permissões.
- A troca de idioma deve afectar a interface do utilizador.
- A arquitectura deve ficar pronta para expansão multi-sucursal.

## Estrutura técnica recomendada pelo documento

Apps sugeridas:

- `accounts`
- `branches`
- `patients`
- `staff`
- `appointments`
- `triage`
- `consultations`
- `medical_records`
- `laboratory`
- `billing`
- `inventory`
- `reports`
- `audit`
- `settingsapp`

## Decisão aplicada nesta entrega

Para alinhar o código com a referência, a primeira implementação foi aberta em `accounts`, cobrindo:

- CRUD de utilizadores
- CRUD de perfis/roles
- gestão de permissões
- idioma preferido do utilizador com foco inicial em Português de Moçambique

## Nota de idioma

Por enquanto, a interface nova deve ser escrita em Português de Moçambique. A base para English fica preparada mais à frente, sem forçar tradução completa nesta fase.
