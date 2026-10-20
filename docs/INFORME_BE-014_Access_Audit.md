# Informe — ID-HU-BE-014: Registro de denegaciones y auditoría de accesos

**Rama:** `feature/be-014-access-audit`. Sale de `feature/be-010-financial-ratio-calculation`, porque reutiliza su motor de ratios.

## Qué resuelve

Antes de este cambio, solo se registraban los accesos **permitidos**. El campo `authorized` de `retrieval_audit_logs` siempre valía `True`, por dos motivos:
- el filtro de confidencialidad saca el contenido restringido en SQL, antes del `LIMIT`, así que la denegación nunca se veía;
- los 403 de `/file` y `/cells` no se guardaban.

Ahora cada intento denegado queda registrado con usuario, fecha y hora, documento y nivel. La información privilegiada no publicada tiene su propio nivel, y un administrador puede filtrar el registro por usuario, documento o periodo.

## Cómo funciona

1. **Módulo central** (`app/audit.py`). Reemplaza los cuatro lugares que antes creaban `RetrievalAuditLog` por su cuenta.
   - `record_granted`: un acceso permitido. Va en la sesión de la petición, como antes.
   - `record_denials`: uno o más accesos denegados. Se escriben **en su propia sesión y con commit inmediato**, así que quedan aunque la petición falle después y se haga rollback (por ejemplo, el 503 del LLM en ratios). Si escribir falla, se deja en el log de la aplicación y el acceso sigue denegado.
   - `AuditContextMiddleware` da a cada petición un `request_id` y un `resource`: `chat`, `query`, `ratios`, `compare`, `resolve`, `cells`, `file` o `audit`. Los hilos paralelos de ratios heredan ese contexto (`contextvars.copy_context`).
   - Dentro de una petición, la misma denegación se registra una sola vez. La búsqueda bilingüe, por ejemplo, ejecuta `retrieve` dos veces.

2. **Puntos de denegación registrados:**

   | Dónde | Acción | Cuándo |
   |---|---|---|
   | `retrieve()` | `retrieve` | Coincidencias **relevantes** que el rol excluye (`inaccessible_matches`). Siempre aplica `RETRIEVAL_MIN_SCORE`, también cuando la extracción de cifras lo desactiva para sus propios resultados. Así una pregunta sin relación no genera denegaciones |
   | Filas leídas directamente (`_as_sources`: extracción y `statement_rows` del chat) | `lookup` | Una fila de la línea pedida con nivel restringido |
   | Precedentes de una fórmula (`formula_context`) | `lookup` | La fórmula depende de una sección que el usuario no puede ver |
   | `extract_figure` (ratios, compare) | `extract` | El documento elegido es restringido para el rol |
   | `/documents/{id}/resolve`, `/cells/...`, `/file` | `resolve`, `view_cell`, `view_file` | Abrir una fuente o el archivo original sin permiso (sin chunk) |
   | `/audit/access-logs` | `audit_query` | Consultar la auditoría sin ser admin |

3. **Información privilegiada no publicada** (`app/retrieval/access.py`):
   - Etiqueta `restricted:insider`. También se aceptan "Pre-release: Insider", "insider" y "pre-release".
   - Requiere `insider-access`, el permiso del rol nuevo `pre-release-disclosure`.
   - **No alcanza el `restricted-access` genérico**: `restricted-reviewer` y `financial-controller` quedan denegados. `admin` sí accede (decisión acordada), y cada evento queda con `is_insider=true`.
   - El chat no incluye ninguna cifra; muestra el aviso genérico "Part of the relevant information is restricted…". Ratios y Compare devuelven "unavailable" sin valor.

4. **Modelo de datos** (migración `0013_access_audit`):
   - `chunk_id` y `document_id` pasan a nullable, para registrar denegaciones de documento completo y de la propia auditoría.
   - Columnas nuevas:
     - `document_filename`, una copia del nombre que sobrevive al borrado del documento;
     - `reason`, `resource`, `request_id` e `is_insider`.
   - Índices: `(authorized, occurred_at)`, `document_filename` y `request_id`.
   - Los registros que ya existían reciben su `document_filename`. La migración aplica desde cero y también en downgrade/upgrade.

## Criterios de aceptación

| Escenario | Resultado |
|---|---|
| **Principal:** sección "restricted:compensation", analista sin `compensation-access` | La respuesta no incluye la sección. Queda un registro `authorized=false` con su usuario, roles, documento, chunk, nivel, motivo ("Requires compensation-access."), pregunta y fecha y hora |
| **Alterno 1:** resultados de Q3 marcados como privilegiados y usuario fuera del grupo | Sin cifras de Q3 en la respuesta (chat, `/query`, ratios). El intento queda con `is_insider=true` y el motivo "requires the pre-release disclosure group". Con el mismo rigor que cualquier otra denegación: también sobrevive al rollback |
| **Alterno 2:** el administrador filtra por "Legal_Contingencies_FY24.pdf" | Aparecen **todos** los intentos, permitidos y denegados, con usuario y fecha. También se puede filtrar por usuario, rango de fechas, resultado, nivel, solo privilegiada y origen; hay paginación y export CSV |

## Prueba manual con la API real (embeddings de OpenAI)

Se subió `samples/sample_financials.xlsx` y se clasificó como "Pre-release: Insider".
- **Analista:** `/query` sobre ingresos devuelve 0 resultados y quedan 8 denegaciones `is_insider` (las 8 secciones relevantes). `/file` responde 403 y se registra.
- **Chat:** `/query/answer` no muestra ninguna cifra, solo el aviso. Quedan 13 denegaciones con `resource=chat`, todas con el mismo `request_id`.
- **Grupo de divulgación previa** (`pre-release-disclosure`): recibe las 8 fuentes.
- **Auditoría:** el analista recibe 403 y ese intento también queda registrado; el admin ve todo lo anterior.

## API

| Endpoint | Cambio |
|---|---|
| `GET /audit/access-logs` | Nuevo, solo admin. Filtros: `user_id`, `document_id`, `document`, `from`, `to`, `authorized`, `tier`, `insider_only` y `resource`. Paginación `limit` (máximo 500) y `offset`. Devuelve `{total, limit, offset, items[]}` |
| `GET /audit/access-logs/export.csv` | Nuevo, solo admin. Mismos filtros. Escapa las celdas que empiezan con `=`, `+`, `-` o `@` |
| `/documents/{id}/resolve`, `/cells/...`, `/file` | Aceptan `X-User-Id`; `/file` también `?user=` |
| `PATCH /documents/{id}/confidentiality` | Acepta `restricted:insider` y sus alias |

## Frontend

- **Campo "User"** en el encabezado. Se envía como `X-User-Id` en todas las llamadas y como `?user=` en el archivo original.
- **Rol nuevo** `pre-release-disclosure` en el selector.
- **Etiqueta `restricted:insider`** en la clasificación de documentos, con distintivo rojo.
- **Pestaña "Audit"** (`AuditView.tsx`), solo con el rol `admin`:
  - filtros: usuario, documento, desde/hasta, resultado, nivel, origen y "solo privilegiada";
  - tabla con fecha, usuario y roles, resultado (verde o rojo), documento, nivel (con el chip "insider"), origen y acción, motivo y pregunta (se expande si es larga);
  - paginación y "Export CSV". Un 403 se explica en vez de mostrar una tabla vacía.

## Tests

- **Backend:** 21 nuevos en `tests/test_access_audit.py`. La suite completa da **441 pasan, 0 fallan** (antes 420).
  - **Política:** alias de la etiqueta, y que `restricted-reviewer`, `restricted-access` y `financial-controller` no ven información privilegiada, mientras que `pre-release-disclosure` y `admin` sí.
  - **Principal:** la denegación trae usuario, documento, nivel, motivo y fecha. Un usuario autorizado queda como permitido. Una pregunta sin relación no registra nada.
  - **Garantías:** la denegación sobrevive al rollback y se deduplica dentro de la petición.
  - **Información privilegiada:** sin divulgación y con `is_insider`, vía `retrieve`, `/query` y `/ratios`. En ratios, los hilos conservan el `request_id`.
  - **Fuente y archivo:** `/resolve`, `/cells` y `/file` quedan registrados sin chunk.
  - **Auditoría:** filtros por documento (por nombre y por id), usuario, resultado y fechas; paginación; CSV; 403 registrado para no-admin. También se verifica que cada denegación tenga usuario, fecha, documento y nivel.
- **Frontend:** 6 nuevos en `AuditView.test.tsx` y 2 en `client.test.ts`. La suite da **68 pasan** y el typecheck queda limpio.

## Limitaciones conocidas

1. **Identidad sin autenticación real.** `X-User-Id` y `X-User-Role` se toman tal como llegan, igual que antes. El registro es tan confiable como esos encabezados. Hace falta la historia de autenticación.
2. **Una fila por chunk denegado** (decisión acordada). Una pregunta puede generar varias filas, agrupadas por `request_id`. La UI todavía no las agrupa por documento.
3. **Sin publicación automática** (decisión acordada). Al publicarse los resultados, la etiqueta privilegiada se cambia a mano.
4. **El aviso de restricción en el chat** revela que *existe* contenido restringido relevante, aunque no su contenido. Es el comportamiento acordado desde BE-013 y se mantiene igual para la información privilegiada.
