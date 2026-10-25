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
| `RETRIEVAL_MIN_SCORE` | Similitud coseno mínima (0–1) para que un chunk cuente como relevante; `0` lo desactiva. `0.25` está calibrado para `text-embedding-3-small` — ver [Recuperación de fuentes relevantes](#recuperación-de-fuentes-relevantes-id-hu-be-007) |
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
  (`native`/`ocr` y su confianza, para chunks de PDF), y `score` (similitud
  con la pregunta, de mayor a menor). Si nada es relevante, `results` es `[]`.
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
  relevancia de las fuentes citadas (`score` del retrieval; un promedio menor a
  `WEAK_RELEVANCE` = 0.3 baja la confianza).
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

### Jerarquía de fuentes y borradores (ID-HU-BE-016)

Cada documento tiene un **nivel de fuente** (`source_tier`, `app/source_tiers.py`), del más confiable al menos:

1. `erp_epm`: exportaciones del ERP/EPM, la fuente de verdad.
2. `official_report`: informes oficiales o publicados (10-K/10-Q, informe anual, estados auditados, earnings release).
3. `internal_report`: reportes gerenciales, extractos de BI, archivos de trabajo.

- **Al subir**, el nivel se deduce del nombre del archivo: `SAP_Andina_Q2_2026.xlsx` → ERP/EPM, `10-Q-Q3-2026.pdf` → informe oficial. Si no se reconoce, queda como informe interno, el nivel más bajo. La migración `0016_source_tier` aplica la misma regla a los documentos existentes.
- **Para cambiarlo:** `PATCH /documents/{id}/source-tier` con `{"tier": "erp_epm"}`. Requiere un rol aprobador (403 si no) y responde 400 si el nivel no existe. En el frontend está en Documents → "Source tier".
- **Prioridad** (`app/qa/source_hierarchy.py`): las fuentes del chat se ordenan por nivel, y el prompt muestra el nivel de cada una y pide usar la más alta. Además, en código: si el triage encontró la cifra pedida en fuentes de distinto nivel para la misma empresa y periodo, las de nivel menor se apartan antes de generar la respuesta. Así la respuesta sale del ERP/EPM aunque un reporte interno diga lo mismo.
- **Avisos** (`source_notices[]` en `POST /query/answer`, con `kind`, `message` y `document_filenames`):
  - `draft`: una fuente citada no está aprobada. *"Based on a draft document not yet approved: … Its figures are provisional, not approved figures."*
  - `lower_tier`: una fuente citada no es ERP/EPM porque ninguna fuente de nivel mayor cubría la cifra. *"… These figures haven't been reconciled against the ERP/EPM source of truth."*
  - `higher_tier_preferred`: una fuente de nivel menor también tenía la cifra pero no se usó como principal. *"Reporte_Gerencial.xlsx (internal report) also covers this figure but wasn't used as the primary source: SAP_….xlsx (ERP/EPM) ranks higher."*
- `versions_used[]` incluye el `source_tier` de cada documento, y el chat muestra los avisos como banners.

### Recuperación de fuentes relevantes (ID-HU-BE-007)

`retrieve()` (`app/retrieval/service.py`) busca los chunks (filas/celdas de
Excel, texto/tablas/notas de PDF) más cercanos a la pregunta en todos los
documentos, en la versión por defecto de cada uno:

- **Ranking:** cada fuente trae `score` (similitud coseno, `1 - distancia`) y
  vienen de mayor a menor.
- **Permisos:** solo se consideran chunks cuya etiqueta de confidencialidad
  permite el rol del usuario; el filtro corre en SQL antes del `LIMIT`, así que
  el contenido restringido nunca ocupa lugares ni llega al modelo.
- **Umbral de relevancia:** se descartan los chunks con `score` menor a
  `RETRIEVAL_MIN_SCORE` (también antes del `LIMIT`). Si nada pasa el umbral, la
  lista es vacía y `/query/answer` responde "no confident answer" sin llamar al
  modelo. Con `text-embedding-3-small` sobre los estados de ejemplo, las
  preguntas fuera de tema puntúan ≤ 0.18 y las que los documentos responden
  ≥ 0.33. Con otro modelo (p. ej. `EMBEDDING_PROVIDER=local`) hay que
  recalibrar.
- **Aviso de información restringida:** solo aparece si la coincidencia
  restringida también supera el umbral (antes salía en casi cualquier
  pregunta).
- **Varios documentos:** el chat (`retrieve_for_question`) limita a cada
  documento al 60 % de las fuentes y busca en ambos idiomas (sección
  siguiente), así que una pregunta como "¿la utilidad neta concilia con el
  punto de partida del flujo de efectivo?" trae filas del estado de resultados
  y del flujo de caja.
- La extracción de cifras de ratios/comparación (`extract_figure`) no usa el
  umbral: el documento ya está elegido y el modelo decide si la cifra está.
- **El documento en cada fragmento** (`app/ingestion/context.py`): cada
  fragmento se convierte a vector con el nombre de su documento delante
  (*"Document: Micron Technology Inc. MU Q3 2026 Report"*), porque una fila de
  un PDF a menudo no dice de qué empresa es (*"row 'Revenue': column 1=9,301"*).
  Solo cambia el vector: el texto guardado, que es el que muestran las citas y
  contra el que se verifican las cifras, queda igual. Los documentos subidos
  antes de este cambio se actualizan con
  `docker compose exec api python -m app.ingestion.reembed`.
- **Primero la empresa que nombra la pregunta**
  (`app/qa/query_expansion.py`): si la pregunta nombra una empresa que es el
  nombre de uno o más documentos, según su archivo (*"total revenues of
  Micron"* → `Micron-Technology-Inc.-…pdf`), se buscan primero en esos
  documentos y sus fragmentos llenan las fuentes. Los demás solo ocupan los
  lugares que sobran. Así, las filas *"Total revenues"* de Tesla ya no
  desplazan las de Micron.

### Lectura de tablas PDF y búsqueda del chat

- **Tablas PDF** (`app/ingestion/chunker.py`): cada valor queda rotulado con su
  columna (periodo), en inglés o español. Para eso se usan las fechas "As of
  …" / "Al 31 de diciembre de …", las corridas de años y los grupos
  ("Three Months Ended", "Tres meses terminados el …", "Year To Date",
  "Acumulado"). También:
  - se excluye la columna de notas ("Nota/Note");
  - se unen números partidos solo si el número unido aparece en la página;
  - cada fila lleva su sección.
  El corpus de formatos está en `tests/statement_pdfs.py`; para cubrir un
  formato nuevo, se agrega ahí.
- **Búsqueda del chat** (`app/qa/query_expansion.py`): cada pregunta se busca
  también en el otro idioma (`QUERY_TRANSLATION`, por defecto `true`). Sin
  documento elegido, ningún documento ocupa más del 60 % de las fuentes.

### Explicación de fórmulas y conflictos en el chat

- **Fórmulas:** si una fuente de Excel tiene fórmula, las filas que referencia
  (y los named ranges) se agregan como fuentes (`app/qa/formula_context.py`).
  Ante "¿cómo se calcula…?", la respuesta incluye el cálculo armado desde el
  propio Excel, con cada componente citado.
- **Conflictos:** solo se marcan cuando dos fuentes dan montos distintos para
  la misma entidad, periodo y tipo de cifra. Presupuesto contra real no es
  conflicto.
- **Aclaraciones:** la respuesta a una aclaración ("Andina – 2024") se combina
  con la pregunta original.

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
    También cuenta como periodo distinto el mismo rótulo con distinto lapso:
    "Q3 2026" de tres meses contra el acumulado de nueve. La extracción informa
    la columna leída (`column_header`) y lo que cubre (`period_coverage`). Si no
    coincide con el `period` pedido, baja a confianza media con el motivo.
  - **Periodo desconocido**: si no se pidió `period` y algún valor no tiene
    periodo identificable, una diferencia **no** se marca como discrepancia ni
    se escala. Se muestra la variación y una nota que pide indicar el periodo.
- **Excel con título**: el chunker toma como encabezado la primera fila con al
  menos dos valores distintos. Las filas de arriba (título, "Cifras en COP") se
  agregan como contexto a cada chunk, así cada cifra lleva su año y su moneda.
  Los documentos ingeridos antes de este cambio hay que volver a subirlos.
  - **Monedas distintas**: se muestran los valores originales y se busca una
    **tasa de cambio documentada** en el corpus (con su cita); si existe, se
    convierte a la moneda de la base; si no, se avisa y no se convierte (nunca
    se usa una tasa "de mercado").
- `POST /compare/export.xlsx` — recibe el resultado de `/compare` tal como se
  muestra en pantalla (no lo recalcula) y devuelve un Excel con valores,
  variaciones, citas, tasas de cambio, notas y la sugerencia de escalamiento.
  La exportación a PDF es la impresión del navegador ("Save as PDF"), con una
  hoja de estilos de impresión que deja solo la tabla.

### Cálculo de ratios financieros (ID-HU-BE-010)

- `GET /ratios/definitions` — la librería de fórmulas (`app/ratios/formulas.py`):
  15 ratios en cuatro categorías.
  - **Liquidez:** current, quick, cash ratio.
  - **Solvencia:** debt-to-equity, debt ratio, interest coverage.
  - **Rentabilidad:** gross / operating / EBITDA / net margin, ROA, ROE.
  - **Eficiencia:** asset, inventory, receivables turnover.
  Se usan saldos de cierre del periodo, sin promedios de apertura y cierre.
- `POST /ratios` — `{"document_ids": [...1 a 6...], "period": <opcional>, "ratios": <opcional>, "categories": <opcional>}`
  (respeta `X-User-Role`). Cada entrada de las fórmulas (utilidad neta, activos
  totales…) se busca **una sola vez entre todos los documentos elegidos** con
  `extract_figure` de FE-003, así que hereda su garantía de no inventar cifras.
  Primero se prueba el documento cuyo nombre u hojas sugieren el estado
  correcto: "EERR", "resultados" o "P&L" para el estado de resultados;
  "balance" o "situación financiera" para el balance. Así un ROA combina solo
  la utilidad neta del estado de resultados con los activos del balance.
  Cada ratio trae:
  - `value`: fracción si `unit="percent"`, múltiplo si `unit="times"`;
  - `inputs[]`: cada línea usada, con etiqueta, valor y **cita**;
  - `citations[]`, `cross_document` y `reasons[]`.
  El `status` es uno de estos:
  - `calculated`.
  - `needs_review`: el valor se calcula, pero la UI lo **retiene** hasta que el
    analista confirme que lo revisó. Pasa en cualquiera de estos casos:
    - una entrada se mapeó a una etiqueta no estándar (p. ej. "Short-Term
      Obligations" para *current liabilities*);
    - las entradas son de periodos distintos, o las del estado de resultados
      cubren lapsos distintos (tres contra nueve meses);
    - una entrada no corresponde al periodo pedido (`period_coverage` de FE-003);
    - un saldo que nunca es negativo sale negativo (p. ej. patrimonio negativo);
    - el resultado sale del rango plausible (p. ej. un margen > 100 %).
  - `not_calculable`: falta una entrada, las monedas son distintas o el
    denominador es cero. Si falta una entrada, `missing_inputs` la nombra
    (p. ej. `["Current liabilities"]`); nunca se devuelve un valor vacío ni
    un 0 (ID-HU-BE-011).
  Detalles de funcionamiento:
  - Cuando todas las entradas salen del mismo documento, el ratio usa las
    cifras tal como están escritas: la escala del documento se cancela, así que
    no importa si la extracción aplicó "en millones" solo a algunas.
  - Las entradas se extraen en paralelo (`RATIO_EXTRACTION_WORKERS`, 6 por
    defecto; cada hilo con su propia sesión de base de datos).
  - Si el LLM no responde en ninguna extracción, la respuesta es **503**. Si
    falla solo en algunas, esas entradas quedan sin dato y una nota pide
    reintentar. `/compare` se comporta igual.

### Dashboard de cifras clave y ratios (ID-HU-FE-004)

Muestra los ratios de liquidez, solvencia, rentabilidad y eficiencia de cada empresa, **periodo por periodo**, con la cita de cada cifra, la tendencia y los valores atípicos. Usa el motor de BE-010; no hay otra lógica de cálculo.

- **Catálogo** (`app/dashboard/catalog.py`, sin LLM): por cada documento vigente y procesado:
  - **Empresa:** se toma del nombre del archivo ("EERR_Andina_2024.xlsx" → Andina; "Andina" y "Andina S.A.S." son la misma).
  - **Periodos:** se toman de los encabezados de columna que rotula el chunker ("2024", "Three Months Ended June 30, 2026" → Q2 2026, "Al 31 de diciembre de 2025" → 2025). Solo años completos y trimestres: los acumulados de seis o nueve meses, el presupuesto y las variaciones no cuentan. Se usan los periodos de las filas que son líneas de los estados, así una columna auxiliar de otra hoja no aparece como periodo.
  - **Estados:** un documento tiene el estado de resultados o el balance si una de sus filas es una línea estándar de ese estado.
  - Solo se listan los documentos que el rol puede ver.
- **Resultados guardados** (`dashboard_runs` + `ratio_snapshots`, migración `0014_ratio_dashboard`):
  - "Calculate" ejecuta `calculate_ratios` por periodo, con los documentos de la empresa para ese periodo, y guarda cada ratio con sus entradas y citas.
  - Abrir el dashboard lee lo guardado: es instantáneo y no llama al LLM.
  - Un periodo queda **"outdated"** cuando sus documentos ya no son los vigentes de la empresa para ese periodo (versión nueva aprobada, documento subido o borrado).
- **Datos insuficientes:** si a la empresa le falta en un periodo el estado que un ratio necesita (por ejemplo, el balance de FY24), el ratio no se envía al motor. Queda `not_calculable` con `missing_statements: ["balance_sheet"]`, las entradas de ese estado en `missing_inputs` y el motivo *"Insufficient data: the balance sheet for 2024 hasn't been ingested for Pacifico."*. Nunca se muestra un 0.

### Ratios por periodo y tendencias (ID-HU-BE-011)

- **Guardado por periodo:** cada "Calculate" (`POST /dashboard/refresh`) guarda un `ratio_snapshot` por ratio y periodo, con su valor, entradas y citas.
- **"Not calculable":** un ratio que no se puede calcular queda `not_calculable`, con `missing_inputs` (los nombres de las entradas que faltan, p. ej. `["Current liabilities"]`) y el motivo. Se guarda en la columna `ratio_snapshots.missing_inputs` (migración `0015_ratio_missing_inputs`, que también pasa los `unavailable` ya guardados a `not_calculable`).
- **Huecos en la historia:** los trimestres o años sin documento entre el primer y el último periodo de la empresa (Q3 entre Q2 y Q4) aparecen igual, como `not_calculable` y con `periods[].gap = true`. Nunca se omiten ni se muestran como 0. Solo se rellenan los huecos entre los `DASHBOARD_MAX_PERIODS` periodos más recientes, así unos documentos de 2010 y 2025 no generan catorce años vacíos.
- `GET /ratios/trend?entity=Andina&ratio=ebitda_margin[&periods=Q1 2024&periods=Q2 2024]`: un ratio de la empresa en orden cronológico. Cada punto trae `status`, `value`, `citations[]` (las fuentes), `missing_inputs` y `reasons`. Lee lo guardado, sin llamar al LLM. Responde 400 si el ratio no existe y 404 si la empresa no existe.
- **Valores atípicos** (`app/dashboard/outliers.py`, sin LLM):
  - **Criterio:** un valor se marca si cambia más de `DASHBOARD_OUTLIER_REL_CHANGE` (50 %) respecto del periodo calculado anterior **y** además supera un mínimo absoluto: 5 puntos porcentuales en porcentajes (`DASHBOARD_OUTLIER_MIN_POINTS`) o 0.5x en múltiplos (`DASHBOARD_OUTLIER_MIN_TIMES`). Con 4 o más periodos también se usa un z-score robusto.
  - **Causas:** el ROE se descompone con DuPont (margen neto × rotación de activos × multiplicador de capital) y el ROA con margen × rotación. Así se dice si el salto vino de la rentabilidad, la eficiencia o el apalancamiento. En los demás ratios se muestra el cambio de sus entradas.
- **Acceso:** si el usuario no puede ver alguno de los documentos de un periodo calculado, ese periodo sale "restricted", sin valores, y el intento queda en la auditoría (`view_dashboard`, ID-HU-BE-014).
- **Solo cifras consolidadas:** no hay filtro por segmento (decisión del plan).

Endpoints:
- `GET /dashboard/catalog`: empresas, con sus periodos y documentos.
- `GET /dashboard?entity=Andina[&periods=2024&categories=solvency]`: el dashboard guardado. Por cada ratio trae `points[]` (un punto por periodo: `status`, `value`, `inputs[]`, `citations[]`, `cross_document`, `missing_statements`, `stale`, `outlier`) y `periods[]` (calculado, desactualizado, restringido, documentos y notas). Responde 404 si la empresa no existe.
- `POST /dashboard/refresh`: `{"entity": "Andina", "periods": ["2024", "2025"]}`. Sin `periods`, calcula todos los del catálogo (hasta `DASHBOARD_MAX_PERIODS`). Tarda unos 20 s por periodo y responde 503 si el LLM no responde. Cada periodo se guarda apenas se calcula.
- `POST /dashboard/export.xlsx`: el dashboard en pantalla, en tres hojas: ratios por periodo, entradas con sus citas, y atípicos con sus notas. El PDF es la impresión del navegador ("Save as PDF").

Datos de demo: `docker compose exec api python scripts/seed_demo.py --refresh`. Genera, sube y clasifica los workbooks de demo y deja el dashboard calculado.

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

## Sincronización con SharePoint y Google Drive (ID-HU-INT-001)

Los archivos nuevos o editados en una carpeta conectada de SharePoint o Google Drive se ingieren solos (`app/sync/`), sin volver a subirlos a mano.

### Desde la pestaña "Repositories" (iniciando sesión con Google o Microsoft)

**Uso:**
1. Con un rol aprobador (`reviewer`, `financial-controller`, `restricted-reviewer` o `admin`), abre la pestaña **Repositories**.
2. Pulsa **Sign in with Google** o **Sign in with Microsoft** e inicia sesión con tu cuenta.
3. Al volver, pulsa **Add folders** y navega:
   - Google: My Drive, Shared with me y las unidades compartidas.
   - Microsoft: My OneDrive, o SharePoint sites → sitio → biblioteca → carpetas.
4. Pulsa **Connect** en la carpeta y elige la clasificación, si incluye subcarpetas y si se aprueban los documentos solos. Se sincroniza enseguida.
5. Después se sincroniza cada `SYNC_INTERVAL_MINUTES`, con el acceso de esa cuenta. Allí mismo ves cada sincronización (nuevos, actualizados, errores) y los archivos.

**Configuración (una sola vez, el administrador):**
- **Google:**
  1. En Google Cloud Console, crea un proyecto, habilita la **Google Drive API** y configura la **pantalla de consentimiento OAuth**. Con tipo *External* en modo prueba, agrega a los usuarios de prueba.
  2. Crea credenciales → **ID de cliente OAuth** → *Aplicación web*, con el URI de redirección `http://localhost:8000/oauth/google/callback`.
  3. Copia el ID y el secreto en `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`.
- **Microsoft:**
  1. En el portal de Azure → Entra ID → **App registrations** → *New registration*, de tipo *Web*, con el URI de redirección `http://localhost:8000/oauth/microsoft/callback`.
  2. En *API permissions*, agrega los permisos **delegados** de Microsoft Graph: `Files.Read.All`, `Sites.Read.All`, `User.Read` y `offline_access`. Algunas organizaciones exigen consentimiento de administrador.
  3. En *Certificates & secrets*, crea un secreto. Copia el ID de la aplicación y el secreto en `MICROSOFT_OAUTH_CLIENT_ID` / `MICROSOFT_OAUTH_CLIENT_SECRET`.
  4. `MICROSOFT_OAUTH_TENANT`: `organizations` (por defecto), `common` o el ID del tenant.
- **Cifrado:** `TOKEN_ENCRYPTION_KEY` (una clave Fernet) cifra los tokens guardados y firma el `state` del inicio de sesión. Si se cambia, hay que volver a iniciar sesión con las cuentas.
- Reinicia la API: `docker compose up -d api`.

**Seguridad:**
- Los permisos pedidos son solo de lectura.
- Los tokens se guardan cifrados y el de acceso se renueva solo. Microsoft rota el refresh token y se guarda el nuevo.
- El `state` va firmado y vence en 10 minutos, contra CSRF.
- **Disconnect** en una cuenta olvida sus tokens y desconecta sus carpetas; los documentos se mantienen.
- La sincronización depende de esa cuenta: si pierde el acceso o revoca el permiso, la carpeta muestra el error.

Endpoints:
- `GET /oauth/providers`.
- `GET /oauth/{google|microsoft}/start` y `GET /oauth/{google|microsoft}/callback`.
- `GET /repository-accounts`, `DELETE /repository-accounts/{id}`.
- `GET /repository-accounts/{id}/folders?parent=…`.
- `POST /sync/sources` con `account_id`.

### Con credenciales de servicio (sin iniciar sesión)

**Credenciales** (en `.env`, ver `.env.example`):
- **SharePoint:** un registro de aplicación en Entra ID (Azure AD) con el permiso de aplicación de Microsoft Graph `Sites.Read.All` (o `Sites.Selected`) y consentimiento de administrador. Se completa `SHAREPOINT_TENANT_ID`, `SHAREPOINT_CLIENT_ID` y `SHAREPOINT_CLIENT_SECRET`; se autentica con client credentials.
- **Google Drive:** una cuenta de servicio con la Drive API habilitada, con cada carpeta compartida con su correo como lector. Se completa `GOOGLE_SERVICE_ACCOUNT_FILE` (ruta al JSON dentro del contenedor) o `GOOGLE_SERVICE_ACCOUNT_JSON`.

**Conectar una carpeta** (`POST /sync/sources`, requiere un rol aprobador):

```json
{"kind": "sharepoint", "name": "Corporate Finance/Q3 Reports",
 "config": {"site_url": "https://contoso.sharepoint.com/sites/Finance", "library": "Documents",
            "folder_path": "Corporate Finance/Q3 Reports", "include_subfolders": false},
 "confidentiality_tag": "internal", "source_tier": null, "auto_approve": false}
```

- Para Google Drive: `{"kind": "google_drive", "config": {"folder_id": "<id o URL de la carpeta>"}}`.
- `confidentiality_tag` (por defecto `internal`) se aplica a cada documento sincronizado, para que los analistas lo puedan consultar enseguida.
- `source_tier` fija el nivel de fuente (BE-016). Sin él, se deduce del nombre del archivo.
- `auto_approve` aprueba cada versión sincronizada, para carpetas que solo tienen documentos aprobados. Así una edición reemplaza a la versión aprobada anterior de inmediato. Sin él, la regla de BE-015 se mantiene: un borrador nuevo solo reemplaza a otro borrador.

**Cómo sincroniza** (`app/sync/service.py`):
- Cada `SYNC_INTERVAL_MINUTES` (15 por defecto; 0 lo apaga) se revisan **todas** las carpetas conectadas, de SharePoint y de Google Drive. La primera revisión es 10 s después de arrancar la API.
- Un candado de Postgres evita dos sincronizaciones a la vez.
- Cada archivo se recuerda con una marca de versión: el `cTag` de SharePoint, que cambia solo con el contenido, o el `md5Checksum` de Drive.
  - **Archivo nuevo:** se ingiere.
  - **Marca cambiada:** se ingiere como **nueva versión** del mismo documento (BE-015), y la copia vieja deja de responder.
  - **Marca igual:** no se descarga.
- Solo se sincronizan `.xlsx`, `.xlsm` y `.pdf`. Las hojas de Google Sheets se exportan como `.xlsx`.
- Un archivo que falla queda registrado con el motivo y se reintenta en la siguiente sincronización.
- Borrar un archivo del repositorio lo **retira** de FinDoc (ver ID-HU-INT-002 abajo).

### Permisos y archivos borrados en el repositorio (ID-HU-INT-002)

Los cambios de acceso en SharePoint o Google Drive se reflejan en FinDoc en la siguiente sincronización (`app/sync/permissions.py`, `app/sync/service.py`).

**Reglas de acceso de cada carpeta:** FinDoc da acceso por clasificación y rol, no por usuarios y grupos. Por eso cada carpeta tiene reglas que traducen los grupos del repositorio a clasificaciones de FinDoc. Se editan en la pestaña Repositories → **Access rules**, al conectar la carpeta o después.

```json
[{"principal": "Finance", "tag": "internal"},
 {"principal": "Legal Corporate", "tag": "restricted:legal"},
 {"principal": "Controllers", "tag": "restricted"},
 {"principal": "anyone", "tag": "public"}]
```

- **`principal`:** un grupo o usuario, por nombre o correo, sin distinguir mayúsculas. Puede ser un grupo de SharePoint como "Finance Members", un grupo de Microsoft 365 o de Google, o un usuario. También acepta `anyone` (enlace anónimo), `organization` (enlace de toda la organización) y `domain:<dominio>` (dominio de Google Drive).
- **Lectura de permisos:** en cada sincronización se leen los permisos de cada archivo, incluidos los heredados (Graph `/permissions`, Drive `permissions.list`), y se calcula su clasificación:
  - gana la **menos restrictiva** de las reglas que coinciden: un archivo compartido con Finance y con Legal lo lee Finance, así que queda `internal`;
  - si coinciden dos categorías restringidas distintas (legal y compensación), queda `restricted`, para no abrirlo de más;
  - si ninguna regla coincide (por ejemplo, solo lo ve su dueño), queda `restricted`.
- **Cambio de permisos:** si la clasificación cambia, se actualizan el documento, **todas sus versiones** y sus fragmentos (`permissions_changed`), tanto al restringir (el analista **pierde** el acceso, sin aviso especial: el documento simplemente deja de aparecer) como al ampliar (lo gana).
- **Sin reglas,** se usa la clasificación fija de la carpeta, y la clasificación que haya puesto un revisor en FinDoc no se toca.
- **Permisos ilegibles:** si no se pueden leer los permisos de un archivo (p. ej. 403), queda como estaba y se reintenta.

**Archivos borrados:**
- Un archivo que ya no está en la carpeta queda `removed`: todas las versiones de su documento dejan de responder consultas, incluso pidiéndolas explícitamente por `document_id`. Comparar y calcular ratios con él responde **410**.
- Se conservan para auditoría.
- Si el archivo vuelve sin cambios (por ejemplo, desde la papelera), se **restaura** (`restored`). Si vuelve editado, se ingiere como nueva versión.

### Fallos de sincronización y reintentos (ID-HU-INT-003)

Un archivo que falla o viene corrupto nunca bloquea al resto (`app/sync/retry.py`).

- **Aislamiento:** cada archivo se sincroniza por separado. Si uno falla al descargarlo, al ingerirlo (archivo corrupto) o al leer sus permisos, el resto se ingiere igual. Si 1 de 20 viene corrupto, los otros 19 quedan disponibles.
- **Archivo corrupto:** la versión que no se pudo ingerir se descarta, así los reintentos no llenan la lista de documentos con copias fallidas.
- **Reintentos:** se reintenta con espera exponencial: `SYNC_RETRY_BACKOFF_MINUTES` (15), luego 30, 60, 120… hasta `SYNC_RETRY_MAX_BACKOFF_MINUTES` (24 h). El reintento ocurre en la primera sincronización después de esa hora; antes no se toca (`retry_pending`).
- **Reintentos agotados:** tras `SYNC_RETRY_MAX_ATTEMPTS` intentos (5), el archivo queda `needs_review`:
  - deja de reintentarse solo;
  - el fallo queda marcado en el registro como **alerta** (y como error en el log del servidor);
  - la pestaña Repositories lo avisa arriba.
  
  Se vuelve a intentar con el botón **Retry** de la pestaña (`POST /sync/files/{id}/retry`, que reinicia los intentos), o solo si el archivo cambia en el repositorio, porque alguien pudo haberlo arreglado.
- **Registro de fallos:** cada intento fallido queda registrado con su paso (`download`, `ingestion`, `permissions`), la causa, el número de intento y el próximo reintento. Cuando un reintento funciona, sus fallos quedan **resueltos** ("retry succeeded"). Si el archivo sale de la carpeta, también ("left the folder").
- **Dónde se ve:** en Repositories → **Sync failure log**, y en `GET /sync/failures[?source_id=…&unresolved=true]` (rol aprobador o admin; si no, 403). `GET /sync/sources` da `needs_review` y `open_failures`; `GET /sync/sources/{id}/files`, `attempts` y `next_retry_at` de cada archivo.

**Endpoints:**
- `PATCH /sync/sources/{id}` con `{"permission_rules": [...]}`: cambia las reglas, que se aplican en la siguiente sincronización.
- `GET /sync/sources/{id}/files`: muestra, por archivo, con quién está compartido (`permissions`) y su clasificación (`access_tag`).

**Endpoints** (todos requieren un rol aprobador; si no, 403):
- `GET /sync/sources`: las carpetas con su última sincronización (`last_status`, `last_error`, `last_summary` con nuevos, actualizados, sin cambios, fallidos y omitidos), el intervalo y si hay una sincronización en curso.
- `POST /sync/sources`: conecta una carpeta. Responde 400 si falta `site_url` o `folder_id`, o si la clasificación o el nivel no existen.
- `DELETE /sync/sources/{id}`: la desconecta; los documentos ya ingeridos se mantienen.
- `GET /sync/sources/{id}/files`: cada archivo con su documento, o con el error.
- `POST /sync/run[?source_id=…]`: sincroniza ya, en segundo plano (202).

## Debugging del agente con Langfuse

Las llamadas del agente se registran en un **Langfuse local** (`app/observability.py`), para ver qué recibió y qué respondió el modelo en cada paso.

**Levantarlo**
- Langfuse corre en el mismo `docker compose`, bajo el perfil `langfuse`. Incluye la web, el worker, ClickHouse, Redis, MinIO y su propio Postgres; usa unos 2-3 GB de RAM.
- `COMPOSE_PROFILES=langfuse` en `.env` lo levanta con `docker compose up`. Sin esa línea, Langfuse no arranca.
- Completa en `.env` `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (cualquier `pk-lf-…` / `sk-lf-…`) y `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD`, como en `.env.example`. En el primer arranque, Langfuse crea el proyecto con esas llaves y ese usuario.
- La interfaz está en `http://localhost:3000`; se entra con el usuario de `.env`.
- Sin llaves, o con `LANGFUSE_TRACING_ENABLED=false`, no se registra nada y la app funciona igual.

**Qué queda registrado**
- Una **traza por operación**, con el usuario (`X-User-Id`), el rol, la entrada y el resultado:
  - `qa.answer`: cada pregunta del chat, agrupada por **sesión** de conversación.
  - `ratios`: `POST /ratios`.
  - `dashboard.refresh`: el cálculo del dashboard.
  - `compare`: `POST /compare`.
- Dentro de `qa.answer`:
  - `retrieval`: la pregunta traducida y las fuentes recuperadas, con su puntaje, documento, celdas, nivel de fuente (BE-016) y si está aprobada.
  - `qa_graph`, con sus nodos `triage`, `generate` y `ground_and_validate`.
  - Cada llamada al modelo: el prompt completo, la respuesta estructurada, los tokens y la latencia.
- Dentro de `ratios` / `dashboard.refresh`: un `calculate_ratios` por periodo, con el estado de cada ratio y sus entradas faltantes, y cada extracción de cifras. Las extracciones en paralelo quedan dentro de la misma traza.

**Privacidad:** los prompts incluyen el texto de los documentos. Por eso Langfuse corre local y su telemetría está apagada (`TELEMETRY_ENABLED=false`).

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
- **Dashboard (ID-HU-FE-004)**: pestaña "Dashboard",
  `frontend/src/components/DashboardView.tsx` y `TrendChart.tsx`. También se
  abre directo con `http://localhost:5173/?entity=Andina#dashboard`.
  - **Filtros:** empresa, periodos (más uno escrito a mano), categorías y "Calculate".
  - **Tarjetas clave:** ROE, ROA, EBITDA margin y current ratio, con la variación contra el periodo anterior y una mini-tendencia.
  - **Detalle del ratio elegido:** gráfico de tendencia (SVG propio, con tooltip), recuadro de "Outlier" con la tabla de causas, y las fuentes del periodo agrupadas por documento (cada cita abre el panel de fuente).
  - **Estados:** "Not calculable" con el documento o la línea faltante ("Missing input: Current liabilities"), "Held for review", "outdated" y "restricted".
  - "Export to Excel" y "Save as PDF".
- **Ratios (ID-HU-BE-010)**: pestaña "Ratios",
  `frontend/src/components/RatiosView.tsx`.
  - **Entrada:** selector de estados (1 a 6), categorías y periodo opcional.
  - **Tarjetas por categoría:** cada una muestra el ratio (`25.0%`, `1.75x`),
    su fórmula, el chip "cross-document" y una tabla con cada línea usada y su
    cita, que abre el panel de fuente.
  - **Ratios retenidos:** muestran "held for review" con el motivo y la línea
    marcada "low-confidence mapping"; el valor aparece al confirmar con
    "I've reviewed it — show ratio".

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

- **Base de datos de tests:** los tests de integración nunca usan la base de
  desarrollo. `tests/conftest.py` cambia a `TEST_DATABASE_URL` o, si no está
  definida, a la base de `DATABASE_URL` con sufijo `_test` (`findoc_rag_test`).
  La crea si no existe, le aplica las migraciones y la vacía al inicio de cada
  corrida. Así los documentos que subas usando la app no desplazan los datos de
  los tests, y los tests no escriben en tus datos.
- La imagen copia `tests/` al construirse; para correr los tests actuales sin
  reconstruir: `docker compose cp backend/tests/. api:/app/tests/` y luego
  `docker compose exec api pytest`.
- `RETRIEVAL_MIN_SCORE` se pone en `0` en los tests (el embedding falso de
  `conftest.py` tiene otra escala); `test_relevant_source_retrieval.py` cubre
  los escenarios de BE-007 pasando `min_score` explícito.
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
- `test_access_audit.py` cubre ID-HU-BE-014: registro de denegaciones (búsqueda, ratios, fuente y archivo), información privilegiada, que la denegación sobreviva al rollback, deduplicación por petición, filtros, paginación, CSV y el 403 de la auditoría. Las denegaciones se guardan en su propia sesión, así que cada test usa sus propios ids de usuario.
- `test_dashboard.py` cubre ID-HU-FE-004: catálogo (empresa, periodos, estados), atípicos y causas, tendencia, datos insuficientes, varias fuentes, resultados guardados y desactualizados, acceso restringido y export a Excel. Ingesta real y extracción guionada.
- `test_dashboard.py` (`test_be011_*`, `test_trend_endpoint`) y `test_ratios.py` (`test_be011_*`) cubren ID-HU-BE-011: guardado por periodo, ratio no calculable con la entrada faltante, tendencia cronológica de cuatro trimestres con fuentes, trimestre faltante (sin documento o sin una entrada) y el endpoint `/ratios/trend`.
- `test_source_hierarchy.py` cubre ID-HU-BE-016: el nivel deducido del nombre, apartar la fuente de nivel menor con la misma cifra (y no apartarla si el periodo o la empresa difieren), los avisos de borrador y de nivel menor, el flujo completo de `answer_question` con un modelo guionado (ERP preferido, reporte interno sin ERP, pronóstico en borrador) y el endpoint `PATCH /documents/{id}/source-tier`.
- `test_observability.py` cubre el tracing con Langfuse, sin servidor (los spans se exportan a memoria): que sin llaves no haga nada, que el usuario y la sesión pasen a los spans anidados, y que los nodos del grafo y las llamadas al modelo queden dentro de la traza de la pregunta. El resto de los tests corre con `LANGFUSE_TRACING_ENABLED=false` (conftest), así nunca envían trazas.
- `test_embeddings_batching.py` cubre el envío de embeddings por lotes (PDFs grandes).
- `test_sync.py` cubre ID-HU-INT-001:
  - **Conectores contra Graph y la Drive API simulados** (`httpx.MockTransport`): inicio de sesión, búsqueda del sitio y la biblioteca, paginación, subcarpetas, descargas y exportación de Google Sheets.
  - **Sincronización con un repositorio falso e ingesta real:** archivo nuevo de SharePoint y de Drive consultable enseguida, archivo editado que pasa a ser nueva versión (la copia vieja deja de responder), archivos sin cambios que no se descargan, ambos repositorios en una sola corrida, fallo y reintento, archivos omitidos, repositorio inaccesible, aprobación automática y candado.
  - **El programador** de sincronizaciones y la API.
- `test_oauth.py` cubre el inicio de sesión con Google y Microsoft contra proveedores simulados: el `state` firmado (falsificado, vencido o de otro proveedor), las URLs de inicio de sesión, el guardado de la cuenta con tokens cifrados, la renovación del token y la rotación del refresh token, el navegador de carpetas, la sincronización con el token de la cuenta y la API (callback, conectar y desconectar). `RepositoriesView.test.tsx` cubre la pestaña.
- `test_sync_retry.py` cubre ID-HU-INT-003:
  - 1 archivo corrupto entre 20: los otros 19 se ingieren y se pueden consultar; el corrupto queda registrado con reintento programado y sin documento fallido.
  - El archivo espera su reintento; un fallo temporal funciona al reintentar y el registro queda resuelto.
  - Tras 5 intentos queda en revisión manual con alerta y ya no se reintenta solo; se vuelve a intentar si cambia en el repositorio o si sale de la carpeta.
  - El tiempo de espera exponencial, y la API del registro y de Retry.
- `test_permission_sync.py` cubre ID-HU-INT-002:
  - De permisos a clasificación: la menos restrictiva gana, categorías distintas o sin coincidencia quedan `restricted`.
  - Lectura de permisos de Graph y Drive simulados.
  - Con ingesta y búsqueda reales: restringir en SharePoint (el analista pierde el acceso, también a versiones anteriores y pidiéndolo explícitamente; el controller lo conserva), ampliar a Finance (el analista lo gana), archivo borrado (deja de responder, 410 en ratios) y restaurado, permisos ilegibles, sin reglas y la API de reglas.
- `test_ratios.py` cubre ID-HU-BE-010.
  - **Sin base ni LLM** (extractor falso): cada fórmula contra estados de
    ejemplo, el mapeo de etiquetas, los ratios entre documentos, los ratios
    retenidos para revisión, periodos y monedas distintos, denominador cero,
    rango plausible y escala.
  - **De punta a punta** (requiere Postgres, LLM guionado): ROA con dos
    workbooks ingeridos de verdad, y una cifra inventada que no llega al ratio.

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
  audit.py                  # ID-HU-BE-014: registro de accesos permitidos/denegados + contexto por petición
  figures/
    extraction.py           # ID-HU-FE-003: extract_figure (compartido con BE-010) + tasas de cambio documentadas
    comparison.py             # variaciones, conciliación, monedas (aritmética determinística)
    export.py                   # Excel de la comparación
  dashboard/                # ID-HU-FE-004
    catalog.py                # empresas, periodos y estados de cada documento (sin LLM)
    service.py                # refresh (calcula y guarda) / get_dashboard (lee, atípicos, desactualizados)
    outliers.py               # detección de atípicos y causas (DuPont)
    export.py                 # Excel del dashboard
  ratios/                   # ID-HU-BE-010
    formulas.py               # librería de ratios y líneas de los estados (sinónimos en/es)
    mapping.py                # ¿la etiqueta encontrada es la entrada de la fórmula? + pistas de estado
    engine.py                 # búsqueda de entradas entre documentos, aritmética y citas
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
    routes_audit.py                # GET /audit/access-logs (+ export.csv), solo admin (ID-HU-BE-014)
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

### Auditoría de accesos denegados (ID-HU-BE-014)

Todo intento de acceso queda en `retrieval_audit_logs`, **permitido o denegado**, con usuario, roles, fecha y hora, documento (id y nombre), chunk (si aplica), nivel de confidencialidad, motivo, origen y la pregunta. El registro lo escribe `app/audit.py`.

- **Qué se registra como denegado:**
  - **Búsqueda:** las coincidencias relevantes que el rol excluye. Siempre se aplica `RETRIEVAL_MIN_SCORE`, también en la extracción de cifras, así que una pregunta sin relación no genera denegaciones.
  - **Lectura directa de una fila:** las filas que leen la extracción de cifras y el chat (`lookup`), y los precedentes de una fórmula.
  - **Documento de ratios o de `/compare` con nivel restringido** (`extract`).
  - **Fuente citada o archivo:** abrir una fuente en `/resolve`, `/cells` o `/file` (`resolve`, `view_cell`, `view_file`, sin chunk).
  - **Auditoría:** consultar el registro sin ser admin (`audit_query`).
- **Las denegaciones se guardan aparte:** se escriben en su propia sesión y con commit inmediato, así que quedan registradas aunque la petición falle después (por ejemplo, un 503 del LLM). Si escribir el registro falla, el acceso sigue denegado.
- **Contexto por petición:** cada petición tiene un `request_id` y un `resource` (`chat`, `query`, `ratios`, `compare`, `resolve`, `cells`, `file`, `audit`), que asigna `AuditContextMiddleware`. Los hilos de ratios heredan ese contexto. Dentro de una petición, la misma denegación se registra una sola vez; la búsqueda bilingüe, por ejemplo, busca dos veces.
- **Quién es:** `X-User-Id` (en `/file`, también `?user=`). El frontend lo envía desde el campo "User" del encabezado.

**Información privilegiada no publicada:**
- Etiqueta `restricted:insider`. También se aceptan "Pre-release: Insider", "insider" y "pre-release".
- Requiere el permiso `insider-access`, que tiene el rol `pre-release-disclosure` (el grupo de divulgación previa).
- **Es más estricta que el resto:** `restricted-access` (de `restricted-reviewer`) no alcanza, ni el `financial-controller`. `admin` sí accede, y queda registrado.
- Cada evento sobre ese contenido lleva `is_insider=true`. La respuesta del chat no incluye ninguna cifra y muestra el aviso genérico de información restringida.
- Al publicarse los resultados, la etiqueta se cambia a mano con `PATCH /documents/{id}/confidentiality`.

**Consulta para administradores** (cualquier otro rol recibe `403`, y ese intento también queda registrado):
- `GET /audit/access-logs`: filtros `user_id`, `document_id`, `document` (parte del nombre), `from`, `to` (ISO 8601), `authorized` (`true`/`false`), `tier`, `insider_only` y `resource`; paginación `limit` (máximo 500) y `offset`. Ordenado del más reciente al más antiguo, con `total`.
- `GET /audit/access-logs/export.csv`: los mismos filtros, en CSV. Las celdas que empiezan con `=`, `+`, `-` o `@` se escapan para que una hoja de cálculo no las evalúe.
- En el frontend, la pestaña **Audit** solo aparece con el rol `admin`.

La migración `0013_access_audit` agrega las columnas nuevas y completa `document_filename` en los registros que ya existían.

