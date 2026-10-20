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

4. **Multiusuario desde el día 1 (aunque hoy seas uno).** Toda lectura/
   escritura de datos pasa por `user_id` (header `X-User-Id`, default
   `"default"`). La dependencia `get_user_id()` es el único punto a cambiar
   cuando haya autenticación real (JWT/sesión) — el resto del código ya está
   preparado. Los datos de cada usuario viven en directorios separados.

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

### Free text y el loop con IA

Las respuestas libres no se auto-califican de forma definitiva: el motor
calcula cobertura de *keywords* (orientativo) y marca `needs_review=True`.
El reporte Markdown exportable incluye la respuesta, la respuesta modelo, los
conceptos cubiertos/faltantes y un prompt de revisión ya redactado — diseñado
para pegarse en un chat con IA o enviarse a un mentor humano. Las free text
respondidas cuentan para *completar* la lección (están "respondidas"), pero
no suman "dominio" hasta que una revisión futura (manual o IA integrada) lo
decida.

## Almacenamiento hoy → mañana

| Hoy (JSON/YAML archivos) | Mañana (sin reescribir negocio) |
|---|---|
| `content/**.yaml` en git | Misma estructura en tablas `courses/modules/lessons/exercises` + CMS propio, o seguir con git (recomendado: content-as-code) |
| `data/users/<uid>/progress.json` | Tabla `user_progress` (una fila por usuario/lección) |
| `data/users/<uid>/attempts/*.json` | Tabla `attempts` + `attempt_results` |
| Locks por archivo + escritura atómica (tmp+fsync+replace) | Transacciones SQL |
| Un proceso uvicorn local | N procesos/contenedores; el storage ya es stateless |

Para migrar: implementar `SqlProgressStore(ProgressStore)` y cambiar una línea
en `repositories.py` (o inyectar por configuración). Los servicios no cambian.

**Rendimiento:** con archivos + caché de contenido, un proceso aguanta sin
problema decenas de estudiantes activos. El cuello de botella real para
"1000+/día" no es lectura de contenido (cacheable/CDN) sino escritura de
intentos; ahí el salto a DB es el paso natural. 1000 estudiantes/día son
~decenas de writes por minuto: Postgres/SQLite-WAL lo resuelven de sobra.

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
- CORS configurable por env (`SOCACADEMY_CORS_ORIGINS`).

Para producción hará falta: auth real (JWT), rate limiting, y mover `data/`
a un volumen persistente o DB gestionada.

## Despliegue

- **Opción A (simple):** backend completo en Render/Railway/Fly.io
  (contenedor con uvicorn + volumen para `data/`). Frontend ya lo sirve el
  mismo backend.
- **Opción B (Vercel):** frontend estático en Vercel + backend como función
  serverless (Vercel soporta Python). Ojo: el filesystem de Vercel es
  efímero → `data/` necesitaría Vercel KV/Postgres (el puerto `ProgressStore`
  hace ese cambio indoloro).
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
- `tests/frontend_smoke.mjs` — jsdom contra la API real: render de las 5
  vistas, interacción real (click en opción → feedback), examen completo
  (intro → start → responder → entregar → resultados → revisión).
