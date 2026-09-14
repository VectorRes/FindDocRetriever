# Avance: ID-HU-FE-001 y ID-HU-FE-002

**Fecha:** 2026-09-14
**Alcance:** Interfaz de consulta en lenguaje natural con respuestas citadas
(FE-001) y Panel de verificación de fuente (FE-002), construidas sobre el motor
de RAG Excel/PDF y el motor de QA (BE-008) ya existentes.

Este documento resume qué se construyó, qué falta, y **específicamente qué
necesita saber quien implemente ID-HU-BE-009** (manejo de ambigüedad / baja
confianza) para integrarse sin fricción con lo que ya existe.

---

## 1. Qué se construyó

### Backend

- **Contexto conversacional** (`backend/app/qa/conversation.py`,
  `db/models.py::ConversationSession/ConversationMessage`, migración
  `0007_conversation_sessions`): cada pregunta puede pasar un `session_id`;
  si no se pasa, se crea una sesión nueva. Las últimas 5 réplicas se pasan al
  modelo como contexto ("CONVERSATION HISTORY") para resolver preguntas de
  seguimiento (`"¿y el trimestre pasado?"`), sin que el historial cuente como
  fuente citable — la respuesta se sigue fundamentando solo en los chunks
  recuperados en esa consulta.
- **Contrato de aclaración para BE-009** — ver sección 2 abajo.
- **Versionado y confidencialidad mínimos de `Document`** (migraciones
  `0008_doc_version_access`, `0009_document_storage_path`): campos
  `is_current`, `superseded_by_id`, `confidentiality_tag`, `storage_path`.
- **Endpoints nuevos** en `backend/app/api/routes_documents.py`:
  - `GET /documents/{id}/resolve` — versión vigente + decisión de acceso.
  - `GET /documents/{id}/cells/{sheet_name}/{address}` — valor/fórmula en vivo.
  - `GET /documents/{id}/file` — sirve el archivo original (nuevo:
    `backend/app/storage.py` + volumen Docker `document_storage`, ya que antes
    los archivos subidos se descartaban tras la ingesta).
  - `POST /documents/{id}/supersede`, `PATCH /documents/{id}/confidentiality`.
- **Endpoint existente extendido**: `POST /query/answer` ahora acepta
  `session_id` y devuelve `session_id` + los campos del contrato de BE-009.

### Frontend (nuevo — no existía antes de este trabajo)

React + Vite en `frontend/`, servicio Docker propio (`http://localhost:5173`).

- `frontend/src/components/Chat.tsx` — chat con historial, envío de
  `session_id`, estados de carga/error/timeout, badges de citación clicables.
- `frontend/src/components/AnswerMessage.tsx` — renderiza los 4 estados de una
  respuesta: fundamentada, sin respuesta confiable, conflicto entre fuentes, y
  **aclaración pendiente** (`needs_clarification`) — este último ya está listo
  para consumir lo que BE-009 produzca.
- `frontend/src/components/SourceVerificationPanel.tsx` — panel side-by-side:
  hoja/celda/fórmula para Excel, salto de página para PDF, banner de versión
  superada con botón de cambio, bloqueo por acceso restringido, botón de abrir
  archivo original.
- Tests: `backend/tests/test_conversation.py`,
  `backend/tests/test_document_resolution.py` (backend, pytest); 5 archivos de
  tests en `frontend/src/components/*.test.tsx` (Vitest + Testing Library).

Todo probado tanto con tests automatizados como manualmente en navegador real
(Docker Desktop + Playwright headless para smoke tests).

---

## 2. Lo que necesita saber quien implemente ID-HU-BE-009

**No se construyó ninguna lógica de detección de ambigüedad a propósito** —
se dejó completamente para BE-009, para no duplicar trabajo. Lo que sí se
dejó listo es el **contrato de datos** para que solo haya que llenarlo:

### Dónde vive el contrato

- `backend/app/qa/schemas.py::GroundedAnswer` — el dataclass interno del
  motor de QA. Ya tiene:
  ```python
  needs_clarification: bool = False
  clarification_question: str | None = None
  ```
- `backend/app/schemas.py::AnswerResponse` — el DTO de la API (mismo shape,
  expuesto en `POST /query/answer`).

**Hoy ambos campos son siempre `False`/`None`.** Nadie los setea.

### Qué hay que hacer en el backend

1. Implementar la lógica de detección de ambigüedad (falta de entidad/periodo,
   pregunta demasiado genérica, etc. — ver acceptance criteria de BE-009 en
   `FinDoc_Retriever_User_Stories...md`).
2. El lugar natural para integrarla es el pipeline de
   `backend/app/qa/graph.py` (LangGraph: hoy es `generate -> ground_and_validate
   -> END`). Lo más simple es agregar un nodo `detect_ambiguity` **antes** de
   `generate` (para no gastar una llamada al LLM de generación si la pregunta
   ya es ambigua), o extender `ground_and_validate_node` si la ambigüedad se
   detecta post-generación (p. ej. el modelo detecta que la pregunta aplica a
   múltiples entidades/periodos en los chunks recuperados).
3. Cuando se detecte ambigüedad, construir el `GroundedAnswer` con
   `needs_clarification=True` y `clarification_question="..."` (ej. "¿Te
   referís a la entidad X o Y? ¿Qué periodo?"), dejando `statements=[]` y
   `grounded=False` (mismo patrón que el caso "sin respuesta confiable").
4. **No hace falta tocar el frontend** — `AnswerMessage.tsx` ya renderiza este
   estado (ver `frontend/src/components/AnswerMessage.tsx`, primer `if`):
   ```tsx
   if (answer.needs_clarification) {
     return <div>{answer.clarification_question ?? "..."}</div>;
   }
   ```
   Si BE-009 devuelve un `clarification_question`, se muestra tal cual. Si
   viene vacío, hay un fallback genérico.
5. **Contexto conversacional ya existe** — si la aclaración requiere que el
   analista responda y el sistema retome la pregunta original con el filtro
   aclarado, eso ya es posible reenviando el mismo `session_id`: el historial
   de la sesión (`qa/conversation.py::get_recent_history`) se pasa al modelo
   en cada llamada.

### Qué NO hay que tocar

- El modelo de datos de `ConversationSession`/`ConversationMessage` no
  necesita cambios para BE-009.
- El contrato de `AnswerResponse`/`GroundedAnswer` ya tiene los campos — solo
  hay que empezar a poblarlos.

---

## 3. Qué queda pendiente (fuera de BE-009)

No forma parte de las historias FE-001/FE-002 tal como se interpretaron, pero
vale la pena que el equipo lo sepa:

| Pendiente | Detalle |
|---|---|
| **Resaltado visual exacto** | El panel de fuente muestra el valor/fórmula de la celda citada como tarjeta de texto, no como una hoja de cálculo renderizada con la celda resaltada. Para PDF, el visor salta a la página (`#page=N`) pero no resalta una región/línea específica — el parser no captura bounding boxes hoy. |
| **Entorno de staging** | Todo se probó en Docker local; no existe un entorno de staging para este proyecto todavía. |
| **Control de acceso real** | `X-User-Role` (header) es un placeholder — no hay autenticación/roles reales. Ver la historia BE de control de acceso en el documento de historias de usuario. |
| **Detección automática de versiones** | Subir un archivo con el mismo nombre no lo detecta como "nueva versión" automáticamente — el enlace `supersede` es manual vía `POST /documents/{id}/supersede`. |

---

## 4. Cómo correr y probar todo

```bash
cp .env.example .env   # completar OPENAI_API_KEY
docker compose up --build
```

- Frontend: `http://localhost:5173`
- API: `http://localhost:8000` (docs interactivos en `/docs`)
- Tests backend: `docker compose exec api pytest`
- Tests frontend: `docker compose exec frontend npm test`

Más detalle de endpoints, variables de entorno y estructura de carpetas en
`README.md`.
