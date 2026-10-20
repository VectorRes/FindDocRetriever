# Informe — ID-HU-FE-004: Dashboard de cifras clave y ratios

**Rama:** `feature/be-014-access-audit`. FE-004 se construyó sobre BE-014, para que el dashboard quede auditado.

## Qué resuelve

El analista abre el dashboard, elige una empresa y ve sus ratios clave (ROE, ROA, EBITDA margin, current ratio y los 15 de la librería de BE-010) periodo por periodo, con la cita de cada cifra. No tiene que recalcularlos a mano. El dashboard además:
- muestra la **tendencia** entre periodos;
- marca los **valores atípicos** y explica qué los causó;
- dice qué **documento falta** cuando un ratio no se puede calcular;
- se exporta a **Excel** o **PDF**.

## Cómo funciona

1. **Catálogo, sin LLM** (`app/dashboard/catalog.py`). Lee lo que la ingesta ya guardó:
   - **Empresa:** sale del nombre del archivo, sin palabras de estado, años, meses ni versiones ("EERR_Andina_2024.xlsx" → Andina). Los nombres de la misma empresa se agrupan ("Andina" = "Andina S.A.S.").
   - **Periodos:** salen de los encabezados de columna que rotula el chunker. Solo cuentan años completos y trimestres: "Three Months Ended June 30, 2026" → Q2 2026, "Al 31 de diciembre de 2025" → 2025, "Q1 2024" → Q1 2024. Los acumulados de seis o nueve meses, el presupuesto y las variaciones se descartan. Se toman de las filas que son **líneas de los estados**: en el Excel real de El Eclipse, una columna auxiliar "2016" de la hoja de flujo de caja aparecía como periodo y ya no.
   - **Estados:** un documento tiene el estado de resultados o el balance si alguna de sus filas es una línea estándar de ese estado (o si su nombre lo indica).
   - Solo se listan los documentos vigentes, procesados y visibles para el rol.

2. **Resultados guardados** (decisión acordada; tablas `dashboard_runs` y `ratio_snapshots`, migración `0014`):
   - "Calculate" ejecuta `calculate_ratios` de BE-010 **por periodo**, solo con los documentos de la empresa para ese periodo, y guarda cada ratio con sus entradas y citas.
   - Abrir el dashboard **lee lo guardado**: no llama al LLM y es instantáneo.
   - Cada periodo se guarda apenas se calcula, así que si el LLM falla a la mitad, lo ya calculado se conserva.
   - **Desactualizado:** un periodo queda "outdated" cuando sus documentos ya no son los vigentes de la empresa para ese periodo (se aprobó una versión nueva, se subió o se borró un documento).

3. **Datos insuficientes** (alterno 1). Antes de llamar al motor se revisa qué estados tiene la empresa en el periodo. Un ratio que necesita un estado ausente **no se envía al LLM**: queda "Insufficient data" con el estado faltante, por ejemplo *"the balance sheet for 2024 hasn't been ingested for Pacifico"*. Nunca se muestra un 0.

4. **Valores atípicos y sus causas** (alterno 2, `app/dashboard/outliers.py`, sin LLM):
   - **Criterio:** un valor se marca si, respecto del periodo calculado anterior, cambia **más del 50 %** y además más de un mínimo absoluto: **5 puntos porcentuales** en porcentajes o **0.5x** en múltiplos. Así, 2 % → 3.5 % no es alarma y ROE 12 % → 34 % sí. Con 4 periodos o más se aplica además un z-score robusto (mediana/MAD).
   - **Solo valores "calculated":** los retenidos o no disponibles no entran en la comparación.
   - **Causas:** el **ROE** se descompone con **DuPont** (margen neto × rotación de activos × multiplicador de capital) y el ROA con margen × rotación. El mensaje nombra el componente que más cambió, por ejemplo *"the main driver is equity multiplier, which rose from 1.66x to 4.17x (a leverage change). Review the underlying drivers before presenting this value."*. En los demás ratios se muestra el cambio de cada entrada.

5. **Varias fuentes** (criterio 3). Cada punto guarda `inputs[]` y `citations[]` de BE-010. La UI agrupa las entradas **por documento**, con el chip "cross-document: N sources", y cada cita abre el panel de fuente.

6. **Acceso** (BE-014). Si quien mira no puede ver uno de los documentos con que se calculó un periodo, ese periodo sale "restricted", sin valores ni citas, y el intento queda en la auditoría (`view_dashboard`).

## Criterios de aceptación

| Escenario | Resultado |
|---|---|
| Estados procesados → abrir el dashboard muestra ROE, ROA, EBITDA margin y current ratio con valor y citas | Tarjetas clave arriba, los 15 ratios por categoría y el detalle con las citas de cada línea |
| Elegir un periodo anterior → tendencia | Selector "Showing", gráfico de tendencia, variación contra el periodo anterior y filtro de periodos |
| Ratio con cifras de más de un documento → lista todas las fuentes | ROA y ROE citan el estado de resultados y el balance, agrupados por documento |
| **Principal:** EBITDA margin FY24 con citas a ingresos y EBITDA | Probado: 27.8 % en Andina 2024; las citas apuntan a las filas "Ingresos" y "EBITDA" |
| **Alterno 1:** sin balance FY24 → "insufficient data" e indica el documento faltante | Pacífico 2024: los ratios de liquidez y solvencia, ROA, ROE y los de eficiencia dicen "Insufficient data — Missing document: the balance sheet for 2024". Los márgenes y la cobertura de intereses sí se calculan |
| **Alterno 2:** ROE 12 % → 34 % → atípico y revisar causas | Andina 2025: ROE 13.0 % → 34.0 % marcado, con la causa "equity multiplier (leverage)". También se marcan debt-to-equity y debt ratio, que es coherente |

Subtareas: diseño con tarjetas y gráficos ✔, gráfico de tendencia ✔, citas de varias fuentes ✔, filtros por empresa, periodo y categoría ✔ (el **segmento** quedó fuera por la decisión acordada: solo cifras consolidadas), export a PDF y Excel ✔.

## Prueba con el LLM real

- **Andina 2023–2025** (datos de demo): calcular los 3 periodos tardó **20 s**. Los 45 valores coinciden con el cálculo a mano y todos salen "calculated".
- **Pacífico 2024** (sin balance): 5 ratios calculados y 10 con "insufficient data" que nombran el balance de 2024.
- **El Eclipse S.A.S.** (Excel real, NIIF, varias hojas y fórmulas), 3 periodos en 37 s. Destapó dos problemas del motor de BE-010, ya corregidos:

  | Problema | Corrección |
  |---|---|
  | "Ganancia (pérdida) del periodo", la utilidad neta en NIIF, no era una etiqueta aceptada. Quedaba como mapeo dudoso, y el modelo además la ofrecía como *utilidad bruta* (margen bruto de 378 %) y *utilidad operacional* | Se agregaron sinónimos NIIF de la utilidad neta (`formulas.py`). Ahora el motor la reconoce y descarta usarla como otra línea: el margen bruto falso desaparece |
  | Las columnas "NIIF-2024" y "2024" se tomaban como periodos distintos y retenían el ratio | `period_key` ignora el marco contable (NIIF, IFRS, GAAP) |

  Resultado: ROE y ROA 2022 pasan a "calculated", y el salto real del ROE (2.8 % → 18.1 % en 2024) se marca como atípico. Los ratios que siguen retenidos tienen motivo real: es una sociedad de inversión y su "ingreso" disponible es "Otros ingresos por función".

## API

| Endpoint | Descripción |
|---|---|
| `GET /dashboard/catalog` | Empresas, con sus periodos y documentos (estados y periodos de cada uno), según el rol |
| `GET /dashboard?entity=&periods=&categories=` | El dashboard guardado. Sin LLM. 404 si la empresa no existe; 400 si una categoría no existe |
| `POST /dashboard/refresh` | `{entity, periods?}`: calcula y guarda; devuelve el dashboard. 503 si el LLM no responde; 400 si un periodo no se entiende |
| `POST /dashboard/export.xlsx` | Excel del dashboard en pantalla, en tres hojas: ratios, entradas y fuentes, atípicos y notas |
| Settings | `DASHBOARD_OUTLIER_REL_CHANGE` (0.5), `DASHBOARD_OUTLIER_MIN_POINTS` (0.05), `DASHBOARD_OUTLIER_MIN_TIMES` (0.5), `DASHBOARD_MAX_PERIODS` (8) |

## Frontend

- **Pestaña "Dashboard"** (`DashboardView.tsx`). Se abre directo con `http://localhost:5173/?entity=Andina#dashboard`.
  - **Configuración:** empresa; periodos (marcar = mostrar y recalcular solo esos; también se puede escribir otro, por ejemplo 2022); categorías; "Calculate"; "Export to Excel"; "Save as PDF".
  - **Tarjetas clave** de ROE, ROA, EBITDA margin y current ratio, con la variación contra el periodo anterior (en pp o x) y una mini-tendencia.
  - **Detalle del ratio elegido:** gráfico de tendencia, el recuadro "Outlier in 2025 — review before presenting" con la tabla de causas, y "Sources for 2025" agrupadas por documento.
  - **Estados:** "Insufficient data" (con el documento faltante; aclara que no significa "sin actividad"), "Held for review" (con "I've reviewed it — show value"), "outdated", "restricted" y "Not calculated".
- **`TrendChart.tsx`**, gráfico SVG propio, sin dependencias nuevas:
  - una sola serie, línea de 2 px y marcadores de 8 px con anillo;
  - cuadrícula tenue y etiqueta solo en el último valor;
  - los periodos sin valor quedan como hueco con "n/a", nunca como 0;
  - el atípico lleva color de estado **más** el ícono "!" y la palabra "Outlier" (no solo color);
  - tooltip al pasar el mouse, clic para elegir el periodo, y descripción completa para lectores de pantalla.

## Datos de demo

`backend/scripts/seed_demo.py` genera, sube y clasifica 9 workbooks:
- Andina 2023–2025, con un ROE que salta por apalancamiento;
- Pacífico 2024, sin balance;
- un documento de resultados preliminares marcado como información privilegiada, para BE-014;
- uno de compensación ejecutiva.

Con `--refresh` deja el dashboard calculado: `docker compose exec api python scripts/seed_demo.py --refresh`.

## Tests

- **Backend:** 45 nuevos en `tests/test_dashboard.py`.
  - catálogo (5 nombres de archivo, 13 encabezados de columna, periodos escritos, orden, columnas auxiliares);
  - atípicos (criterio, movimientos chicos, z-score) y causas (DuPont y entradas);
  - de punta a punta con ingesta real: principal, tendencia, alterno 1, alterno 2, varias fuentes, lectura sin extracción, desactualizado hasta recalcular, periodos sin calcular, periodo sin documentos, acceso restringido con auditoría, errores y falla del LLM;
  - API: catálogo por rol, dashboard, categorías, 404/400 y Excel.
  - La suite completa da **486 pasan, 0 fallan**.
- **Frontend:** 11 en `DashboardView.test.tsx` y 4 en `TrendChart.test.tsx`. La suite da **83 pasan** y el typecheck queda limpio.
- **Revisión visual:** capturas con Edge headless de Andina (atípico y causas) y de Pacífico (datos insuficientes); ajusté el tamaño del texto del gráfico y los chips.

## Limitaciones conocidas

1. **La empresa sale del nombre del archivo.** Un archivo sin el nombre de la empresa ("Estados_2024.xlsx") queda como su propia empresa. Para la demo y el uso normal conviene nombrar `EERR_<Empresa>_<año>` / `Balance_<Empresa>_<año>`.
2. **Trimestres fiscales.** Un trimestre se ubica por el mes de cierre de la columna (calendario). Para un año fiscal distinto, por ejemplo el Q3 fiscal de Apple que cierra en junio, el dashboard lo muestra como Q2. Se puede escribir el periodo a mano.
3. **Sin filtro por segmento** (decisión acordada).
4. **"I've reviewed it" no se guarda** (igual que en BE-010): solo muestra el valor en pantalla.
5. **Un periodo calculado con documentos que el usuario no ve** sale "restricted" completo, aunque algunos ratios no usen el documento restringido. Es lo más seguro. Quien no tiene ese acceso puede recalcular el periodo: el cálculo usa solo los documentos que su rol ve, y para quien sí tiene acceso ese periodo queda "outdated" hasta que lo recalcule.
