# FinDoc Retriever — RAG de Excel y PDF

Motor de ingesta y recuperación semántica para workbooks `.xlsx`/`.xlsm` y documentos
`.pdf`. Extrae hojas, celdas, fórmulas y referencias cruzadas (Excel) o texto, tablas
financieras y notas/secciones numeradas (PDF, con fallback OCR para páginas
escaneadas), indexa todo (datos estructurados + embeddings vectoriales) y expone una
API para consultarlo en lenguaje natural con citación exacta (documento, hoja, celda
— o página y tabla/nota, para PDF).

Incluye ingesta de Excel (hojas, celdas, fórmulas, referencias cruzadas), un pipeline
de ingesta de PDF (texto nativo + OCR, extracción de tablas financieras y de
notas/secciones numeradas), un motor de QA con respuestas citadas y contexto
conversacional (ID-HU-BE-008 / FE-001), y un frontend React con chat + panel de
verificación de fuente (ID-HU-FE-001 / FE-002).

## Levantar el entorno

```bash
cp .env.example .env
# por defecto EMBEDDING_PROVIDER=openai, así que hay que completar OPENAI_API_KEY
# con una key real (proveedores sin endpoint de embeddings, p. ej. DeepSeek, no
# sirven aquí). Para no depender de una API externa, cambiá a
# EMBEDDING_PROVIDER=local (sentence-transformers, corre en el propio contenedor).
docker compose up --build
```

Esto levanta Postgres (con la extensión `pgvector`), la API FastAPI en
`http://localhost:8000` y el frontend en `http://localhost:5173`.
Las migraciones de Alembic se aplican automáticamente al arrancar el contenedor `api`.
La imagen de la API también instala `tesseract-ocr` (paquetes de idioma `eng` y `spa`),
necesario para el fallback de OCR sobre páginas de PDF escaneadas. Los archivos
originales subidos se persisten en un volumen Docker (`document_storage`), para poder
servirlos de nuevo desde el panel de verificación de fuente del frontend.

## Variables de entorno (`.env`)

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | Cadena de conexión a Postgres |
| `EMBEDDING_PROVIDER` | `openai` o `local` — proveedor de embeddings activo |
| `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_EMBEDDING_DIMENSIONS` | Config del proveedor `openai` |
| `LOCAL_EMBEDDING_MODEL`, `LOCAL_EMBEDDING_DIMENSIONS` | Config del proveedor `local` (sentence-transformers, corre en el mismo contenedor, sin llamadas externas) |
| `RETRIEVAL_TOP_K` | Cantidad de chunks devueltos por consulta |
| `PDF_CHUNK_SIZE`, `PDF_CHUNK_OVERLAP` | Tamaño (caracteres) y solape de la ventana deslizante usada para trocear el texto de cada página PDF |
| `PDF_OCR_ENABLED` | Si es `false`, las páginas sin texto nativo se marcan como fallidas en vez de pasar por OCR |
| `PDF_OCR_DPI` | Resolución con la que se renderiza cada página a imagen antes de correr Tesseract |
| `PDF_OCR_LANGUAGE` | Idiomas de Tesseract, formato `eng+spa` |
| `PDF_OCR_MIN_NATIVE_CHARS` | Umbral (caracteres no-espacio) de texto nativo por debajo del cual una página se considera "sin texto útil" y se manda a OCR. En 1 solo hace OCR a páginas puramente imagen; subirlo ayuda con PDFs cuya capa de texto está rota |
| `PDF_OCR_LOW_CONFIDENCE_THRESHOLD` | (sin variable de entorno en `.env.example`, valor por defecto `0.70` en `config.py`) Confianza promedio por palabra de Tesseract por debajo de la cual el texto OCR de una página se etiqueta `"low confidence — verify against scan"` |
| `CORS_ALLOWED_ORIGINS_RAW` | Orígenes permitidos para llamar a la API, separados por coma (el frontend dev server, etc.) |
| `DOCUMENT_STORAGE_DIR` | Directorio (montado como volumen Docker) donde se persisten los archivos originales subidos, para "abrir archivo original" y el visor de PDF |
| `VITE_API_URL` | URL de la API que usa el frontend para sus llamadas |
| `FRONTEND_PORT` | Puerto host publicado para el frontend (por defecto `5173`) |

**Cambiar de proveedor de embeddings requiere re-indexar**: la dimensión del vector queda
fija en la tabla `chunks` desde la migración inicial (según `EMBEDDING_PROVIDER` en el
momento de migrar). Para comparar `openai` vs `local`, lo más simple es usar bases de datos
(o esquemas) separados por proveedor.

## Pipeline de ingesta de PDF

1. **Texto por página**: se intenta extracción nativa (`pypdf`) primero. Si una
   página no tiene texto útil (menos de `PDF_OCR_MIN_NATIVE_CHARS` caracteres) y
   `PDF_OCR_ENABLED=true`, se renderiza a imagen con PyMuPDF y se corre Tesseract
   OCR sobre ella. Cada página queda etiquetada con su `extraction_method`
   (`native`/`ocr`) y una `confidence` (1.0 para nativo; confianza promedio por
   palabra de Tesseract para OCR). Si la confianza de OCR cae por debajo de
   `PDF_OCR_LOW_CONFIDENCE_THRESHOLD`, la página se etiqueta
   `"low confidence — verify against scan"` y se agrega un warning.
2. **Tablas financieras**: por cada página se corre el detector de tablas de
   PyMuPDF (estrategia `text`, pensada para tablas sin bordes como los estados
   financieros). Se detecta la fila de encabezado real aunque haya varias filas de
   título/subtítulo encima, se recupera la etiqueta completa de filas indentadas
   que el grid de columnas hubiera truncado, se fusionan años partidos en dos
   celdas (p. ej. `"20"`/`"26"`) y se descartan columnas vacías (ruido del grid).
   Cada tabla queda con un score de confianza (`high-confidence structure` /
   `low-confidence structure`) calculado a partir de consistencia de ancho de
   fila, densidad de celdas no vacías y proporción de celdas numéricas. Si una
   página parece tener una tabla financiera pero no se pudo estructurar de forma
   confiable, se conserva el texto crudo de la página y se agrega un warning.
3. **Notas y secciones numeradas**: se detectan líneas tipo `Note 12: ...`,
   `Nota 3.- ...`, `Section 4.2 ...` o encabezados numerados sin prefijo
   (`4.2 Revenue recognition`, `12. Leases`), deduplicando por
   `(tipo, número)` y prefiriendo la ocurrencia con título más específico
   cuando el mismo número aparece dos veces (p. ej. `"Note 1"` y luego
   `"1. Accounting policies"`).
4. **Chunking**: el texto de cada página se trocea con una ventana deslizante
   (`PDF_CHUNK_SIZE`/`PDF_CHUNK_OVERLAP`, por palabras); cada fila de tabla se
   convierte en un chunk `table_row` propio (`"Financial table page N, row
   'Revenue': 2026=850000; 2025=790000"`); cada nota/sección se convierte en un
   chunk `reference`. Los chunks de PDF heredan `extraction_method`,
   `confidence` y `confidence_label` de la página de la que provienen.

## API

- `POST /ingest` — sube un `.xlsx`/`.xlsm` o un `.pdf` (multipart `file`), lo parsea,
  indexa y devuelve el estado del documento (`ready`/`failed`) junto con `doc_type`
  (`excel`/`pdf`), su versión (ver [Control de versiones](#control-de-versiones-id-hu-be-015))
  y los warnings de ingesta. Campo multipart opcional `version_of` (id de un
  documento existente) para registrarlo como nueva versión de ese documento
  aunque el nombre no siga la convención `_vN`:
  - Excel: macros, enlaces externos, hojas protegidas, referencias circulares.
  - PDF: páginas sin texto extraíble ni OCR utilizable, páginas OCR de baja
    confianza, tablas detectadas con estructura de baja confianza, y PDFs
    cifrados (se intenta descifrar con contraseña vacía; si requiere contraseña
    real, se marca como `failed` con un warning explicativo y no se extrae nada).
- `POST /query` — `{"question": "...", "top_k": <opcional>}` → chunks más
  relevantes. El backend aplica filtrado de confidencialidad antes del `LIMIT`;
  el rol/permisos se recibe temporalmente mediante `X-User-Role` (puede contener
  varios permisos separados por coma). cada uno con `document_filename` y, según el tipo de contenido,
  `sheet_name`+`cell_range` (Excel), `page_number` (PDF), `content_type`
  (`text`/`table_row`/`reference`), `reference_number` (para chunks de
  notas/secciones), y `extraction_method`/`confidence`/`confidence_label`
  (`native`/`ocr` y su confianza, para chunks de PDF).
- `GET /documents/{document_id}/pdf-structure` — para un documento `pdf`, devuelve
  todas sus tablas financieras (`headers`, `rows`, `confidence`,
  `confidence_label`, `raw_text`) y todas sus notas/secciones detectadas
  (`reference_type`, `reference_number`, `title`, `text`), ordenadas por página.
  Responde `400` si el documento no es un PDF y `404` si no existe.
- `POST /query/answer` (ID-HU-BE-008/FE-001) — `{"question": "...", "top_k":
  <opcional>, "session_id": <opcional>}` → respuesta citada y fundamentada
  (`statements[]`, cada uno con `citations[]`), `grounded` (`false` = "no
  encontré una respuesta confiable"), y `session_id` — pasalo de vuelta en la
  siguiente pregunta para mantener contexto conversacional (resolución de
  pronombres/referencias implícitas tipo "¿y el trimestre pasado?").
  `confidence`, `needs_clarification`/`clarification_question`/`clarification_options`
  y `escalation` vienen de ID-HU-BE-009 — ver
  [Confianza, ambigüedad y escalamiento](#confianza-ambigüedad-y-escalamiento-id-hu-be-009).
  `versions_used[]` indica qué versión de cada documento citado se usó;
  `document_id` (opcional en el request) limita la pregunta a una versión
  específica — ver [Control de versiones](#control-de-versiones-id-hu-be-015).

### Confianza, ambigüedad y escalamiento (ID-HU-BE-009)

El pipeline de QA (`qa/graph.py`) es `triage -> (clarify | generate -> ground_and_validate)`:

- **Triage** (una llamada corta al LLM antes de generar): reporta *hechos* sobre
  las fuentes recuperadas — si contienen la métrica pedida, para qué entidades y
  periodos aparece, si la pregunta (con su historial) ya especifica entidad o
  periodo, si pide un juicio profesional (p. ej. un tratamiento contable) y el
  tema. **La decisión de ambigüedad la toma el código** (`clarification_for`):
  si la métrica aparece para más de una entidad o periodo y la pregunta no dice
  cuál, la respuesta es `needs_clarification=true` con `clarification_question`
  y hasta 6 `clarification_options` sacadas de las fuentes, en vez de adivinar.
  Una métrica que no está en las fuentes nunca es "ambigua". Si el triage falla,
  se responde igual (sin esos chequeos).
- **Sin respuesta confiable:** `grounded=false` y ninguna cifra. Además de
  descartar afirmaciones sin cita válida, se descarta toda afirmación con una
  cifra que **no aparece en la fuente que cita** (tolera redondeo, formatos
  `1,234.5`/`1.234,5` y "3.4 millones"; ignora años y números < 100 sin decimales).
- **`confidence`** (`level` high/medium/low, `score` 0–1, `reasons[]`) para cada
  respuesta, a partir de: afirmaciones descartadas, cifras no verificadas,
  autoconfianza del modelo, si la pregunta pide un juicio profesional, y la
  relevancia de las fuentes citadas cuando el retrieval provea `score` (ver
  [handoff de BE-007](docs/HANDOFF_BE-007_BE-010.md)).
- **`escalation`** (`team`, `topic`, `reason`) cuando no hay respuesta o la
  confianza es baja. El mapeo tema → equipo está en `app/escalation.py`
  (`TOPIC_TEAMS`, editable): reconocimiento de ingresos / contabilidad /
  consolidación → Accounting/Consolidation, impuestos → Tax, caja/FX/deuda →
  Treasury, nómina → HR/Compensation, litigios → Legal, presupuesto → FP&A, y
  el resto → Financial Reporting. Solo se *sugiere* el equipo; crear el ticket
  es ID-HU-INT-008.

### Control de versiones (ID-HU-BE-015)

Cada documento pertenece a un **grupo de versiones**, derivado del nombre del
archivo: `Budget_v1.xlsx`, `Budget_v2.xlsx` y `Budget.xlsx` son versiones del
mismo documento (`excel:budget`). Se reconocen sufijos `_v2`, ` v2.1`, `-ver3`,
`_version 4` y ` (1)`; un periodo distinto (`Budget_2025_v1.xlsx`) es otro
documento, y un PDF nunca se agrupa con un Excel. Para nombres que no siguen la
convención, usar `version_of` en `POST /ingest` o `POST /documents/{id}/supersede`.

Cada subida entra como la versión más reciente de su grupo (`version_number`),
en estado `draft`. La **versión por defecto** del grupo (`is_current=true`) es:
- la versión **aprobada** más reciente, o
- si no hay ninguna aprobada, la versión más reciente (así un documento recién
  subido y sin versiones previas se puede consultar de inmediato).

Una versión nueva **no reemplaza** a una aprobada hasta que se aprueba: mientras
tanto queda como "pendiente de aprobación". Al aprobarla pasa a ser la versión
por defecto y la anterior queda `superseded`, enlazada a su sucesora vía
`superseded_by_id`. Las versiones anteriores nunca se borran automáticamente.

`/query` y `/query/answer` solo usan la versión por defecto de cada grupo (el
filtro se aplica antes del `LIMIT`). Para auditoría, `document_id` en el request
limita la búsqueda a esa versión específica, aunque esté superada; la respuesta
lo indica en `versions_used[]` (`is_current=false`, `current_version_filename`).

Endpoints:
- `POST /documents/{document_id}/approve` — aprueba una versión. Requiere un rol
  aprobador en `X-User-Role` (`reviewer`, `restricted-reviewer`,
  `financial-controller`/`controller`, `admin`); si no, `403`. `400` si el
  documento no terminó de procesarse (`ready`).
- `GET /documents/{document_id}/versions` — todas las versiones del grupo, de la
  más reciente a la más antigua, incluidas las superadas.
- Al borrar la versión por defecto (`DELETE /documents/{id}`), el grupo vuelve a
  la siguiente según la misma regla.

Los documentos existentes antes de la migración `0012_version_control` quedan
como `approved` (ya se estaban usando para responder).

### Comparación entre documentos (ID-HU-FE-003)

- `POST /compare` — `{"document_ids": [...2 a 6...], "metric": "net income", "period": <opcional>}`
  (respeta `X-User-Role`). El primer documento es la **base**. Por cada
  documento se extrae la cifra (`app/figures/extraction.py::extract_figure`):
  búsqueda solo dentro de ese documento, el LLM indica qué fuente tiene la cifra
  y la copia tal como está escrita, y **el código verifica que ese número
  aparezca en la fuente** — si no, la fila sale como "no encontrada", nunca
  inventada. Una escala ("en miles", "in millions") solo se aplica si el texto
  del documento la menciona. Respuesta: `rows[]` (valor, moneda, periodo,
  etiqueta, cita, confianza, variación absoluta y % contra la base),
  `reconciles`, `across_periods`, `exchange_rates[]`, `notes[]` y `escalation`.
  - **Mismo periodo** (p. ej. utilidad neta en el estado de resultados vs. el
    flujo de caja del Q1): se evalúa si concilian, con tolerancia relativa
    `COMPARISON_TOLERANCE` (default `0.001` = 0.1 %, absorbe redondeo pero marca
    4,820,000 vs. 4,795,000). Si no concilian → `reconciles=false` y se sugiere
    escalar a Accounting/Consolidation.
  - **Periodos distintos** (p. ej. ingresos 2024 vs. 2025): solo variación,
    `across_periods=true`, sin veredicto de conciliación ni escalamiento.
  - **Monedas distintas**: se muestran los valores originales y se busca una
    **tasa de cambio documentada** en el corpus (con su cita); si existe, se
    convierte a la moneda de la base; si no, se avisa y no se convierte (nunca
    se usa una tasa "de mercado").
- `POST /compare/export.xlsx` — recibe el resultado de `/compare` tal como se
  muestra en pantalla (no lo recalcula) y devuelve un Excel con valores,
  variaciones, citas, tasas de cambio, notas y la sugerencia de escalamiento.
  La exportación a PDF es la impresión del navegador ("Save as PDF"), con una
  hoja de estilos de impresión que deja solo la tabla.

### Confidentialidad y control de acceso

Los documentos y chunks nuevos se clasifican como `restricted` por defecto.
Las etiquetas soportadas son `public`, `internal`, `restricted` y
`restricted:<categoria>` (por ejemplo `restricted:compensation`,
`restricted:related-parties` o `restricted:legal-contingencies`).

El permiso requerido para una etiqueta categorizada es `<categoria>-access`.
Por ejemplo, `restricted:compensation` requiere `compensation-access`.
`admin` tiene acceso a todos los niveles. Un analista normal puede consultar
contenido `public` e `internal`, pero no contenido `restricted`.

El filtrado se ejecuta en la capa de recuperación antes de entregar fuentes al
modelo. Si una consulta tiene coincidencias relevantes que fueron excluidas por
confidencialidad, `/query/answer` añade el aviso:
`Part of the relevant information is restricted and was excluded from this answer.`

Endpoints de clasificación:
- `PATCH /documents/{document_id}/confidentiality` — clasifica el documento y
  sus chunks actuales. Ejemplo: `{"tag": "internal"}` o
  `{"tag": "restricted:compensation"}`.
- `PATCH /documents/{document_id}/chunks/{chunk_id}/confidentiality` — permite
  sobreescribir la clasificación de una sección/chunk individual.

- `GET /documents/{document_id}/resolve` (ID-HU-FE-002) — antes de mostrar la
  fuente de una citación: si el documento fue superado por una versión más
  reciente (`is_superseded` + `current_version`, la versión por defecto de su
  grupo; una versión nueva pendiente de aprobación no cuenta como superada) y si
  el usuario tiene acceso
  (`access.allowed`/`access.reason`, según `confidentiality_tag` y el header
  `X-User-Role` y la política de permisos de confidencialidad.
- `GET /documents/{document_id}/cells/{sheet_name}/{address}` — valor, fórmula
  y `formula_references` de una celda citada. Respeta el mismo control de
  acceso que `/resolve`.
- `GET /documents/{document_id}/file` — sirve el archivo original ("abrir
  archivo original" y el visor de PDF). `404` si nunca se persistió, `403` si
  el documento está `restricted` y el rol no alcanza. Acepta `?role=` como
  alternativa al header (el navegador carga esta URL directo en iframe/link).
- `POST /documents/{document_id}/supersede` — `{"new_document_id": "..."}`,
  enlaza explícitamente una versión vieja con la nueva cuando los nombres no lo
  revelan: el documento pasa al grupo de la nueva, justo antes de ella. Cuál
  queda por defecto sigue la regla de aprobación (si la vieja está aprobada y la
  nueva no, hay que aprobar la nueva).
- `GET /health` — chequeo básico.

Documentación interactiva en `http://localhost:8000/docs`.

## Frontend

React + Vite (TypeScript) en `frontend/`, servido en `http://localhost:5173` vía el
servicio `frontend` de `docker-compose.yml` (hot-reload con polling — necesario en
Docker Desktop sobre Windows, donde los bind mounts no reenvían eventos `inotify`).

- **Chat (ID-HU-FE-001)**: `frontend/src/components/Chat.tsx` — input de
  preguntas, historial de la conversación, badges de citación clicables,
  estados de "sin respuesta confiable"/aclaración/error/timeout, y contexto
  conversacional (reenvía `session_id` en cada pregunta de seguimiento).
- **Source Verification Panel (ID-HU-FE-002)**:
  `frontend/src/components/SourceVerificationPanel.tsx` — se abre al hacer
  clic en una citación; layout side-by-side con el chat. Muestra hoja/celda y,
  si la celda citada es una sola (no un rango), su valor y fórmula en vivo;
  para PDF intenta incrustar la página real del archivo original; banner de
  advertencia con botón para cambiar a la versión vigente si el documento fue
  superado; bloqueo con mensaje de permisos si está `restricted`; botón para
  abrir el archivo original.
- **Comparación (ID-HU-FE-003)**: pestaña "Compare",
  `frontend/src/components/ComparisonView.tsx` — selector de 2 a 6 documentos
  (el primero seleccionado es la base), métrica con sugerencias y periodo
  opcional; tabla lado a lado con valor, valor comparado, variación absoluta y
  %, y la cita de cada valor (abre el panel de fuente); las filas que no
  concilian se resaltan en rojo con el equipo sugerido; botones "Export to
  Excel" y "Save as PDF".

## Probar con un PDF de ejemplo

```bash
curl -X POST http://localhost:8000/ingest -F "file=@informe_q3.pdf"
curl -X POST http://localhost:8000/query -H "Content-Type: application/json" \
  -d '{"question": "¿Cuál fue el ingreso del Q3?"}'
```

La respuesta de `/query` incluirá `page_number` en cada chunk citando la página del
PDF de la que proviene el texto (no aplica `sheet_name`/`cell_range`, que quedan
`null` para documentos PDF), más `content_type` (`text`, `table_row` o
`reference`) y `extraction_method`/`confidence` para saber si esa página vino de
texto nativo o de OCR. Para ver las tablas y notas detectadas en el PDF como
estructuras completas (no como chunks sueltos):

```bash
curl http://localhost:8000/documents/<document_id>/pdf-structure
```

## Probar con un archivo Excel de ejemplo

Los `.xlsx` no se versionan en el repo (ver `.gitignore`) — son datos, no código. Para
generar un workbook de prueba con hojas, fórmulas cruzadas, un named range y una celda
combinada:

```bash
docker compose exec api python -c "
import openpyxl
from openpyxl.workbook.defined_name import DefinedName

wb = openpyxl.Workbook()
assumptions = wb.active
assumptions.title = 'Assumptions'
assumptions['A1'] = 'Assumption'; assumptions['B1'] = 'Value'
assumptions['A2'] = 'Tax Rate'; assumptions['B2'] = 0.21
wb.defined_names['TaxRate'] = DefinedName('TaxRate', attr_text='Assumptions!\$B\$2')

income = wb.create_sheet('Income Statement')
income['A1'] = 'Q3 2024 Income Statement'; income.merge_cells('A1:C1')
income['A2'] = 'Line Item'; income['B2'] = 'Amount'
income['A3'] = 'Revenue'; income['B3'] = 850000
income['A4'] = 'EBITDA'; income['B4'] = 500000
income['A5'] = 'Tax'; income['B5'] = '=B4*TaxRate'
income['A6'] = 'Net Income'; income['B6'] = '=B4-B5'

cashflow = wb.create_sheet('Cash Flow')
cashflow['A1'] = 'Line Item'; cashflow['B1'] = 'Amount'
cashflow['A2'] = 'EBITDA'; cashflow['B2'] = \"='Income Statement'!B4\"
cashflow['A3'] = 'Capex'; cashflow['B3'] = 120000
cashflow['A4'] = 'Free Cash Flow'; cashflow['B4'] = '=B2-B3'

wb.save('/tmp/sample_financials.xlsx')
"
docker compose cp api:/tmp/sample_financials.xlsx ./sample_financials.xlsx

curl -X POST http://localhost:8000/ingest -F "file=@sample_financials.xlsx"
curl -X POST http://localhost:8000/query -H "Content-Type: application/json" \
  -d '{"question": "What is the Free Cash Flow and how does it relate to EBITDA?"}'
```

O usa cualquier `.xlsx` propio arrastrándolo en `http://localhost:8000/docs` (endpoint `POST /ingest`).

## Tests

Backend:

```bash
docker compose exec api pytest
```

- `test_excel_parser.py`, `test_pdf_parser.py`, `test_chunker.py`, `test_pdf_chunker.py`
  no requieren base de datos. Los de PDF generan sus propios archivos `.pdf`
  mínimos en `tests/pdf_helpers.py`, sin depender de una librería de autoría de
  PDFs; los OCR/PyMuPDF se prueban con `monkeypatch` sobre `_ocr_pdf_page` en vez
  de correr Tesseract de verdad.
- `test_retrieval.py` y `test_retrieval_pdf.py` son tests de integración que
  necesitan Postgres/pgvector accesible (se saltan automáticamente si no lo está)
  y usan un proveedor de embeddings falso y determinista (en `tests/conftest.py`)
  para no depender de una API externa ni descargar modelos.
- `test_qa_generation.py` prueba el pipeline LangGraph de generación de
  respuestas (`qa/graph.py`) con un chat model falso vía `monkeypatch` —
  fundamentación, citas multi-documento, valores en conflicto.
- `test_conversation.py` (integración, requiere Postgres) verifica que una
  pregunta de seguimiento recibe el turno anterior como contexto en el prompt.
- `test_document_resolution.py` (integración, requiere Postgres) cubre
  ID-HU-FE-002: lectura de celda/fórmula, supersesión de versión, acceso
  restringido y descarga del archivo original.

Frontend:

```bash
docker compose exec frontend npm test
```

Vitest + React Testing Library. Cubre `ChatInput`, `CitationBadge`,
`AnswerMessage` (estados fundamentado/sin respuesta/aclaración/conflicto),
`Chat` (flujo completo con la API mockeada, incluido el manejo de errores de
red) y `SourceVerificationPanel` (celda con fórmula, rango multi-celda,
versión superada, acceso restringido).

## Estructura

```
backend/app/
  config.py               # settings desde .env (incluye toda la config de OCR)
  storage.py                # persistencia de archivos originales en disco
  db/models.py             # Document, Sheet, Cell, NamedRange, PdfTable, PdfReference, Chunk,
                            # ConversationSession, ConversationMessage
  versioning.py             # ID-HU-BE-015: grupos de versiones, aprobación, versión por defecto
  escalation.py             # ID-HU-BE-009: tema -> equipo de escalamiento (lo reutiliza FE-003)
  figures/
    extraction.py           # ID-HU-FE-003: extract_figure (compartido con BE-010) + tasas de cambio documentadas
    comparison.py             # variaciones, conciliación, monedas (aritmética determinística)
    export.py                   # Excel de la comparación
  schemas.py                # DTOs Pydantic de la API (incluye PdfStructureResponse, AnswerResponse,
                             # DocumentResolutionOut, CellOut)
  ingestion/
    excel_parser.py          # extracción con openpyxl
    pdf_parser.py              # texto nativo (pypdf) + OCR fallback (PyMuPDF/Tesseract),
                                # extracción de tablas financieras y de notas/secciones
    chunker.py                   # arma chunks embebibles con metadata de citación
                                  # (Excel: filas de hoja; PDF: texto, table_row, reference)
    service.py                     # orquesta parseo -> persistencia -> embeddings
  embeddings/                # proveedor intercambiable (openai / local)
  retrieval/                  # búsqueda por similitud en pgvector
  qa/
    graph.py                   # pipeline LangGraph: triage -> (clarify | generate -> ground_and_validate)
    confidence.py                # ID-HU-BE-009: verificación de cifras + puntaje de confianza
    prompts.py                   # system prompt, prompt de triage + construcción del prompt (incluye historial)
    conversation.py                # sesión/historial de conversación (ID-HU-FE-001)
    service.py                       # orquesta retrieval -> QA graph -> persistencia del turno
  api/
    routes_ingest.py           # POST /ingest
    routes_query.py              # POST /query, POST /query/answer, GET /documents/{id}/pdf-structure
    routes_compare.py              # POST /compare, POST /compare/export.xlsx
    routes_documents.py            # GET /documents/{id}/resolve, /cells/{sheet}/{address}, /file,
                                    # /versions, POST /approve, /supersede, PATCH /confidentiality

frontend/src/
  api/
    client.ts               # fetch wrappers + manejo de timeout/errores
    types.ts                  # tipos espejo de los DTOs del backend
  components/
    Chat.tsx                 # ID-HU-FE-001: input, historial, estados de respuesta
    AnswerMessage.tsx, CitationBadge.tsx, ChatInput.tsx
    SourceVerificationPanel.tsx  # ID-HU-FE-002: visor side-by-side
  App.tsx                   # layout: chat + panel de fuente
```
## Confidentiality authorization and audit (ID-HU-BE)

Restricted retrieval is permission-aware and auditable. `X-User-Role` accepts one or more comma-separated roles/permissions. Built-in role mappings include `analyst`, `financial-controller`, `controller`, `restricted-reviewer`, and `admin`; explicit permissions such as `compensation-access` and `legal-access` remain supported.

For example:

```text
X-User-Id: controller-42
X-User-Role: compensation-access
```

A user with `compensation-access` can retrieve `restricted:compensation` but not `restricted:legal-contingencies`. A `financial-controller` is mapped to compensation, related-party, and legal-contingency access. Retrieved sources, including authorized restricted sources, are persisted in `retrieval_audit_logs` with user identity, roles, document/chunk IDs, confidentiality tier, question, authorization status, and timestamp.

The audit table intentionally stores document/chunk IDs without foreign keys so audit history survives deletion of the source content.

