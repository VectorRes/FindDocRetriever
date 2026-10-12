# Informe — ID-HU-BE-010: Motor de ratios financieros

**Rama:** `feature/be-010-financial-ratio-calculation`. Sale de `feature/fe-003-comparison`, porque reutiliza su `extract_figure`, como se acordó en `docs/HANDOFF_BE-007_BE-010.md`.

## Qué resuelve

El analista elige los estados ya ingeridos (por ejemplo, el estado de resultados y el balance) y el sistema calcula los ratios estándar de liquidez, solvencia, rentabilidad y eficiencia. Cada ratio trae la cita de **cada** línea usada, así que no hace falta recalcularlo a mano.

## Cómo funciona

1. **Librería de fórmulas** (`app/ratios/formulas.py`). Tiene 15 ratios:
   - **Liquidez:** current ratio, quick ratio, cash ratio.
   - **Solvencia:** debt-to-equity, debt ratio, interest coverage.
   - **Rentabilidad:** gross margin, operating margin, EBITDA margin, net margin, ROA, ROE.
   - **Eficiencia:** asset turnover, inventory turnover, receivables turnover.

   Cada ratio declara sus entradas, su aritmética, su unidad (porcentaje o veces) y un **rango plausible**. Las 15 líneas de los estados que usan las fórmulas tienen sinónimos en inglés y español: "Utilidad neta", "Net profit", "Pasivo corriente"…

2. **Mapeo de líneas a entradas** (`app/ratios/mapping.py` y `engine.py`):
   - La cifra la obtiene `extract_figure` de FE-003: el valor siempre aparece en la fuente citada y nunca se inventa.
   - Cada entrada se busca **una sola vez entre todos los documentos elegidos**. Primero se prueba el documento cuyo nombre u hojas sugieren el estado correcto ("EERR", "resultados", "P&L" o "balance", "situación financiera"). La búsqueda para en el primer resultado de confianza alta; si no hay ninguno, se queda con el mejor.
   - Luego se verifica **en código** que la etiqueta encontrada sea un nombre estándar de esa entrada. La comparación es estricta: ignora tildes, mayúsculas, años, notas y un "Total" inicial, pero nada más. "Total pasivo corriente (COP)" es válida. "Short-Term Obligations" y "Non-current liabilities" no lo son, así que el mapeo queda con **confianza baja**.
   - Esta verificación manda sobre la opinión del LLM: si el modelo duda de "Net sales" para *revenue* pero la etiqueta está en la lista, la duda se descarta. Así no aparece un aviso innecesario y la búsqueda para en el primer documento.
   - La consulta incluye los sinónimos más comunes ("Revenue / Ingresos / Sales / Net sales"), porque la búsqueda solo ve ese texto. Con eso, los ingresos del 10-Q pasaron de "no encontrado" a encontrados.
   - Las entradas se buscan **en paralelo** (`RATIO_EXTRACTION_WORKERS`, 6 por defecto). Cada hilo usa su propia sesión de base de datos y guarda su auditoría de retrieval.

3. **Cálculo determinístico**, sin LLM (`compute_ratio`). Hay tres estados posibles:

   | Estado | Cuándo |
   |---|---|
   | `calculated` | Todas las entradas encontradas, mismas moneda y periodo, resultado plausible. |
   | `needs_review` | El valor se calcula, pero la UI **lo retiene** hasta que el analista confirma que lo revisó. Pasa en cualquiera de estos casos: una entrada se mapeó a una etiqueta no estándar; las entradas son de periodos distintos; las cifras del estado de resultados cubren lapsos distintos (tres meses contra nueve); una entrada no corresponde al periodo pedido (nueve meses cuando se pidió "Q3"); un saldo que nunca es negativo sale negativo (por ejemplo, patrimonio negativo en debt-to-equity o ROE); o el resultado sale del rango plausible. |
   | `unavailable` | Falta una entrada, las monedas son distintas o el denominador es cero. Se explica el motivo; no se muestra ningún número. |

   Si una entrada tiene confianza media por otro motivo (por ejemplo, "el documento también trae 2023; se usó el periodo más reciente"), el ratio se muestra igual, junto con el aviso.

4. **Citas:** cada ratio lleva `inputs[]` (línea, etiqueta tal como aparece, valor, documento y cita) y `citations[]` sin duplicados. `cross_document=true` cuando las entradas vienen de más de un documento.

## Criterios de aceptación

| Escenario | Resultado |
|---|---|
| Estado de resultados y balance de un periodo → ratios de liquidez, solvencia, rentabilidad y eficiencia | Los 15 se calculan y coinciden con el cálculo a mano en el test `test_all_categories_…`; cada uno cita todas sus líneas. |
| **Principal:** EBITDA margin del FY24 | 25.0 %, con citas a las líneas de EBITDA e ingresos. |
| **Alterno 1:** ROA con la utilidad neta en el estado de resultados y los activos en el balance | 10.0 %, `cross_document`, cita a los dos documentos. También probado de punta a punta con dos workbooks ingeridos. |
| **Alterno 2:** pasivos corrientes rotulados "Short-Term Obligations" | El current ratio queda `needs_review` y la línea marcada "low-confidence mapping"; el valor solo aparece tras "I've reviewed it — show ratio". Los ratios que no usan esa línea no se retienen. |

## Prueba manual con el LLM real (10-Q de Apple, Q3 2026)

La prueba con un documento real destapó dos problemas, ya corregidos y cubiertos por tests:

1. **Escala aplicada solo a algunas cifras.** La extracción aplicó "en millones" a los activos corrientes pero no a los pasivos corrientes del mismo documento, y el current ratio salió **1,003,294**. Ahora, si todas las entradas vienen del mismo documento, se usan las cifras tal como están escritas: la escala se cancela. Resultado: **1.00**.
2. **Mezcla de periodos sin detectar.** El 10-Q trae columnas de tres meses, de nueve meses y por segmento. Sin un periodo indicado, se mezclaron y salieron márgenes del 217 % y el 401 %. Ahora esos ratios quedan retenidos por **rango plausible** o por **periodos distintos**, y el reporte sugiere indicar un periodo. Con `period: "Q3 2026"`, los ratios de liquidez y solvencia salen limpios.

## Correcciones tras la revisión de código

Una revisión posterior encontró cinco problemas más, uno de ellos en FE-003. Todos están corregidos y cubiertos por tests:

| Problema | Corrección |
|---|---|
| El botón decía "Mapping is correct" aunque el motivo fuera otro (periodos, rango) | Ahora dice "I've reviewed it — show ratio". |
| Si el LLM dudaba de una etiqueta aceptada ("Net sales"), aparecía un aviso innecesario y se hacían llamadas extra | Manda la lista de etiquetas aceptadas; la duda del modelo se descarta. |
| Un debt-to-equity negativo (patrimonio negativo) salía como "calculated" | Los saldos que nunca son negativos (patrimonio, activos, pasivos, ingresos, caja…) retienen el ratio si salen negativos. Las utilidades negativas no se marcan, porque son válidas. |
| La librería completa tardaba ~25 s y con 6 documentos podía pasar del timeout de 180 s | Extracción en paralelo: **~5 s** por documento con el LLM real. |
| Si el LLM fallaba, la API devolvía un 500 genérico | Si fallan todas las extracciones: **503** con un mensaje claro. Si fallan algunas: esas entradas quedan sin dato y una nota pide reintentar. El frontend muestra el mensaje y no el JSON. Aplica también a `/compare`. |
| **FE-003:** en documentos con columnas de tres y nueve meses, la extracción tomaba la columna equivocada (la utilidad de nueve meses rotulada "Q3") | Ver abajo. |

**Corrección en FE-003** (`app/figures/extraction.py`, `comparison.py`):
- El prompt pide preferir el total consolidado sobre segmentos y, si el periodo es un trimestre, la columna de tres meses.
- El modelo ahora informa la columna leída (`column_header`) y el lapso que cubre (`period_coverage`: tres, seis, nueve o doce meses, o fecha puntual). Si eso no coincide con el periodo pedido, el código lo marca como un problema (`issues["coverage"]`), con confianza media y el motivo.
- En BE-010, una entrada con ese problema **retiene el ratio**. También se retiene si las entradas del estado de resultados cubren lapsos distintos.
- En la comparación, dos valores con el mismo periodo pero distinto lapso ("Q3 2026" de tres meses contra el de nueve) se tratan como periodos distintos: se muestra la variación y no se emite veredicto de conciliación.
- `ExtractedFigure` expone los problemas por tipo (`issues`) para que BE-010 decida cuáles descarta. El texto de `reason` no cambia.

**Falso "no concilian" entre años (encontrado probando a mano con Excel de ejemplo):** al comparar la utilidad neta 2024 contra 2025, `/compare` decía *"don't reconcile"* y sugería escalar, cuando debía mostrar "periodos distintos". Había tres causas, todas corregidas:
- **Ingesta de Excel** (`app/ingestion/chunker.py`): se tomaba la primera fila con datos como encabezado. En un estado financiero esa fila suele ser el título ("Andina S.A.S. — Estado de resultados — Año 2024"), así que la cifra llegaba como `B=610000`, sin año ni moneda.
  - Ahora el encabezado es la primera fila con al menos dos valores distintos. Un título, aunque esté combinado en varias columnas, tiene uno solo.
  - Las filas de arriba (título, "Cifras en COP") viajan como contexto en cada chunk: `Sheet 'Estado de resultados' (… Año 2024 · Cifras en pesos colombianos (COP)), Utilidad neta: Concepto=Utilidad neta; 2024=610000`.
  - **Los documentos ya ingeridos conservan los chunks viejos: hay que volver a subirlos.**
- **Periodo vacío:** el prompt ahora exige leer el periodo del encabezado de columna o del título. Si el modelo igual lo deja vacío y la fuente citada menciona un solo año, se usa ese año. Se ignora la propia cifra, para que un valor de 2010 no se lea como año.
- **Red de seguridad en la comparación:** si no se pidió un periodo y alguno de los valores no tiene periodo conocido, una diferencia ya no se reporta como discrepancia ni se escala. Se muestra la variación y una nota que pide indicar el periodo.

Con el LLM real, la comparación 2024 contra 2025 ahora da `period=2024/2025`, COP en ambos, *"different periods"* y ningún escalamiento.

Resultado con el 10-Q real y `period: "Q3 2026"`:
- La utilidad neta ahora es **29,789**, la de tres meses; antes tomaba 101,464, la de nueve.
- Quedan "calculated" los ratios limpios: liquidez, debt ratio y ROA trimestral.
- Todos los que mezclaban columnas quedan retenidos con el motivo exacto, como "Revenue: the value covers twelve months (Total net sales)…".

## Correcciones al chat encontradas con el set de prueba

Probar el chat con `samples/` (Andina 2024/2025, Pacífico, presupuesto con fórmulas) destapó problemas de BE-009 y FE-002. Todos están corregidos y probados con el LLM real:

| Problema | Corrección |
|---|---|
| "¿Cómo se calcula la utilidad neta presupuestada?" marcaba un **falso conflicto** entre el presupuesto (723,723) y lo real (702,000) | **Prompt:** presupuesto/proyección contra real, escenarios o entidades distintas no son conflicto. **Código** (`settle_conflicts`): un conflicto necesita al menos dos afirmaciones del mismo tipo de cifra; una de presupuesto nunca choca con una real. |
| La misma pregunta **no explicaba el cálculo** | `app/qa/formula_context.py`: las filas que la fórmula referencia (incluidos named ranges como `TasaImpuesto`) se agregan como fuentes, respetando permisos y con auditoría. La explicación se arma desde el propio Excel: *"Cálculo de Utilidad neta (723,723): =C5-C6, donde C5 = Utilidad operacional (1,113,420) y C6 = Impuesto de renta (389,697)"*, con cita a cada componente. 3 de 3 corridas. |
| La verificación de cifras aceptaba **"1,205,000 USD" cuando la fuente decía 1205** (BE-009) | Multiplicar por mil o por millón solo se acepta si la fuente citada declara la escala ("en miles", "in millions"…). |
| "What was revenue?" **no pedía aclaración** pese a haber dos empresas y dos años | El triage ahora recorre las fuentes una por una (`metric_sources`: id, entidad, periodo). Las entidades se comparan sin la forma jurídica ("Andina" = "Andina S.A.S."), y las opciones son solo los pares que existen (no aparece "Pacífico – 2025"). De 0 a 3 de 3 corridas. |
| La **respuesta a la aclaración** ("Andina S.A.S. – 2024") perdía la pregunta original y devolvía cualquier cifra de Andina 2024 | La respuesta se combina con la pregunta aclarada para buscar y generar ("What was revenue? (Andina S.A.S. – 2024)"). |
| El conflicto real BI (4,795,000) contra estado de resultados (4,820,000) **no se marcaba** | `detect_conflicts`: dos afirmaciones que citan la cifra pedida para la misma entidad, periodo y tipo, con montos distintos (tolerancia de conciliación 0.1 %), se marcan como conflicto. Ahora sale el aviso con confianza baja y el escalamiento a Accounting/Consolidation. |
| El conflicto incluía la misma cifra **en otra moneda** (1,205 USD contra 4,820,000 COP) | El conflicto se agrupa también por moneda: la del texto de la afirmación o, si no la dice, la de su fuente. |
| Con fuentes en conflicto, la respuesta salía con **confianza alta** | `assess_confidence(has_conflicts=True)` la baja a **baja**, con el motivo, y siempre escala a Accounting/Consolidation, igual que una comparación que no concilia. |
| Si el modelo ya explicaba la fórmula, el código agregaba la explicación de una **sub-fórmula** (la utilidad operacional dentro de la utilidad neta) | Solo se explica la fórmula de nivel superior entre las citadas, y solo si el modelo no citó ya sus componentes. |
| El modelo a veces dejaba restos como "(S2).},{" en el texto | `clean_statement_text` los quita; las citas se muestran aparte. |
| Las cifras salían sin separador ("610000") | El prompt pide formato con separador de miles ("610,000"). La verificación acepta ambas formas. |

## Cambios de API

| Endpoint | Cambio |
|---|---|
| `GET /ratios/definitions` | Nuevo. La librería de fórmulas. |
| `POST /ratios` | Nuevo. `{document_ids (1–6), period?, ratios?, categories?}`; respeta `X-User-Role`/`X-User-Id`. Devuelve `ratios[]`, `line_items[]` y `notes[]`. Un ratio o una categoría desconocidos devuelven 400; si el LLM no responde, 503. |
| `POST /compare` (FE-003) | 503 si el LLM no responde. Las filas traen `period_coverage` y `column_header`. |
| Configuración | Nuevo setting `RATIO_EXTRACTION_WORKERS` (6). |

## Frontend

- **Pestaña "Ratios"** (`RatiosView.tsx`): selector de estados, categorías y periodo opcional.
- **Tarjetas por categoría:** cada una muestra el valor (`25.0%`, `1.75x`), la fórmula, el chip "cross-document" y una tabla de entradas con su cita, que abre el panel de fuente existente. Cada entrada se muestra **tal como está escrita en la fuente**, con su escala si la tiene ("149,818 USD (in millions)"), para que coincida con lo que muestra la cita.
- **Ratios retenidos:** borde ámbar, "held for review", motivo, y la línea marcada "low-confidence mapping".

## Tests

- **Backend:** 68 nuevos en `tests/test_ratios.py`, 18 nuevos en `tests/test_comparison.py` y 3 en `tests/test_chunker.py`, todos pasan.
  - La mayoría corre sin base ni LLM, con un extractor falso.
  - Los de punta a punta usan ingesta real y LLM guionado. El de ROA ejercita la extracción en paralelo contra Postgres; otro simula la caída del LLM y verifica el 503.
  - Además, `tests/test_formula_context.py` (16), `tests/test_conflicts.py` (13) y nuevos casos en `test_confidence_ambiguity.py` cubren las correcciones del chat.
  - La suite completa da **278 pasan, 0 fallan**, estable en corridas repetidas.
  - Antes fallaban 3 tests (`test_confidentiality`, `test_retrieval`, `test_conversation`); ya fallaban en la rama base, verificado con el código de `feature/fe-003-comparison`. La causa: corrían contra la base de desarrollo, cuyos documentos desplazaban los datos del test en el top-k, como avisa el handoff.
  - Se corrigió en `tests/conftest.py`: los tests usan su propia base (`findoc_rag_test`, o `TEST_DATABASE_URL`), que se crea, se migra y se vacía al iniciar cada corrida. La base de desarrollo ya no se toca.
- **Frontend:** 11 nuevos en `RatiosView.test.tsx` y 2 en `client.test.ts`. Pasan los 60 de la suite y el typecheck.

## Limitaciones conocidas

1. **Una llamada al LLM por entrada.** Las llamadas van en paralelo (~5 s por documento), pero el volumen depende del límite de tasa del proveedor; se ajusta con `RATIO_EXTRACTION_WORKERS`.
2. **La calidad sigue dependiendo de que el LLM informe bien la columna.** Con el 10-Q, el modelo todavía elige a veces la columna anual para los ingresos. La diferencia es que ahora lo dice, y esos ratios quedan retenidos con el motivo en vez de mostrarse como válidos. Si el modelo marca mal la columna (dice "tres meses" y es de nueve), el control no lo atrapa. Conviene indicar siempre el periodo.
3. **Ratios con saldos de cierre.** El ROA y el ROE usan los activos y el patrimonio al cierre, no el promedio. Debt-to-equity usa los pasivos totales, no solo la deuda financiera. Las dos cosas están documentadas en la fórmula.
4. **La confirmación de un ratio retenido no se guarda.** "I've reviewed it" solo muestra el valor en pantalla; no se recuerda para la próxima vez. Guardar mapeos confirmados por documento sería una historia aparte.
5. **Revisión visual limitada.** Se probó en el navegador contra la API real, solo con el 10-Q (liquidez y solvencia, `Q3 2026`). Falta probarla con un par estado de resultados + balance en español antes de la demo.
