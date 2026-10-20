# 🛡️ SOC Academy

**Laboratorio interactivo de estudio para Analista SOC N1** — y plataforma de
aprendizaje escalable por diseño (hoy un curso y un estudiante; mañana una
academia con muchos cursos y miles de estudiantes).

Construido a partir de tu roadmap personal: **teoría → ejemplos reales →
ejercicios con feedback inmediato → exámenes cronometrados → reportes
exportables para feedback profundo con IA/mentor.**

---

## 🚀 Inicio rápido

Requisitos: **Python 3.10+** (nada más; el frontend no requiere build).

```bash
cd soc-academy
pip install -r requirements.txt
python run.py
```

Abre **http://localhost:8000** — listo. 🎉

- Documentación interactiva de la API: http://localhost:8000/api/docs
- Tu progreso se guarda en `data/users/default/` (archivos JSON, inspeccionables)

### Tests

```bash
# Backend (motor de evaluación + API): 26 tests
pip install -r requirements-dev.txt
python -m pytest backend/tests/ -q

# Frontend (requiere el server corriendo + npm install)
npm install
node tests/frontend_smoke.mjs
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
(drag & drop), asociar pares (shuffle anti-trampa) y respuesta libre
(revisión pendiente → se exporta en el reporte para feedback humano/IA).

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
| Base de datos real | Implementar `ContentStore`/`ProgressStore` contra Postgres/SQLite; cero cambios en servicios y API |
| Multiusuario / 1000+ estudiantes | `X-User-Id` ya aísla datos por usuario; se sustituye por auth JWT en `deps.py` |
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
soc-academy/
├── run.py                     # arranque local (uvicorn --reload)
├── requirements.txt           # fastapi, uvicorn, pydantic, pyyaml
├── package.json               # solo devDeps de tests frontend (jsdom)
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app factory + static mount
│   │   ├── config.py          # rutas/env (SOCACADEMY_*)
│   │   ├── api/v1/            # cursos, lecciones, exámenes, progreso
│   │   ├── core/              # models, services, evaluation, feedback, errors
│   │   └── storage/           # base (puertos) + json_store (adaptador)
│   └── tests/                 # 26 tests: evaluación + API + anti-fugas
├── content/courses/soc-n1/    # CONTENIDO (YAML versionado)
│   ├── course.yaml
│   ├── modules/<id>/{module.yaml, lessons/<id>.yaml}
│   └── exams/<id>.yaml
├── data/users/<user>/         # DATOS (progreso.json, attempts/, reports/)
├── frontend/                  # SPA sin build (index.html, css/, js/)
├── tests/frontend_smoke.mjs   # smoke test jsdom de todas las vistas
├── tools/                     # generadores de contenido
└── docs/                      # arquitectura + guía de contenido
```

---

## ⏭️ Roadmap del proyecto

1. **Autorar Fase 2** (logs Windows/Sysmon, SIEM/Splunk, triage de IoCs) con el mismo formato
2. Simulador de terminal / queries SPL evaluables (nuevo grader: `siem_query`)
3. Export CSV de progreso + gráficas de evolución por tag
4. Autenticación real y modo multi-estudiante
5. Despliegue (backend Render/Fly + frontend Vercel, o todo en Vercel serverless)
