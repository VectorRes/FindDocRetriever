# FinDoc Retriever — RAG de Excel y PDF

Motor de ingesta y recuperación semántica para workbooks `.xlsx`/`.xlsm` y documentos
`.pdf`. Extrae hojas, celdas, fórmulas y referencias cruzadas (Excel) o texto, tablas
financieras y notas/secciones numeradas (PDF, con fallback OCR para páginas
escaneadas), indexa todo (datos estructurados + embeddings vectoriales) y expone una
API para consultarlo en lenguaje natural con citación exacta (documento, hoja, celda
— o página y tabla/nota, para PDF).

Incluye ingesta de Excel (hojas, celdas, fórmulas, referencias cruzadas) y un pipeline
de ingesta de PDF (texto nativo + OCR, extracción de tablas financieras y de
notas/secciones numeradas) construido sobre el mismo motor de recuperación.

## Levantar el entorno

```bash
cp .env.example .env
# por defecto EMBEDDING_PROVIDER=openai, así que hay que completar OPENAI_API_KEY
# con una key real (proveedores sin endpoint de embeddings, p. ej. DeepSeek, no
# sirven aquí). Para no depender de una API externa, cambiá a
# EMBEDDING_PROVIDER=local (sentence-transformers, corre en el propio contenedor).
docker compose up --build
```

Esto levanta Postgres (con la extensión `pgvector`) y la API FastAPI en `http://localhost:8000`.
Las migraciones de Alembic se aplican automáticamente al arrancar el contenedor `api`.
La imagen de la API también instala `tesseract-ocr` (paquetes de idioma `eng` y `spa`),
necesario para el fallback de OCR sobre páginas de PDF escaneadas.

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
  (`excel`/`pdf`) y los warnings de ingesta:
  - Excel: macros, enlaces externos, hojas protegidas, referencias circulares.
  - PDF: páginas sin texto extraíble ni OCR utilizable, páginas OCR de baja
    confianza, tablas detectadas con estructura de baja confianza, y PDFs
    cifrados (se intenta descifrar con contraseña vacía; si requiere contraseña
    real, se marca como `failed` con un warning explicativo y no se extrae nada).
- `POST /query` — `{"question": "...", "top_k": <opcional>}` → chunks más
  relevantes, cada uno con `document_filename` y, según el tipo de contenido,
  `sheet_name`+`cell_range` (Excel), `page_number` (PDF), `content_type`
  (`text`/`table_row`/`reference`), `reference_number` (para chunks de
  notas/secciones), y `extraction_method`/`confidence`/`confidence_label`
  (`native`/`ocr` y su confianza, para chunks de PDF).
- `GET /documents/{document_id}/pdf-structure` — para un documento `pdf`, devuelve
  todas sus tablas financieras (`headers`, `rows`, `confidence`,
  `confidence_label`, `raw_text`) y todas sus notas/secciones detectadas
  (`reference_type`, `reference_number`, `title`, `text`), ordenadas por página.
  Responde `400` si el documento no es un PDF y `404` si no existe.
- `GET /health` — chequeo básico.

Documentación interactiva en `http://localhost:8000/docs`.

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

## Estructura

```
backend/app/
  config.py               # settings desde .env (incluye toda la config de OCR)
  db/models.py             # Document, Sheet, Cell, NamedRange, PdfTable, PdfReference, Chunk
  schemas.py                # DTOs Pydantic de la API (incluye PdfStructureResponse)
  ingestion/
    excel_parser.py          # extracción con openpyxl
    pdf_parser.py              # texto nativo (pypdf) + OCR fallback (PyMuPDF/Tesseract),
                                # extracción de tablas financieras y de notas/secciones
    chunker.py                   # arma chunks embebibles con metadata de citación
                                  # (Excel: filas de hoja; PDF: texto, table_row, reference)
    service.py                     # orquesta parseo -> persistencia -> embeddings
  embeddings/                # proveedor intercambiable (openai / local)
  retrieval/                  # búsqueda por similitud en pgvector
  api/
    routes_ingest.py           # POST /ingest
    routes_query.py              # POST /query, GET /documents/{id}/pdf-structure
```