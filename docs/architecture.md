# Arquitectura y decisiones de diseño

Este documento explica **por qué** el proyecto está estructurado así y cómo
ejecutar cada evolución prevista (de "un estudiante en local" a "academia con
1000+ estudiantes/día").

## Principios

1. **Contenido = datos, no código.** Cursos, lecciones y exámenes viven en
   YAML bajo `content/`. Añadir el curso SOC N2 no toca el backend: es crear
   `content/courses/soc-n2/`. El backend *descubre* el contenido al vuelo
   (con caché TTL de 5 s para desarrollo).

2. **Lógica de negocio aislada de HTTP y del almacenamiento.**
   `core/services.py` no importa nada de FastAPI ni de archivos. Los routers
   son traductores finos. Los stores son intercambiables. Esto permite:
   - Testear la lógica sin servidor (pytest puro).
   - Cambiar JSON → SQLite → Postgres sin reescribir servicios.
   - Reutilizar servicios desde un worker, un bot o una app móvil.

3. **Las respuestas correctas nunca viajan al cliente.** El modelo
   `Exercise.public()` elimina `answer`/`explanation` y además reordena
   aleatoriamente `ordering`/descompone `matching` en `left_items`/
   `right_items` (si llegaran los pares crudos, la asociación correcta sería
   visible en el payload). Hay **tests anti-fuga** que escanean la respuesta
   HTTP completa buscando `"answer"`, `"explanation"`, etc.

4. **Multiusuario real (Iteración 1 implementada).** La identidad proviene
   **exclusivamente del JWT verificado en el backend** (Supabase Auth en
   producción; emisor dev en local — misma verificación, distinto emisor).
   La antigua cabecera `X-User-Id` fue eliminada: ya no se lee en ningún
   punto. `deps.get_current_user()` es el único punto donde se establece la
   identidad; los stores siguen filtrando todos los datos por `user_id`
   (= `sub` del token), y los tests de aislamiento (QA-02) lo verifican con
   pruebas negativas de suplantación.

5. **Historial append-only.** Cada intento (ejercicio suelto o examen) se
   guarda como un archivo JSON inmutable en `data/users/<uid>/attempts/`.
   Ventajas: auditoría completa, cero riesgo de corromper historial,
   migración trivial a una tabla SQL (`un archivo = una fila`), y análisis
   offline con pandas si algún día quieres estadísticas serias.

## Capas

```
HTTP (FastAPI routers, /api/v1)
  └─> Servicios (core/services.LearningService)
        ├─> Motor de evaluación (core/evaluation.GRADERS)
        ├─> Feedback (core/feedback: bandas, weak tags, reportes)
        ├─> ContentRepository  ──> ContentStore (puerto) ──> JsonContentStore (YAML/JSON)
        └─> ProgressRepository ──> ProgressStore (puerto) ──> JsonProgressStore (JSON atómico)
```

### Motor de evaluación extensible

Cada tipo de ejercicio tiene un *grader* puro:
`grader(Exercise, submitted) -> GradeOutcome(correct, ratio, feedback, needs_review, detail)`.
Registro en `GRADERS[type]`. Para añadir un tipo nuevo (p. ej. `siem_query`,
donde el estudiante escribe una query SPL y se evalúa contra un dataset o un
patrón de resultados):

1. Escribir `grade_siem_query(ex, submitted)` en `core/evaluation.py`.
2. Registrarlo: `GRADERS["siem_query"] = grade_siem_query`.
3. Añadir el tipo al `Literal ExerciseType` en `core/models.py`.
4. Añadir el widget correspondiente en `frontend/js/components/exercises.js`
   (un `case` nuevo en el `switch`).

Nada más: intentos, progreso, exámenes y reportes funcionan automáticamente.

### Créditos parciales

Los graders devuelven `ratio` 0.0–1.0, no solo bool: selección múltiple
(aciertos − fallos), multi-blank (por espacio), ordenar (por posición),
matching (por par). Esto da feedback más fino y notas más justas en exámenes.

### Free text y el loop con IA (Iteración 2: revisión humana formalizada)

Las respuestas libres **no puntúan automáticamente**: la cobertura de
keywords se reporta como señal ORIENTATIVA (una coincidencia de palabras no
es prueba de comprensión) y el examen queda en estado `pending_review` con
resultado provisional sobre la parte automática. Un revisor humano autorizado
(`KIWI_REVIEWER_USERS`, allowlist de `sub` resuelta contra el JWT verificado)
consulta `GET /api/v1/reviews/attempts/{id}` (respuesta modelo + cobertura +
respuesta del estudiante) y finaliza con `POST /api/v1/reviews/attempts/{id}`
(puntos por pregunta + feedback), momento en el que se calcula el veredicto
definitivo y se regenera el reporte. El reporte Markdown exportable sigue
incluyendo la respuesta, la respuesta modelo, los conceptos cubiertos/faltantes
y un prompt de revisión ya redactado — diseñado para pegarse en un chat con
IA o enviarse a un mentor humano, que puede sugerir la puntuación que luego
el revisor autorizado registra en la plataforma.

## Almacenamiento hoy → mañana

| Hoy (JSON/YAML archivos) | Mañana (sin reescribir negocio) |
|---|---|
| `content/**.yaml` en git | Misma estructura en tablas `courses/modules/lessons/exercises` + CMS propio, o seguir con git (recomendado: content-as-code) |
| `data/users/<uid>/progress.json` | ✅ **Ya disponible:** tabla `kiwi_progress` vía `SqlProgressStore` (`KIWI_DATABASE_URL=postgresql://...` o `sqlite:///...`) |
| `data/users/<uid>/attempts/*.json` | ✅ **Ya disponible:** tabla `kiwi_attempts` (una fila por intento, mismo JSON) |
| Locks por archivo + escritura atómica (tmp+fsync+replace) | ✅ **Iteración 3:** `transaction()` y `mutate_progress()` en el puerto — SQL con transacciones reales (Postgres `SELECT…FOR UPDATE`, SQLite `BEGIN IMMEDIATE`); JSON con serialización **y rollback real** (diario copy-on-write; si la restauración queda incompleta se lanza `TransactionRollbackError` — nunca se silencia ni se presenta como recuperado — y el store queda **degradado**: una comprobación centralizada rechaza TODAS sus escrituras públicas (transaccionales y directas) hasta `verify_recovery()` satisfactorio o reconstrucción de la instancia; limitación residual ante fallos físicos documentada en `json_store.py`) |
| Un proceso uvicorn local | N procesos/contenedores; el storage ya es stateless |

La migración de datos existentes es un comando:
`KIWI_DATABASE_URL=... python tools/migrate_json_to_sql.py` (idempotente).

**Iteración 3 — integridad:** las operaciones compuestas (envío de examen,
revisión humana) son unidades transaccionales en AMBOS stores: la consistencia
normal no depende de reparación manual. Los intentos (append-only) son la
fuente de verdad; los agregados del progreso son datos derivados y
reconstruibles con `tools/repair_progress.py`
(`LearningService.rebuild_progress_from_attempts`), que queda como mecanismo
ADICIONAL ante catástrofes externas (p. ej. muerte del proceso).
La idempotencia de envíos de examen usa la sesión de servidor como clave:
reenviar una sesión consumida reproduce el resultado original
(`idempotent_replay: true`) sin duplicar intentos. Los reportes tienen guarda
de propiedad: nunca se sobrescribe/reasigna el de otro usuario.
Para cambiar de backend de progreso solo cambia la variable de entorno — la
fábrica (`storage/factory.py`) elige implementación; los servicios no cambian.

**Rendimiento:** con archivos + caché de contenido, un proceso aguanta sin
problema decenas de estudiantes activos. Con `SqlProgressStore` + Postgres
(Supabase pooler en serverless), la escritura de intentos deja de ser el
cuello de botella: 1000 estudiantes/día son ~decenas de writes por minuto,
trivial para Postgres.

## Frontend

SPA **vanilla JS con ES modules, sin build step**: `index.html` + un router
de hash. Razones:

- Un solo comando levanta todo (`python run.py`); cero node_modules para usar la app.
- La vista previa funciona offline (sin CDNs: fuentes del sistema, iconos SVG inline).
- El contrato real es la API REST; cuando el proyecto lo amerite, un frontend
  React/Next puede reemplazarlo carpeta por carpeta (o convivir: servir React
  en `/` y mantener `/api/v1`).

Módulos clave:
- `js/api.js` — único punto de contacto con el backend (+ `X-User-Id`).
- `js/markdown.js` — renderer de un subconjunto de Markdown (teoría), con
  escapeado HTML y whitelist de esquemas en enlaces (sin `eval`, sin CDNs).
- `js/components/exercises.js` — fábrica de widgets por tipo; devuelve
  `{node, getAnswer, showResult, reset}` y no conoce la API (testable).
- `js/views/*` — una función `render*` por pantalla.

## Seguridad (uso local honesto)

Esto es una herramienta de estudio local, no un producto multi-tenant en
producción. Aun así, se aplican las reglas correctas desde ya:

- Respuestas nunca salen del servidor (tests lo verifican).
- `user_id` sanitizado contra path traversal.
- Escapeado de HTML en todo contenido renderizado (el YAML de contenido es
  de confianza, pero el renderer no confía: escapa antes de procesar).
- Escrituras atómicas: un crash no corrompe tu progreso.
- CORS configurable por env (`KIWI_CORS_ORIGINS`).

Para producción hará falta: auth real (JWT), rate limiting, y mover `data/`
a un volumen persistente o DB gestionada.

## Despliegue

> Guía práctica paso a paso (Render, Vercel, Docker): [`docs/deployment.md`](deployment.md).

- **Opción A (simple):** backend completo en Render/Railway/Fly.io
  (contenedor con uvicorn + volumen para `data/`). Frontend ya lo sirve el
  mismo backend.
- **Opción B (Vercel):** frontend + backend como función serverless (ya
  configurado: `vercel.json` + `api/index.py`). El filesystem es efímero →
  define `KIWI_DATABASE_URL` con tu Supabase y la función es 100% stateless
  con persistencia real.
- **Opción C (todo Vercel):** reescribir servicios como API routes de Next.js
  (el diseño por capas lo permite, pero hoy no aporta nada).

Recomendado cuando llegue el momento: **A** para el backend (filesystem
persistente gratis de mentalidad) + Vercel solo para el frontend si se
migra a React.

## Tests

- `backend/tests/test_evaluation.py` — los 6 graders: acierto, fallo, crédito
  parcial, regex, case sensitivity, multi-blank, keywords free text.
- `backend/tests/test_api.py` — flujos E2E con TestClient y datos en tmp:
  catálogo, **anti-fugas**, práctica, lección completa, examen perfecto/cero,
  reportes MD+JSON, dashboard, **aislamiento multiusuario**.
- `backend/tests/test_sql_store.py` — ProgressStore SQL (driver SQLite):
  roundtrip de progreso, intentos (orden/aislamiento/idempotencia), reportes,
  esquema idempotente, fábrica JSON↔SQL por env, e integración completa
  servicio+SQL (ejercicio + examen + reporte).
- `tests/frontend_smoke.mjs` — jsdom contra la API real: render de las 5
  vistas, interacción real (click en opción → feedback), examen completo
  (intro → start → responder → entregar → resultados → revisión).
