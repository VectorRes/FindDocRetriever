# Informe — ID-HU-BE-009: Confianza baja, preguntas ambiguas y escalamiento

**Rama:** `feature/be-009-confidence-ambiguity` (sale de `feature/be-015-version-control`)

## Qué resuelve

Antes, el sistema podía mostrar una cifra que no estaba en la fuente que citaba, respondía preguntas ambiguas eligiendo una interpretación al azar, y cuando no tenía respuesta recomendaba consultar a "un experto" sin decir cuál.

## Cómo funciona

El pipeline de QA pasa a ser `triage → (clarify | generate → ground_and_validate)`.

1. **Triage**, una llamada corta al LLM antes de generar la respuesta. No responde la pregunta: solo reporta **hechos**:
   - si las fuentes recuperadas contienen la métrica pedida;
   - para qué entidades y periodos aparece;
   - si la pregunta, leída con el historial, ya dice cuál;
   - si la pregunta pide un **juicio profesional** (por ejemplo, cómo reconocer un ingreso);
   - el tema.

   Si el triage falla, se responde igual, sin esos chequeos.

2. **Ambigüedad, decidida en código** (`clarification_for`, no por el LLM): si la métrica aparece para más de una entidad o periodo y la pregunta no dice cuál, se devuelve `needs_clarification` con hasta 6 opciones sacadas de las fuentes. El front las muestra como botones; al elegir una, la conversación sigue con ese contexto. Una métrica que no está en las fuentes nunca es "ambigua".

   > Primero probamos con la regla dentro del prompt de generación, y luego con un triage que respondía "¿es ambigua?". Con el LLM real ninguna de las dos funcionó: el modelo elegía un periodo y respondía. Pedirle hechos y decidir en código sí funcionó.

3. **Sin respuesta confiable:** además de descartar afirmaciones sin cita válida, ahora se descarta **toda afirmación con una cifra que no aparece en la fuente que cita**. La verificación tolera redondeo, formatos `1,234.5` y `1.234,5`, "3.4 millones" y fuentes expresadas en miles. Si no queda nada, `grounded=false`: no se muestra ninguna cifra.

4. **Confianza** (`high` / `medium` / `low`, con un puntaje de 0 a 1 y las razones), calculada a partir de:
   - las afirmaciones descartadas;
   - las cifras que no se pudieron verificar;
   - la autoconfianza que reporta el modelo;
   - si la pregunta pide un juicio profesional (una pregunta así nunca sale con confianza alta);
   - la relevancia de las fuentes citadas, cuando el retrieval la provea (BE-007).

5. **Escalamiento:** cuando no hay respuesta o la confianza es baja, se sugiere un equipo según el tema (`app/escalation.py`, editable):

| Tema | Equipo |
|---|---|
| Reconocimiento de ingresos, contabilidad, consolidación | Accounting/Consolidation |
| Impuestos | Tax |
| Caja, FX, deuda | Treasury |
| Nómina, compensación | HR/Compensation |
| Litigios, contingencias | Legal |
| Presupuesto, pronóstico | FP&A |
| Otros | Financial Reporting |

## Criterios de aceptación (probados con el LLM real)

| Escenario | Resultado |
|---|---|
| "What is our supplier concentration risk?" (no está en ningún documento) | Sin respuesta confiable, sin cifra, con equipo sugerido |
| "What was revenue?" con varias entidades y periodos | Pide aclaración con opciones (entidad × periodo); al elegir una, responde |
| "¿Cuál fue la utilidad neta de Pacífico S.A. en 2025?" | Responde directo, confianza alta, sin pedir aclaración |
| "How should we recognize revenue for the Acme contract?" | Confianza **baja** y sugiere **Accounting/Consolidation** |

## Cambios de API (`POST /query/answer`)

Campos nuevos en la respuesta:

| Campo | Contenido |
|---|---|
| `confidence` | `{level, score, reasons[]}`; `null` cuando no hay respuesta |
| `clarification_options[]` | Opciones para la aclaración |
| `escalation` | `{team, topic, reason}` |

`needs_clarification` y `clarification_question` ahora sí se usan.

## Frontend

- Badge de confianza en cada respuesta.
- Banner rojo cuando la confianza es baja, con las razones y el equipo sugerido.
- Opciones de aclaración como botones.
- El mensaje de "sin respuesta" nombra al equipo y ahora también aparece cuando, además, hay contenido restringido (antes solo salía el aviso de restricción).

## Tests

- **Backend:** 116 pasan, 38 de ellos nuevos, en `tests/test_confidence_ambiguity.py`. Cubren la verificación de cifras, el puntaje, la decisión de ambigüedad, el triage fallido, el historial de la aclaración y el ruteo de temas.
- **Frontend:** 40 tests y el typecheck pasan.
- **Tests ajustados:**
  - `test_conversation.py`: el LLM falso ahora contempla el triage, y el test cita las dos fuentes, porque su orden no está garantizado.
  - Un test de BE-013 en el front: el caso "todo restringido" ahora también muestra "sin respuesta confiable".

## Limitaciones conocidas

1. **Hay una llamada extra al LLM por pregunta** (el triage). Es una llamada corta, pero suma algo de latencia y costo.
2. **El umbral de relevancia del retrieval lo hace tu compañero (BE-007).** Mientras tanto, "sin fuente relevante" depende de que el LLM diga que las fuentes no responden, más la verificación de cifras. Ver `docs/HANDOFF_BE-007_BE-010.md`.
3. **Para que la aclaración tenga opciones completas, el retrieval tiene que traer todos los documentos relevantes.** Con el top global actual pueden faltar entidades (también está en el handoff).
4. **En preguntas de juicio, el modelo puede seguir citando reglas generales que aparecen en las fuentes**, por ejemplo una nota de políticas contables de otro documento. Por eso esas respuestas siempre salen con confianza baja y sugieren el equipo.
5. **La verificación numérica ignora enteros menores a 100 y los años**, para no confundir etiquetas ("Q3", "nota 12") con cifras.
