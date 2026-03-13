# Dermatology Clinic Agent

> 🩺 Assistente conversacional para consultório de dermatologia, com cadastro de pacientes, teleorientação, agenda integrada ao Google Calendar e confirmação por e-mail.

---

## ✨ Visão Geral

Este projeto foi desenhado para funcionar como um atendente digital de um consultório de dermatologia.

Ele recebe a mensagem do paciente, entende o contexto da conversa, valida ou cria o cadastro, conduz uma etapa inicial de telemedicina com apoio de base de conhecimento e, quando necessário, agenda a consulta.

### O que ele faz hoje

- 🧾 valida ou cria cadastro de pacientes em Postgres
- 🧠 conduz teleorientação dermatológica com apoio de RAG
- 📚 consulta uma base dermatológica vetorial em ChromaDB
- 📅 agenda consultas no Google Calendar
- 🔁 remarca e cancela consultas no Google Calendar
- 📬 envia e-mail ao paciente com resumo do atendimento e dados do agendamento
- ⏰ dispara lembretes automáticos (D-1 e D-0) e checagem pós-consulta por e-mail
- 🧵 persiste histórico e estado da conversa por `thread_id`

---

## 🗺️ Jornada do Paciente

```text
Paciente
   ↓
Root Agent
   ↓
Registry Agent
   ↓
Telemedicine Agent
   ↓
Scheduler Agent
   ↓
Notification Agent
```

### Fluxo principal

1. O paciente envia uma mensagem.
2. O `root_agent` entende em que etapa a conversa está.
3. O `registry_agent` verifica se o paciente já existe na base.
4. O `telemedicine_agent` conversa sobre sintomas e tenta orientar.
5. Se orientação não for suficiente, pergunta se o paciente deseja prosseguir para o agendamento.
6. O `scheduler_agent` consulta a agenda e cria o evento no Google Calendar.
7. O `notification_agent` envia o e-mail de confirmação.

---

## 💡 Funcionalidades

### 🧾 Cadastro de pacientes

- busca por `CPF`
- reutiliza cadastro existente
- cria novo cadastro com:
  - `nome`
  - `idade`
  - `sexo`
  - `email`
  - `celular`
  - `CPF`

### 🩺 Telemedicina dermatológica

- pergunta sobre sintomas, dor, coceira, localização e evolução
- consulta a base de conhecimento dermatológica
- responde com mais profundidade antes de sugerir consulta
- sempre oferece:
  - continuar ajudando
  - ou prosseguir para agendamento

### 📅 Agendamento

- interpreta data e horário enviados pelo paciente
- normaliza o horário para o formato esperado pela integração
- consulta disponibilidade real no Google Calendar
- cria o evento quando o slot está livre
- sugere horários alternativos quando necessário
- remarca consulta existente a partir de CPF + nome
- cancela consulta existente a partir de CPF + nome

### 📬 E-mail de confirmação

- envia resumo do atendimento
- inclui dados do agendamento
- usa SMTP, incluindo Gmail com senha de app
- inclui endpoint para disparo de lembretes de consulta e checagem de no-show

---

## 🧱 Arquitetura

| Camada | Responsabilidade |
|---|---|
| `main.py` | API FastAPI e ciclo de vida |
| `graph.py` | orquestração LangGraph |
| `state.py` | estado compartilhado |
| `agents/root_agent.py` | roteamento determinístico |
| `agents/registry_agent.py` | cadastro e consulta de pacientes |
| `agents/telemedicine_agent.py` | teleorientação dermatológica |
| `agents/scheduler_agent.py` | agenda e integração com Google Calendar |
| `agents/notification_agent.py` | envio de confirmação por e-mail |
| `services/patient_registry.py` | persistência de pacientes |
| `services/knowledge_base.py` | busca RAG no ChromaDB |
| `services/google_calendar.py` | integração com agenda |
| `services/email_service.py` | envio SMTP |
| `scripts/build_derm_kb.py` | ingestão da base dermatológica |

---

## 🧠 Base de Conhecimento Dermatológica

Os documentos ficam em `pdfs/` e são processados para uma collection persistente no ChromaDB.

### Estratégia de ingestão

- 📄 extração página a página com `pypdf`
- 🧩 detecção heurística de seções
- ✂️ chunks com alvo de `1400` caracteres
- 🔁 overlap de `220` caracteres
- 🏷️ metadados por arquivo, seção, páginas e tamanho

### Geração da base

```bash
/opt/miniconda3/envs/clinic/bin/python scripts/build_derm_kb.py --reset
```

Arquivos gerados:

- `chromadb/`
- `kb_manifest.json`

---

## ⚙️ Variáveis de Ambiente

### Infraestrutura

- `DB_PATH`
- `DB_PATH_CADASTROS`
- `LOG_LEVEL`

### AWS / modelo

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION`
- `AWS_DEFAULT_REGION`

### Google Calendar

- `GOOGLE_CLIENT_SECRET_FILE`
- `GOOGLE_TOKEN_FILE`
- `GOOGLE_CALENDAR_ID`
- `GOOGLE_TIMEZONE`

### ChromaDB

- `CHROMA_DB_PATH`
- `CHROMA_COLLECTION_NAME`
- `CHROMA_EMBEDDING_PROVIDER`

### E-mail

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `SMTP_FROM_NAME`
- `SMTP_USE_TLS`

### Exemplo para Gmail

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=seuemail@gmail.com
SMTP_PASSWORD=sua_senha_de_app
SMTP_FROM_EMAIL=seuemail@gmail.com
SMTP_FROM_NAME=Consultorio de Dermatologia
SMTP_USE_TLS=true
```

---

## 🚀 Execução Local

### Instalar dependências

```bash
/opt/miniconda3/envs/clinic/bin/python -m pip install -r requirements.txt
```

### Subir a API

```bash
/opt/miniconda3/envs/clinic/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8001
```

### Healthcheck

```bash
curl http://localhost:8001/health
```

### Exemplo de requisição

```bash
curl -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -d '{
    "query":"Gostaria de agendar uma consulta para o dia 25/03/2026 as 15h. Meu nome é Ana Silva, feminino, 34 anos, CPF 12345678909, telefone 11999998888, email ana@email.com",
    "thread_id":"demo-thread-1"
  }'
```

### Disparo de lembretes

```bash
curl -X POST http://localhost:8001/appointments/reminders
```

---

## 🔌 Endpoints

- `GET /health`
- `GET /patients`
- `GET /patients/{cpf}`
- `POST /query`
- `POST /appointments/reminders`

---

## 🛡️ Observações

- A teleorientação não substitui avaliação médica presencial.
- Urgências clínicas devem ser encaminhadas para atendimento médico imediato.
- O WhatsApp ainda não está conectado a um webhook real.
- O fluxo de remarcação e cancelamento ainda pode evoluir.

---

## 🎯 Resumo

Este projeto já funciona como uma base sólida para um assistente de dermatologia com:

- atendimento conversacional
- cadastro persistente
- telemedicina com RAG
- agenda real no Google Calendar
- e-mail automático de confirmação

Se a próxima etapa for produto, os caminhos mais naturais são integração com WhatsApp, templates de comunicação e painel operacional para a clínica.
