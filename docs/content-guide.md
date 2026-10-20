# Guía de contenido (para autores)

Todo el contenido vive en `content/courses/<course-id>/` como archivos YAML
versionados en git. El backend los descubre automáticamente: **crear contenido
no requiere tocar código**. Con `python run.py` activo, los cambios se ven al
recargar la página (caché de 5 s).

```
content/courses/soc-n1/
├── course.yaml                     # metadata del curso
├── modules/
│   └── 01-fundamentos/
│       ├── module.yaml             # metadata del módulo/fase
│       └── lessons/
│           └── 01-modelo-osi-protocolos.yaml
└── exams/
    └── fase-1-examen.yaml
```

## course.yaml

```yaml
id: soc-n1                # único; se usa en URLs
title: "Analista SOC — Nivel 1 (N1)"
description: >-
  Texto largo...
language: es
level: beginner
estimated_weeks: "24-40"
tags: [soc, blue-team]
modules:                  # ids de módulo, en orden
  - 01-fundamentos
  - 02-deteccion
meta:                     # dict libre (la UI muestra "perfil", "meta", "ventaja_dev")
  perfil: "..."
```

## module.yaml

```yaml
id: 01-fundamentos
course_id: soc-n1
title: "Fundamentos de redes y seguridad"
phase: 1                          # número de fase (UI)
subtitle: "La base que todo analista SOC debe tener sólida"
duration: "Meses 1-2"
outcome: "Al terminar sabrás..."  # objetivo observable del módulo
lessons:                          # ids de lección, en orden
  - 01-modelo-osi-protocolos
exam: fase-1-examen               # id de examen o null
resources:
  - title: "TryHackMe — Pre-Security"
    url: "https://..."
    type: lab                     # lab|video|curso|libro|referencia|empleo
    note: "Recurso principal"
```

## lesson (lección)

```yaml
id: 01-modelo-osi-protocolos
module_id: 01-fundamentos
course_id: soc-n1
title: "Redes: modelo OSI / TCP-IP"
kind: teoria                      # teoria | practica | herramienta (badge UI)
estimated_minutes: 120
status: published                 # published | draft (draft = bloqueada en UI)
summary: >-
  Una frase que vende la lección.
objectives:
  - "Objetivo observable 1"
theory:                           # secciones de teoría (orden = orden de lectura)
  - id: t1
    section: "Título de sección"
    format: markdown              # markdown | log | code | command
    language: bash                # opcional, para log/code
    body: |-
      Markdown con **negritas**, `código`, tablas |...|,
      listas, > citas y bloques ```cercados```.
examples:                         # mismos campos que theory
  - id: e1
    section: "Ejemplo 1 — ..."
    format: markdown
    body: |-
      ...
exercises: [ ... ]                # ver abajo
```

El renderer markdown soporta: `##`/`###`, **bold**, *italic*, `code`,
```bloques```, tablas, listas `-`/`1.`, `> citas`, `---`, [enlaces](https://).

## exercises — los 6 tipos

Campos comunes a todos:

```yaml
- id: l1-e1                 # único dentro de la lección/examen
  type: <tipo>
  prompt: "Enunciado"
  context:                  # OPCIONAL: log/código que acompaña la pregunta
    format: log             # log | code | command | markdown
    language: text
    body: |-
      líneas de log...
  tags: [osi, tcp]          # CLAVE: alimenta el diagnóstico de áreas débiles
  difficulty: 1             # 1-3
  points: 10                # peso en la nota
  hint: "Pista opcional (se muestra al fallar)"
  answer: <ver por tipo>    # ⚠ nunca sale al frontend
  explanation: "Por qué esa es la respuesta (feedback pedagógico)"
```

### 1. multiple_choice

```yaml
type: multiple_choice
options:
  - {id: a, text: "Opción A"}
  - {id: b, text: "Opción B"}
answer: "b"                 # id de la opción correcta
```

### 2. multiple_select (crédito parcial: aciertos − fallos)

```yaml
type: multiple_select
options:
  - {id: a, text: "..."}
  - {id: b, text: "..."}
  - {id: c, text: "..."}
answer: [a, c]              # ids correctos
```

### 3. fill_blank (uno o varios espacios)

```yaml
# un espacio:
type: fill_blank
answer:
  accepted: ["TXT", "txt registro"]   # variantes aceptadas
  case_sensitive: false                # opcional (default false)
  # regex: "pattern"                   # alternativa/complemento (fullmatch)

# varios espacios (el texto usa [___1], [___2]...):
type: fill_blank
text: "SYN = [___1] · SYN-ACK = [___2]"
answer:
  blanks:
    - {accepted: ["S"], case_sensitive: true}
    - {accepted: ["S."], case_sensitive: true}
```

### 4. ordering (el servidor los baraja al entregar)

```yaml
type: ordering
items:
  - {id: h1, text: "Paso A"}
  - {id: h2, text: "Paso B"}
  - {id: h3, text: "Paso C"}
answer: [h3, h1, h2]        # ids en el orden correcto
```

### 5. matching (el servidor separa y baraja el lado derecho)

```yaml
type: matching
pairs:
  - {left_id: s1, left: "SSH", right_id: p1, right: "22"}
  - {left_id: s2, left: "DNS", right_id: p2, right: "53"}
answer: {s1: p1, s2: p2}    # left_id -> right_id
```

### 6. free_text (revisión pendiente → reporte exportable)

```yaml
type: free_text
answer:
  model_answer: >-
    La respuesta modelo completa (aparece en el reporte exportable).
  keywords: ["tunneling", "aislar", "bloquear", "escalar"]
  min_keywords: 3            # cobertura mínima para "buen indicio"
```

## Exámenes (exam)

Mismo formato de ejercicios, en `questions`:

```yaml
id: fase-1-examen
course_id: soc-n1
module_id: 01-fundamentos     # opcional
title: "Examen Fase 1 — ..."
description: >-
  Qué evalúa y cómo.
time_limit_minutes: 30        # null = sin límite (auto-entrega al agotarse)
pass_score: 70                # porcentaje para aprobar
attempts_allowed: null        # null = ilimitados
questions: [ <exercise>, ... ]
```

Convenciones recomendadas: mezclar dificultades (1/2/3), ~15 preguntas,
incluir 1 free_text de síntesis (entra al reporte para feedback con IA),
y **tags consistentes con las lecciones** (el diagnóstico cruza tags →
lecciones donde repasarlo).

## Cómo se calcula el progreso

- **Ejercicio dominado:** acertado al menos una vez (autocorregibles).
- **Free text respondida:** enviada al menos una vez (queda "pendiente de revisión").
- **Lección completada:** teoría marcada como leída + todos los
  autocorregibles dominados + todas las free text respondidas.
- **Examen aprobado:** percent ≥ `pass_score`. Se guarda el mejor intento.
- Todo intento (incluso ejercicios sueltos) queda en el historial.

## Añadir un CURSO NUEVO (p. ej. SOC N2)

1. `mkdir content/courses/soc-n2/{modules,exams}`
2. Crear `course.yaml` con su `id: soc-n2` y lista de módulos.
3. Crear módulos y lecciones con el formato de arriba.
4. Recargar la UI: el curso aparece solo en el panel y en `/api/v1/courses`.

No hay registros, ni migraciones, ni código. Ese es el diseño.

## Checklist de calidad por lección

- [ ] `objectives` observables ("sabrás leer X", no "entenderás Y")
- [ ] Teoría con tablas/ejemplos SOC reales (logs anotados), no definiciones secas
- [ ] ≥2 ejemplos con logs/capturas reales comentadas línea a línea
- [ ] ≥6 ejercicios, mezclando tipos y dificultades, todos con `explanation`
- [ ] Tags consistentes con otras lecciones (alimentan el diagnóstico)
- [ ] ≥1 free_text de triage/análisis (dificultad 3) con keywords exigentes
- [ ] Examen de módulo con preguntas nuevas (no recicladas de ejercicios)
