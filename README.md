# FinDoc Retriever — RAG de Excel y PDF

Motor de ingesta y recuperación semántica para workbooks `.xlsx`/`.xlsm` y documentos
`.pdf`. Extrae hojas, celdas, fórmulas y referencias cruzadas (Excel) o texto por
página (PDF), indexa todo (datos estructurados + embeddings vectoriales) y expone
una API para consultarlo en lenguaje natural con citación exacta (documento, hoja,
celda — o página, para PDF).

Cubre las historias de usuario **BE-001, BE-002, BE-003** (ingesta de Excel) y **BE-007**
(recuperación) del documento `FinDoc_Retriever_User_Stories...md`, más ingesta de PDF
como extensión del mismo pipeline de recuperación. No incluye todavía generación de
respuestas con LLM (BE-008/BE-009) ni el agente de LangChain — esta pieza es la base
de recuperación que ese agente usará más adelante como herramienta.

## Levantar el entorno

```bash
cp .env.example .env
# por defecto EMBEDDING_PROVIDER=local (sentence-transformers, sin API key).
# si prefieres EMBEDDING_PROVIDER=openai, completa OPENAI_API_KEY con una key
# de OpenAI real — proveedores sin endpoint de embeddings (p. ej. DeepSeek) no sirven aquí.
docker compose up --build
```

Esto levanta Postgres (con la extensión `pgvector`) y la API FastAPI en `http://localhost:8000`.
Las migraciones de Alembic se aplican automáticamente al arrancar el contenedor `api`.

## Variables de entorno (`.env`)

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | Cadena de conexión a Postgres |
| `EMBEDDING_PROVIDER` | `openai` o `local` — proveedor de embeddings activo |
| `OPENAI_API_KEY`, `OPENAI_EMBEDDING_MODEL`, `OPENAI_EMBEDDING_DIMENSIONS` | Config del proveedor `openai` |
| `LOCAL_EMBEDDING_MODEL`, `LOCAL_EMBEDDING_DIMENSIONS` | Config del proveedor `local` (sentence-transformers, corre en el mismo contenedor, sin llamadas externas) |
| `RETRIEVAL_TOP_K` | Cantidad de chunks devueltos por consulta |
| `PDF_CHUNK_SIZE`, `PDF_CHUNK_OVERLAP` | Tamaño (caracteres) y solape de la ventana deslizante usada para trocear el texto de cada página PDF |

**Cambiar de proveedor de embeddings requiere re-indexar**: la dimensión del vector queda
fija en la tabla `chunks` desde la migración inicial (según `EMBEDDING_PROVIDER` en el
momento de migrar). Para comparar `openai` vs `local`, lo más simple es usar bases de datos
(o esquemas) separados por proveedor.

## API

- `POST /ingest` — sube un `.xlsx`/`.xlsm` o un `.pdf` (multipart `file`), lo parsea,
  indexa y devuelve el estado del documento (`ready`/`failed`) junto con `doc_type`
  (`excel`/`pdf`) y los warnings de ingesta:
  - Excel: macros, enlaces externos, hojas protegidas, referencias circulares.
  - PDF: páginas sin texto extraíble (típicamente escaneadas, sin OCR) y PDFs
    cifrados (se intenta descifrar con contraseña vacía; si requiere contraseña
    real, se marca como `failed` con un warning explicativo y no se extrae nada).
- `POST /query` — `{"question": "..."}` → chunks más relevantes, cada uno con
  `document_filename` y, según el tipo de documento, `sheet_name`+`cell_range`
  (Excel) o `page_number` (PDF).
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
`null` para documentos PDF).

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

Los tests de `excel_parser`, `pdf_parser` y `chunker` no requieren base de datos —
los de PDF generan sus propios archivos `.pdf` mínimos en `tests/pdf_helpers.py`,
sin depender de una librería de autoría de PDFs. `test_retrieval.py` y
`test_retrieval_pdf.py` son tests de integración que necesitan Postgres/pgvector
accesible (se saltan automáticamente si no lo está) y usan un proveedor de
embeddings falso y determinista (en `tests/conftest.py`) para no depender de una
API externa ni descargar modelos.

## Estructura

```
backend/app/
  config.py          # settings desde .env
  db/models.py        # Document, Sheet, Cell, NamedRange, Chunk
  ingestion/
    excel_parser.py    # extracción con openpyxl
    pdf_parser.py         # extracción de texto por página con pypdf
    chunker.py               # arma chunks embebibles con metadata de citación (Excel y PDF)
    service.py                  # orquesta parseo -> persistencia -> embeddings
  embeddings/            # proveedor intercambiable (openai / local)
  retrieval/              # búsqueda por similitud en pgvector
  api/                      # endpoints FastAPI
```