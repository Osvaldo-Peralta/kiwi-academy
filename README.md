# 🥝 Kiwi Academy

**Centro académico con educación de calidad mundial** 🥝 — plataforma de
aprendizaje interactivo escalable por diseño. Su primer curso: **Analista SOC
N1** (hoy un curso y un estudiante; mañana una academia con muchos cursos y
miles de estudiantes).

Construido a partir de tu roadmap personal: **teoría → ejemplos reales →
ejercicios con feedback inmediato → exámenes cronometrados → reportes
exportables para feedback profundo con IA/mentor.**

---

## 🚀 Inicio rápido

Requisitos: **Python 3.10+** (nada más; el frontend no requiere build).

```bash
cd kiwi-academy
pip install -r requirements.txt
python run.py
```

Abre **http://localhost:8000** — listo. 🎉

`run.py` arranca en **modo desarrollo** (`KIWI_AUTH_MODE=local`): la pantalla
de sesión te pedirá un usuario `dev-*` y el backend emitirá un JWT de
desarrollo. **La verificación del token es la misma que en producción**; solo
cambia el emisor. En producción el modo por defecto es `supabase` (identidad
emitida por Supabase Auth, verificada en el backend con HS256 o JWKS).

- Documentación interactiva de la API: http://localhost:8000/api/docs
- Tu progreso se guarda bajo tu identidad JWT en `data/users/<sub>/` (modo archivos) o en SQL (`KIWI_DATABASE_URL`)
- La cabecera `X-User-Id` **ya no existe**: la identidad proviene solo del JWT verificado
- `run.py` hace un **preflight con diagnósticos**: si algo falla (Python viejo,
  dependencias, puerto ocupado) te dice exactamente qué y cómo arreglarlo

### Si algo falla en local

| Síntoma | Causa típica | Solución |
|---------|--------------|----------|
| `ModuleNotFoundError: fastapi` (o yaml/uvicorn/jwt) | Dependencias no instaladas (o venv sin activar) | `pip install -r requirements.txt` |
| `TypeError: unsupported operand type(s) for \|` al arrancar | Python 3.8 o menor | Instala Python 3.9+ (recomendado 3.11+) |
| `address already in use` | Otra instancia corriendo en el puerto 8000 | `KIWI_PORT=8001 python run.py` (Windows: `set KIWI_PORT=8001`) |
| La página carga pero dice "No se pudo contactar la API" | Abriste `index.html` directo (file://) en vez de pasar por el server | Entra siempre por `http://localhost:8000` |
| La API responde `503 auth_not_configured` | Modo `supabase` sin `SUPABASE_URL`/secreto configurados (fail-closed a propósito) | Configura Supabase (`.env`) o usa `python run.py` (modo local) |
| `401` al entrar con usuario sin prefijo `dev-` en local | El emisor dev exige el namespace `dev-*` | Usa p. ej. `dev-osvaldo` |
| `Error: contenido inválido` al cargar un curso | YAML de contenido mal editado | El mensaje incluye archivo y línea; revisa tu última edición |
| Puerto ocupado en Windows sin saber por qué | — | `netstat -ano \| findstr :8000` → `taskkill /PID <pid> /F` |

### Tests

```bash
# Backend (evaluación + API + auth/JWT + integridad de examen + integridad
# de datos/transacciones + SQL): 115 tests
pip install -r requirements-dev.txt
python -m pytest backend/tests/ -q

# Frontend (55 verificaciones; requiere el server en modo test + npm install)
npm install
KIWI_AUTH_MODE=test uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 &
node tests/frontend_smoke.mjs
```

### Despliegue

Listo para **Render** (`render.yaml` + Dockerfile), **Vercel** (`vercel.json` +
`api/index.py`) o **cualquier Docker host**. Guía paso a paso con comparativa
de persistencia de datos en [`docs/deployment.md`](docs/deployment.md).

```bash
# Docker local en 2 comandos:
docker build -t kiwi-academy .
docker run -d -p 8000:8000 -v kiwi-data:/app/data kiwi-academy
```

---

## 📚 El flujo de aprendizaje

```
 Teoría          Ejemplos           Ejercicios            Examen              Feedback
┌──────────┐   ┌──────────┐   ┌────────────────┐   ┌────────────────┐   ┌─────────────────┐
│ Secciones │→ │ Logs y   │→ │ 6 tipos, con   │→ │ 15 preguntas,  │→ │ Automático: por │
│ markdown, │   │ capturas │   │ feedback       │   │ cronometrado,  │   │ pregunta + áreas│
│ objetivos │   │ reales   │   │ inmediato y    │   │ aprobación 70% │   │ débiles.        │
│ y recursos│   │ anotadas │   │ pistas         │   │                │   │ Exportable: MD  │
└──────────┘   └──────────┘   └────────────────┘   └────────────────┘   │ para tu IA      │
                                                                        └─────────────────┘
```

**Tipos de ejercicio soportados:** opción única, selección múltiple (crédito
parcial), completar espacios (multi-blank + regex), ordenar secuencias
(drag & drop), asociar pares (shuffle anti-trampa) y respuesta libre.

**Exámenes con integridad de servidor (Iteración 2):** el servidor emite una
sesión por intento y hace cumplir el límite de tiempo con su propio reloj
(el temporizador del cliente es informativo); los envíos se validan de forma
estricta (ids, duplicados, orden y estructura por tipo). Las respuestas
libres **no puntúan automáticamente** (la cobertura de palabras clave es
orientativa): el examen queda **PENDIENTE DE REVISIÓN HUMANA** con resultado
provisional hasta que un revisor autorizado (`KIWI_REVIEWER_USERS`) asigna
los puntos definitivos vía `POST /api/v1/reviews/attempts/{id}`.

**El loop de feedback con IA:** al terminar un examen pulsa *"Copiar reporte
(Markdown)"* y pégalo en tu chat con Qwen (o tu mentor). El reporte incluye
tus respuestas vs. las correctas, explicaciones, áreas débiles y un prompt de
feedback ya redactado.

---

## 🏗️ Arquitectura

```
┌────────────────────────── frontend/ (SPA vanilla JS, sin build) ─────────────────────────┐
│  index.html · css/ · js/app.js (router hash) · js/views/* · js/components/exercises.js   │
└──────────────────────────────────────────────┬───────────────────────────────────────────┘
                                               │ REST JSON (/api/v1, header X-User-Id)
┌──────────────────────────────────────────────▼───────────────────────────────────────────┐
│ backend/app (FastAPI)                                                                    │
│  api/v1/        routers finos: traducen HTTP ↔ servicios                                 │
│  core/services  LÓGICA DE NEGOCIO (catálogo, intentos, progreso, reportes)               │
│  core/evaluation  motor de evaluación: 1 grader por tipo, registro extensible            │
│  core/feedback    diagnóstico, áreas débiles por tag, reporte Markdown/JSON              │
│  core/models      esquemas Pydantic (contenido + API) — `public()` nunca filtra answers  │
│  storage/base     PUERTOS abstractos (ContentStore, ProgressStore)                       │
│  storage/json_store  implementación archivos (atómica + locks) — hoy JSON/YAML           │
└──────────────┬──────────────────────────────────────────────┬────────────────────────────┘
               │                                              │
   ┌───────────▼───────────┐                      ┌───────────▼───────────┐
   │ content/courses/...   │  solo lectura        │ data/users/<uid>/...  │  lectura/escritura
   │ YAML versionado en git│  (contenido)         │ JSON: progreso,       │  (por estudiante)
   │ curso → módulos →     │                      │ intentos append-only, │
   │ lecciones + exámenes  │                      │ reportes exportados   │
   └───────────────────────┘                      └───────────────────────┘
```

### Decisiones pensadas para escalar

| Necesidad futura | Cómo está preparada hoy |
|---|---|
| Más cursos (SOC N2, etc.) | Un curso = una carpeta YAML en `content/courses/`. La API los descubre sola |
| Más tipos de ejercicio | Escribir 1 función grader + registrarla en `GRADERS` (10 líneas) |
| Base de datos real | ✅ **Ya disponible**: `KIWI_DATABASE_URL=postgresql://...` (Supabase) o `sqlite:///...` — mismo contrato `ProgressStore`, cero cambios en servicios/API |
| Multiusuario / 1000+ estudiantes | ✅ **Iteración 1:** identidad por JWT verificado (Supabase Auth); `X-User-Id` eliminado; datos aislados por `sub` con tests negativos de suplantación |
| Frontend React/móvil | La API REST v1 es el contrato; el frontend actual es reemplazable sin tocar backend |
| Despliegue | Backend: Render/Railway/Fly (uvicorn) o Vercel (serverless). Frontend: Vercel/Netlify estático. `DATA_DIR`/`CONTENT_DIR` via env vars |
| Auditoría/analytics | Intentos append-only en un archivo por intento → migrables a tabla/CSV tal cual |
| Seguridad | Las respuestas correctas NUNCA salen del backend (verificado por tests anti-fuga) |

Detalles en [`docs/architecture.md`](docs/architecture.md).
Guía para crear contenido en [`docs/content-guide.md`](docs/content-guide.md).

---

## 🗺️ Estado del contenido (curso `soc-n1`)

| Fase | Módulo | Estado |
|---|---|---|
| 1 (meses 1-2) | Fundamentos de redes y seguridad | ✅ **Publicada completa**: 3 lecciones (21 secciones de teoría, 8 ejemplos, 24 ejercicios) + examen de 15 preguntas |
| 2 (meses 3-4) | Detección y análisis de amenazas | 🚧 Estructura + objetivos (contenido por autorar) |
| 3 (meses 5-7) | Herramientas y laboratorio práctico | 🚧 Estructura + objetivos |
| 4 (meses 8-10) | Preparación para la entrevista | 🚧 Estructura + objetivos |

Las lecciones `draft` se muestran bloqueadas en la UI y se activan solas al
cambiar su `status: published`.

---

## 📁 Estructura del proyecto

```
kiwi-academy/
├── run.py                     # arranque local (uvicorn --reload)
├── requirements.txt           # fastapi, uvicorn, pydantic, pyyaml
├── package.json               # solo devDeps de tests frontend (jsdom)
├── Dockerfile                 # imagen de producción (Render/Railway/Fly/VPS)
├── render.yaml                # Blueprint de Render (un clic)
├── vercel.json + api/         # despliegue serverless en Vercel
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app factory + static mount
│   │   ├── config.py          # rutas/env (KIWI_*) + fallback /tmp serverless
│   │   ├── api/v1/            # auth/config, cursos, lecciones, exámenes, progreso
│   │   ├── core/              # auth (JWT), models, services, evaluation, feedback, errors
│   │   └── storage/           # base (puertos) + json_store + sql_store + factory
│   └── tests/                 # 141 tests: evaluación + API + auth/JWT + examen + integridad + SQL
├── content/courses/soc-n1/    # CONTENIDO (YAML versionado)
│   ├── course.yaml
│   ├── modules/<id>/{module.yaml, lessons/<id>.yaml}
│   └── exams/<id>.yaml
├── data/users/<user>/         # DATOS (progreso.json, attempts/, reports/)
├── frontend/                  # SPA sin build (index.html, css/, js/)
│   └── js/vendor/             # supabase-js vendorizado + PROVENANCE.md
├── tests/frontend_smoke.mjs   # smoke test jsdom de todas las vistas
├── tools/                     # generadores de contenido
└── docs/                      # arquitectura + contenido + despliegue
```

---

## ⏭️ Roadmap del proyecto

1. **Autorar Fase 2** (logs Windows/Sysmon, SIEM/Splunk, triage de IoCs) con el mismo formato
2. Simulador de terminal / queries SPL evaluables (nuevo grader: `siem_query`)
3. Export CSV de progreso + gráficas de evolución por tag
4. Autenticación real y modo multi-estudiante
5. Despliegue (backend Render/Fly + frontend Vercel, o todo en Vercel serverless)
