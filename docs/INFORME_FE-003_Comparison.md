# Informe — ID-HU-FE-003: Vista de comparación entre documentos

**Rama:** `feature/fe-003-comparison` (sale de `feature/be-009-confidence-ambiguity`)

## Qué resuelve

El analista puede elegir de 2 a 6 documentos y una métrica ("utilidad neta", "ingresos"…) y ver los valores lado a lado, cada uno con su cita. El sistema marca si concilian, muestra la variación (absoluta y %) y maneja monedas distintas, sin conciliar los archivos a mano.

## Cómo funciona

1. **Extracción por documento** (`app/figures/extraction.py::extract_figure`, que reutiliza BE-010):
   - Busca **solo dentro de ese documento**, con el alcance por documento de BE-015. Así un documento no desplaza a los otros, que es el problema del top global.
   - El LLM indica qué fuente tiene la cifra y la copia **tal como está escrita**.
   - **El código verifica que ese número aparezca en la fuente**, con la misma verificación de BE-009. Si no aparece, la fila sale como "no encontrada"; nunca se inventa una cifra.
   - Si la etiqueta del documento no coincide con la métrica (por ejemplo, "Short-Term Obligations" para pasivos corrientes), la confianza baja a media y se explica.
   - Respeta los permisos: un documento restringido para el rol sale como restringido, y su contenido nunca llega al LLM.

2. **Comparación determinística** (`app/figures/comparison.py`, sin LLM):
   - **Mismo periodo** (estado de resultados contra flujo de caja del Q1): se evalúa si concilian, con una tolerancia relativa configurable (`COMPARISON_TOLERANCE`, por defecto 0.1 %). Si no concilian, las filas se marcan en rojo y se sugiere **Accounting/Consolidation**.
   - **Periodos distintos** (2024 contra 2025): solo se muestra la variación contra la base, sin veredicto de conciliación, porque el cambio es esperado.
   - **Monedas distintas:** se muestran los valores originales y se busca una **tasa de cambio documentada** en los documentos, con su cita. Si existe, se convierte; si no, se avisa y no se convierte. Nunca se usa una tasa "de mercado".

3. **Exportación:**
   - **Excel** (`POST /compare/export.xlsx`): exporta la comparación que se ve en pantalla, sin recalcularla, con citas, variaciones, tasa de cambio, notas y equipo sugerido.
   - **PDF:** "Guardar como PDF" del navegador, con una hoja de estilos de impresión que deja solo la tabla.

## Criterios de aceptación (probados con el LLM real)

| Escenario | Resultado |
|---|---|
| Utilidad neta: estado de resultados vs. flujo de caja (Q1) | 610,000 en ambos, con su celda citada → **concilian** |
| Ingresos BI (4,820,000) vs. ERP (4,795,000) | **No concilian**, fila ERP en rojo, −25,000 / −0.52 %, sugiere **Accounting/Consolidation** |
| Ingresos en EUR vs. USD **sin** tasa documentada | Valores originales, sin conversión y con aviso |
| Ingresos en EUR vs. USD **con** tasa documentada (EUR/USD 1.08) | 1,000,000 EUR → 1,080,000 USD, tasa citada → concilian |
| Ingresos 2024 vs. 2025 | +490,000 / +10.17 %, sin veredicto de conciliación |
| Exportar a Excel | Archivo `.xlsx` con valores, citas y resultado |

## Bug encontrado durante las pruebas reales

Las pruebas con el LLM real destaparon que el modelo **inventaba la escala**: decía "en miles" o "en millones" sin que el documento lo indicara, y multiplicaba la cifra (4,820,000 → 4,820,000,000). Ahora una escala solo se aplica si el texto del documento la menciona ("en miles", "in thousands", "millones", "MM"…). Si no, se usa el valor tal como está escrito y se avisa con confianza media. Hay un test que cubre este caso.

## Cambios de API

| Endpoint | Cambio |
|---|---|
| `POST /compare` | Nuevo. Recibe `{document_ids (2–6), metric, period?}` y respeta `X-User-Role` |
| `POST /compare/export.xlsx` | Nuevo. Recibe el resultado de `/compare` |
| Configuración | Nuevo setting `COMPARISON_TOLERANCE` |

## Frontend

- **Nueva pestaña "Compare"** en el header. El chat sigue montado al cambiar de pestaña, así que la conversación no se pierde.
- **Selector de documentos:** solo muestra los ya procesados, marca la base y avisa si una versión no es la vigente.
- **Campos:** métrica con sugerencias y periodo opcional.
- **Tabla:** cada valor tiene su cita, y la cita abre el panel de fuente existente.
- **Resultado y exportación:** banner con el resultado (concilian / no concilian con equipo / entre periodos / no determinable), notas, y los botones "Export to Excel" y "Save as PDF".

## Tests

- **Backend:** 146 pasan, 30 de ellos nuevos, en `tests/test_comparison.py`. Cubren lecturas numéricas, extracción verificada, cifra inventada, documento restringido, etiqueta no estándar, escala sin respaldo, conciliación, tolerancia, periodos, monedas con y sin tasa, orientación de la tasa, versiones no vigentes, exportación y validación del request.
- **Frontend:** 47 pasan, 7 nuevos en `ComparisonView.test.tsx`. El typecheck y el build de producción pasan.

## Limitaciones conocidas

1. **Hay una llamada al LLM por documento** (más una por cada moneda que necesite tasa). Comparar 6 documentos tarda varios segundos; el frontend espera hasta 90 s.
2. **Si un documento trae varios periodos y no se indica cuál, se usa el más reciente** y se avisa con confianza media. Para fijarlo, está el campo "Period".
3. **La conversión de moneda solo usa tasas que estén escritas en los documentos subidos.** Si no hay ninguna, la conciliación queda "no determinable".
4. **La cita apunta a la fila completa del Excel** (`A3:B3`), por la misma brecha de FE-002: los chunks de Excel son por fila.
5. **No se revisó visualmente en el navegador.** La vista está cubierta por tests de componentes y el build, pero conviene que alguien del equipo la mire antes de la demo.
6. **⚠ Posible punto de falla: falsos negativos en la verificación de cifras (pendiente de investigar).** En una prueba manual con el estado financiero real `EEFF EL ECLIPSE S.A.S DICIEMBRE 2024.xlsx` (v1 y v2), la métrica "Operating cash flow" salió como *"The extracted figure couldn't be found in its source"* en ambas versiones. Ese mensaje no significa que el dato falte: si faltara, diría *"This document doesn't state…"*. Significa que el LLM propuso una cifra y la verificación la rechazó porque el número no aparece tal cual en la fuente citada. El sistema no muestra una cifra no verificada, que es lo correcto, pero con documentos reales la comparación puede quedar vacía.
   - **Causas probables (sin confirmar):**
     - el modelo escribe el número distinto a la fuente (redondeado, o con `$`, espacios o separadores);
     - toma un valor de otra fila o columna;
     - el Excel guarda la cifra en un formato que `match_figure` no reconoce.
   - **Cómo investigarlo:** registrar el `value_text` y el `source_id` que propone el modelo y compararlos con el texto del chunk citado. Luego ajustar la verificación o el prompt **sin aflojar la garantía de no inventar cifras**.
