# Handoff — BE-007 (retrieval) y BE-010 (ratios)

**Para:** el compañero a cargo de ID-HU-BE-007 e ID-HU-BE-010 en el Sprint 6.
**De:** el trabajo de BE-009 (y, próximamente, FE-003).

## Desde qué rama trabajar

Nada de los sprints 4 a 6 está integrado todavía en `feature/excel-rag`. La rama más actualizada es **`feature/fe-003-comparison`**: incluye FE-001/002, FE-005, BE-012/013, BE-015, BE-009 y FE-003 (con `extract_figure`). Conviene crear tus ramas desde ahí, o esperar a que se integre a la rama principal, para no trabajar sobre una base vieja.

Archivos que tocamos los dos (coordinemos antes de cambiarlos):

| Archivo | Quién lo cambia en este sprint |
|---|---|
| `backend/app/retrieval/service.py`, `retrieval/vector_store.py` | Tú (BE-007) |
| `backend/app/qa/*` (graph, prompts, confidence, schemas) | Nosotros (BE-009 ya hecho) |
| `backend/app/figures/*` (nuevo) | Nosotros (FE-003). Úsalo desde BE-010; si necesitas cambiarlo, avisemos |

---

## BE-007 — Tareas pendientes que te tocan

### 1. Umbral de relevancia (acordado como tuyo)

Hoy el retrieval siempre devuelve los `top_k` chunks más cercanos, aunque ninguno tenga que ver con la pregunta. Eso es el caso alterno 2 de BE-007 ("returns no relevant sources") y también el primer criterio de BE-009.

**Contrato acordado**, porque BE-009 ya lo consume:

- `RetrievedChunk` gana un campo **`score: float | None`**: la similitud coseno (`1 - cosine_distance`), donde un número más alto es más relevante.
- Nuevo setting **`RETRIEVAL_MIN_SCORE`** en `config.py` / `.env`. `retrieve()` descarta los chunks con `score` menor al umbral.
- Si nada pasa el umbral, `retrieve()` devuelve **lista vacía**.
- El umbral se aplica **después** de los filtros de confidencialidad y versión, que ya ocurren antes del `LIMIT`.

**Qué pasa del lado de BE-009 cuando lo implementes** (no hace falta que cambies nada nuestro):

- Con lista vacía, el grafo de QA ya responde "no confident answer" sin llamar al LLM, y sugiere un equipo de escalamiento.
- `qa/confidence.py` ya lee `score` de las fuentes citadas (`getattr(chunk, "score", None)`). Si el promedio es menor a `WEAK_RELEVANCE` (hoy `0.5`, un valor provisional), baja la confianza. **Calibremos juntos ese valor y `RETRIEVAL_MIN_SCORE`** con preguntas reales: con `text-embedding-3` las similitudes útiles suelen estar bastante por debajo de 1.

### 2. El aviso de "información restringida" también debería usar el umbral

`restricted_match_exists()` / `has_inaccessible_match()` miran los `top_k` más cercanos sin filtrar por relevancia. Resultado: el aviso *"Part of the relevant information is restricted…"* aparece en casi cualquier pregunta mientras haya documentos restringidos en la base. Lo vimos en todas las pruebas de BE-009, incluso con "What is our supplier concentration risk?". Con el umbral, solo debería avisar si la coincidencia restringida es relevante.

### 3. Diversidad multi-documento (criterio 2 de BE-007)

El `top_k` es global y puede llenarse con chunks de un solo documento. En las pruebas de BE-009, "What was revenue?" trajo 8 resultados de los archivos de ejemplo en inglés y **ninguno** de los estados de resultados de Andina/Pacífico (en español), que también tenían ingresos. Sugerencias: un tope de chunks por documento, o MMR (*maximal marginal relevance*). También conviene revisar consultas en inglés sobre documentos en español.

### 4. Ojo con los tests

`test_confidentiality.py::test_authorized_retrieval_is_audited` y `test_retrieval.py::test_restricted_chunk_is_excluded_…` fallan si se corren contra una base de desarrollo con documentos cargados (esos datos desplazan a los del test). Contra una base limpia pasan. Recomendación: correr los tests contra una base separada, `findoc_test`.

---

## BE-010 — Reutiliza `extract_figure` (lo construimos nosotros en FE-003)

Acordamos que **nosotros** construimos la extracción de cifras dentro de FE-003, y que BE-010 la reutiliza para las entradas de las fórmulas en vez de reimplementarla.

**Ya está implementado** en la rama `feature/fe-003-comparison`, en `backend/app/figures/extraction.py`:

```python
def extract_figure(
    db: Session,
    document_id: UUID | str,
    metric: str,                       # p. ej. "net income", "total assets", "current liabilities"
    user_roles: str | None = "analyst",  # respeta control de acceso (BE-013)
    user_id: str | None = "anonymous",   # queda en la auditoría de retrieval
    period: str | None = None,           # qué periodo tomar si el documento trae varios
) -> ExtractedFigure: ...

@dataclass
class ExtractedFigure:
    document_id: str
    found: bool
    value: float | None          # en unidades base (valor escrito x escala documentada)
    value_text: str | None       # el número tal como aparece en la fuente
    unit_scale: str              # "units" | "thousands" | "millions" | "billions"
    currency: str | None         # "COP", "USD", "EUR"… si el documento lo dice
    period: str | None           # "FY2024", "Q3 2024"…
    label: str | None            # la etiqueta tal como aparece ("Short-Term Obligations")
    citation: ResolvedCitation | None  # documento, hoja/celdas o página, texto
    confidence: str              # "high" | "medium" | "low"
    reason: str | None           # por qué no se encontró, o por qué bajó la confianza
```

También está `find_exchange_rate(db, from_currency, to_currency, ...)`, que busca una tasa de cambio **documentada** en el corpus, con su cita.

- **Garantía:** el `value_text` siempre aparece en la fuente citada (verificación numérica de BE-009). Si no, `found=False`; nunca se inventa una cifra. Una escala ("en miles") solo se aplica si el texto del documento la menciona.
- `label` y `confidence` sirven directo para el caso alterno 2 de BE-010: si la etiqueta del estado no coincide con la métrica estándar, por ejemplo "Short-Term Obligations" para *current liabilities*, la confianza baja y se explica en `reason`.
- Para ratios que combinan varios documentos, por ejemplo ROA = utilidad neta (estado de resultados) / activos totales (balance), basta llamar `extract_figure` por cada documento y entrada. Cada una trae su propia cita.

También está disponible **`app/escalation.py`** (`suggest_escalation(topic, reason)`), por si un ratio con baja confianza debe sugerir un equipo.
