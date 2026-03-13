# Clinic Office Agent

API multiagente para atendimento administrativo de consultorios particulares, usando a mesma base tecnica do projeto `travel-agent`: FastAPI, LangGraph, Bedrock, checkpointer e agentes especializados.

## O que o servico faz

Fluxo principal:

1. Recebe uma mensagem administrativa do paciente em `POST /query`.
2. O agente orquestrador identifica a intencao e decide quais agentes precisam atuar.
3. Os agentes especializados executam tarefas de agenda, cadastro, telemedicina e notificacoes.
4. O grafo monta a resposta final localmente.

## Arquitetura

- `main.py`: API FastAPI e ciclo de vida do grafo.
- `graph.py`: orquestracao LangGraph e checkpointer.
- `state.py`: modelos estruturados e estado compartilhado.
- `agents/root_agent.py`: agente central de roteamento.
- `agents/scheduler_agent.py`: agenda, remarcacao, cancelamento e encaixe.
- `agents/registry_agent.py`: cadastro do paciente e documentos pendentes.
- `agents/telemedicine_agent.py`: teleorientacao dermatologica com apoio de base de conhecimento.
- `agents/notification_agent.py`: confirmacoes, lembretes e follow-up.

## Variaveis de ambiente

O projeto pode reaproveitar o mesmo `.env` do repositiorio principal. As chaves mais relevantes sao:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION`
- `AWS_DEFAULT_REGION`
- `DB_PATH`
- `DB_PATH_CADASTROS`
- `LOG_LEVEL`

Se `DB_PATH` nao estiver definido, o checkpointer usa memoria.
Se `DB_PATH_CADASTROS` estiver definido, o servico cria e consulta a tabela `patients` para armazenar cadastros.
Se `SMTP_HOST`, `SMTP_USER` e `SMTP_PASSWORD` estiverem definidos, o sistema envia email ao paciente apos agendamento confirmado.

Configuracao minima para email:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `SMTP_FROM_NAME`
- `SMTP_USE_TLS`

## Execucao local

```bash
cd clinic-agent
/opt/miniconda3/envs/travel/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8001
```

Teste rapido:

```bash
curl http://localhost:8001/health
curl -X POST http://localhost:8001/query \
  -H "Content-Type: application/json" \
  -d '{"query":"Preciso remarcar minha consulta de segunda para quarta a tarde."}'
```

## Base de conhecimento dermatologica

O projeto inclui um script de ingestao para PDFs em `pdfs/`, gerando uma collection persistente no ChromaDB na raiz do projeto.

Instalacao das dependencias:

```bash
/opt/miniconda3/envs/travel/bin/python -m pip install -r requirements.txt
```

Geracao da collection:

```bash
/opt/miniconda3/envs/travel/bin/python scripts/build_derm_kb.py --reset
```

Se quiser evitar problemas com `torch`/`sentence-transformers`, mantenha o provider default do Chroma:

```bash
/opt/miniconda3/envs/travel/bin/python scripts/build_derm_kb.py --reset --embedding-provider default
```

Estrategia de ingestao:

- extracao pagina a pagina com `pypdf`
- deteccao heuristica de secoes por titulos/linhas curtas
- chunking por secao com alvo de 1400 caracteres e overlap de 220
- metadados por chunk: arquivo, secao, paginas, tamanho e estimativa de tokens

Arquivos gerados:

- `chromadb/`: banco vetorial persistente
- `kb_manifest.json`: resumo da ingestao

## Limitacoes atuais

- A integracao com WhatsApp ainda nao foi ligada a um webhook real.
- O foco da automacao e administrativo e de teleorientacao; urgencias clinicas sao tratadas como excecao.
- O fluxo usa heuristicas e fallback local quando o modelo nao estiver disponivel.
