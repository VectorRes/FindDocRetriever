# Informe — ID-HU-BE-015: Control de versiones (supersesión y versión por defecto)

**Rama:** `feature/be-015-version-control` (sale de `feature/be-013-access-control`, que ya incluye FE-005 y el control de acceso)
**Commit principal:** `b540314`

## Qué resuelve

Antes de esta historia, las versiones solo afectaban lo que mostraba el panel de fuente. Había tres problemas:

1. Solo se detectaba una versión nueva si el nombre del archivo era **idéntico**. `Budget_v1.xlsx` → `Budget_v2.xlsx` no se reconocía como versión.
2. No existía el estado "aprobado". Subir un archivo reemplazaba al anterior en el acto.
3. El chat podía citar versiones viejas, y la respuesta no decía qué versión había usado.

## Cómo funciona ahora

**Grupos de versiones.** Cada documento pertenece a un grupo que se deriva del nombre del archivo:

| Archivo | Grupo |
|---|---|
| `Budget_v1.xlsx`, `Budget_v2.xlsx`, `Budget.xlsx`, `Budget (1).xlsx` | `excel:budget` |
| `Budget_2025_v1.xlsx` | `excel:budget 2025` (otro periodo = otro documento) |
| `Budget_v1.pdf` | `pdf:budget` (un PDF nunca se agrupa con un Excel) |

Si un archivo no sigue la convención `_vN` (por ejemplo, "Presupuesto final"), al subirlo se puede mandar `version_of=<id>` para asociarlo a mano.

**Aprobación y versión por defecto.**
- Toda subida entra como la versión más reciente de su grupo, en estado `draft`.
- La **versión por defecto** del grupo (la que usa el chat) es la **última aprobada**. Si ninguna está aprobada, es la más reciente, para que un documento recién subido se pueda consultar de inmediato.
- Una versión nueva **no reemplaza** a una aprobada hasta que se aprueba. Mientras tanto aparece como *pending approval*.
- Al aprobarla, pasa a ser la versión por defecto. La anterior queda `superseded` y enlazada a su sucesora.
- Si se borra la versión por defecto, el grupo vuelve a la siguiente según la misma regla.

**Retrieval y respuestas.**
- El chat solo busca en la versión por defecto de cada grupo. El filtro se aplica antes del `LIMIT`, así las versiones viejas no ocupan espacio en los resultados.
- Cada respuesta incluye `versions_used`, con qué versión de cada documento se usó. El front lo muestra como *"Based on: Budget_v2.xlsx (v2, approved, current)"*.
- **Auditoría:** con `document_id` en la consulta, la pregunta se limita a esa versión aunque esté reemplazada. La respuesta avisa que no es la vigente y dice cuál lo es.

## Criterios de aceptación

| Criterio | Cómo se cumple | Verificado |
|---|---|---|
| Con varias versiones, se usa la última aprobada | Filtro de retrieval por versión por defecto | Test + app real: con v1 aprobada y v2 sin aprobar, responde con v1 (50,000) |
| Al aprobar una versión nueva, reemplaza a la anterior y quedan enlazadas | `POST /documents/{id}/approve` | Test + app real: al aprobar v2, responde 72,000 y v1 queda `superseded` |
| Se puede consultar una versión anterior para auditoría | `document_id` en la consulta + `GET /documents/{id}/versions` | Test + app real: responde 50,000 y avisa que no es la versión vigente |
| La respuesta dice qué versión usó | `versions_used` en `/query/answer` | Test + app real |

## Cambios de API

| Endpoint | Cambio |
|---|---|
| `POST /ingest` | Campo opcional `version_of` (multipart) |
| `POST /documents/{id}/approve` | **Nuevo.** Requiere rol aprobador en `X-User-Role` (`reviewer`, `restricted-reviewer`, `financial-controller`/`controller`, `admin`); si no, `403`. Devuelve `400` si el documento no está `ready` |
| `GET /documents/{id}/versions` | **Nuevo.** Todas las versiones del grupo, de la más nueva a la más vieja |
| `POST /query`, `POST /query/answer` | Campo opcional `document_id` para fijar la versión |
| `POST /query/answer` | Nueva respuesta `versions_used[]` |
| `DocumentOut` | Nuevos campos `version_group`, `version_number`, `approval_status`, `approved_at` |
| `GET /documents/{id}/resolve` | Una versión nueva pendiente de aprobación ya **no** se reporta como `superseded`; `current_version` es la versión por defecto del grupo |
| `POST /documents/{id}/supersede` | Ahora mete el documento en el grupo del nuevo; cuál queda por defecto sigue la regla de aprobación |

**Migración `0012_version_control`:** agrega las columnas nuevas. Los documentos existentes quedan como `approved` (ya se usaban para responder) y se agrupan por nombre de archivo.

## Frontend

- **Panel de documentos:** badge de versión (`v2`), estado (*current version* / *superseded* / *pending approval*) y *approved*. Además, los botones **Approve version** (solo para roles aprobadores) y **Ask about this version**.
- **Chat:** un chip *"Asking about Budget_v1.xlsx (v1, superseded)"* cuando hay una versión fijada, con una × para quitarla.
- **Respuestas:** el pie *"Based on: …"* y un aviso naranja cuando se usa una versión que no es la vigente.
- **Panel de fuente:** *"Version 1 · approved · superseded"*.

## Tests

- **Backend:** 78 pasan, de los cuales 20 son nuevos, en `backend/tests/test_version_control.py`. Cubren la agrupación por nombre, la aprobación, la regla de versión por defecto, el retrieval con y sin versión fijada, el borrado, el historial y `versions_used`. Reemplaza a `test_ingest_supersession.py`: sus mismos casos quedan cubiertos con la regla nueva.
- **Frontend:** 36 tests (6 nuevos) y el typecheck pasan.
- **Nota:** algunos tests de retrieval de BE-013 dependen de que la BD esté vacía. Contra una BD de desarrollo con documentos cargados pueden fallar; contra una BD limpia pasan todos.

## Puntos a revisar en equipo

1. **Cambia una regla de FE-005.** Antes, subir un archivo con el mismo nombre reemplazaba la versión anterior de inmediato. Ahora eso solo pasa si la anterior **no** estaba aprobada; si lo estaba, hay que aprobar la nueva.
2. **La agrupación por nombre es una heurística.** Nombres como "Budget mayo" y "Budget junio" no se agrupan solos (para eso está `version_of`). Nombres sin separador antes de la `v` (`Budgetv2`) tampoco.
3. **La aprobación sí se valida en el backend**, pero el rol viene de `X-User-Role` y se acepta tal cual, igual que el resto del control de acceso. No es seguridad real hasta que haya autenticación.
4. **No hay flujo para "des-aprobar" o rechazar una versión.** Una versión pendiente que no se quiera se borra.
