# Despliegue

La app es un único proceso (FastAPI sirve la API **y** el frontend estático),
así que desplegar = ejecutar ese proceso en algún sitio. Tres rutas según lo
que necesites:

| Ruta | Costo | Datos persistentes | Ideal para |
|------|-------|--------------------|------------|
| **A. Render (Docker)** | Free / $7 mes | Free: no¹ · Starter: sí (disco) | Uso personal sin configurar DB |
| **B. Vercel (serverless)** | Free | **Sí, con Supabase** (ver abajo) · sin DB: no² | Compartir/demo + uso serio con DB |
| **C. Docker propio (VPS/Fly/Railway)** | Según proveedor | Sí (volumen) | Control total |

¹ En Render free el disco no existe y la instancia se reinicia: el progreso se
pierde en cada redeploy/spin-down (o conecta Supabase y listo). Con plan
Starter + el `disk` de `render.yaml`, persiste en `/app/data`.
² Sin `KIWI_DATABASE_URL`, en Vercel los datos van a `/tmp` de la función:
sobreviven entre peticiones calientes, se pierden en arranques en frío.

---

## Persistencia con base de datos (Supabase o SQLite)

El almacenamiento es intercambiable por variable de entorno — **cero cambios
de código**:

| `KIWI_DATABASE_URL` | Backend | Cuándo usarlo |
|---|---|---|
| *(vacío)* | archivos JSON en `data/users/` | default local, Render con disco, Docker con volumen |
| `sqlite:///data/kiwi.db` | SQLite (stdlib, sin deps) | local persistente, VPS sencillo |
| `postgresql://...` | Postgres/Supabase (requiere `pip install -r requirements-postgres.txt`) | **Vercel/serverless y multi-estudiante** |

### Conectar Supabase (5 minutos, free tier)

1. Crea un proyecto en [supabase.com](https://supabase.com) (free).
2. **Settings → Database → Connection string (URI)**: copia la URI y
   sustituye `[YOUR-PASSWORD]` por tu contraseña. Queda algo así:
   ```
   postgresql://postgres.abcd1234:TU-PASS@aws-0-us-east-1.pooler.supabase.com:6543/postgres
   ```
   (el puerto **6543** es el pooler "transaction" — el correcto para serverless).
3. Instala el driver: `pip install -r requirements-postgres.txt`
4. Define la variable:
   - **Local:** en un archivo `.env` en la raíz del proyecto (hay `.env.example`):
     `KIWI_DATABASE_URL=postgresql://...`
   - **Vercel:** Project → Settings → Environment Variables → añade
     `KIWI_DATABASE_URL` → Redeploy.
   - **Render:** Service → Environment → añade la variable.
5. Las tablas (`kiwi_progress`, `kiwi_attempts`, `kiwi_reports`) se crean
   solas al arrancar.

**Migrar tu progreso existente (JSON → SQL):**
```bash
KIWI_DATABASE_URL="postgresql://..." python tools/migrate_json_to_sql.py
```
Idempotente: puedes correrlo varias veces sin duplicar nada.

## A. Render (recomendado para empezar sin DB)

**Con Blueprint (un clic, usa `render.yaml`):**
1. Entra a [render.com](https://dashboard.render.com) → **New +** → **Blueprint**.
2. Conecta tu cuenta de GitHub y elige `Osvaldo-Peralta/kiwi-academy`.
3. Render detecta `render.yaml` y crea el Web Service (runtime Docker).
4. **Apply** → espera el build (~2-4 min) → abre la URL `*.onrender.com`.

**O manual:** New → Web Service → conectar repo → Runtime: **Docker** →
Health check path: `/healthz` → Create.

Verifica: `https://<tu-app>.onrender.com/healthz` debe responder
`{"status":"ok","app":"Kiwi Academy",...}`.

> Free tier: la primera petición tras inactividad tarda ~50 s (spin-up).
> El progreso se mantiene mientras la instancia viva; se reinicia en redeploys.

## B. Vercel

Ya incluye `vercel.json` + `api/index.py` (entrypoint ASGI). Dos formas:

**Dashboard:** [vercel.com/new](https://vercel.com/new) → importar el repo →
Framework Preset: **Other** → Deploy. Vercel detecta `vercel.json`, instala
`requirements.txt` con `@vercel/python` y reescribe todo el tráfico a la
función FastAPI.

**CLI:**
```bash
npm i -g vercel
vercel login
cd kiwi-academy
vercel --prod
```

⚠ Sin `KIWI_DATABASE_URL` el progreso es efímero (ver tabla). Para uso en
serio aquí, añade la variable con tu URI de Supabase en Project → Settings →
Environment Variables y haz redeploy (guía completa arriba).

## C. Docker en cualquier lugar

```bash
docker build -t kiwi-academy .
docker run -d --name kiwi -p 8000:8000 -v kiwi-data:/app/data kiwi-academy
# http://localhost:8000
```

- **Fly.io:** `fly launch` (detecta el Dockerfile) + `fly volumes create kiwi_data --size 1`
  y monta el volumen en `/app/data`.
- **Railway:** New Project → from GitHub repo → Builder: Dockerfile → añadir
  un Volume montado en `/app/data`.
- **VPS:** igual que Docker local + un reverse proxy (Caddy/nginx) con TLS.

---

## Variables de entorno (todas opcionales)

| Variable | Default | Para qué |
|----------|---------|----------|
| `KIWI_AUTH_MODE` | `supabase` | Identidad: `supabase` (producción) / `local` (desarrollo; `run.py` lo usa) / `test` (fixtures) |
| `SUPABASE_URL` | *(vacío)* | Proyecto Supabase (iss del JWT + JWKS). Obligatorio en modo `supabase` |
| `SUPABASE_JWT_SECRET` | *(vacío)* | Secreto HS256 legacy. **Secreto**: solo backend. Si tu proyecto usa signing keys asimétricas, déjalo vacío (se usará JWKS) |
| `SUPABASE_ANON_KEY` | *(vacío)* | Clave pública del navegador (se sirve vía `/api/v1/config`) |
| `KIWI_JWT_MODE` | `auto` | `auto` (secret si hay secreto, si no jwks) / `secret` / `jwks` |
| `KIWI_DEV_JWT_SECRET` | *(efímero)* | Secreto estable del emisor dev (modo `local`) |
| `KIWI_REVIEWER_USERS` | *(vacío)* | CSV de `sub` autorizados a revisar respuestas libres; vacío = exámenes pendientes sin veredicto |
| `KIWI_EXAM_GRACE_SECONDS` | `30` | Margen de red sobre el límite del examen (lo cumple el servidor) |
| `KIWI_DATABASE_URL` | *(vacío = JSON files)* | `postgresql://...` (Supabase) o `sqlite:///ruta.db` |
| `KIWI_DATA_DIR` | `<repo>/data` (ó `/tmp/kiwi-academy-data` si no hay permisos) | Dónde vive el progreso (modo archivos) |
| `KIWI_CONTENT_DIR` | `<repo>/content` | Cursos/lecciones/exámenes |
| `KIWI_FRONTEND_DIR` | `<repo>/frontend` | SPA estática |
| `KIWI_CORS_ORIGINS` | `*` | Restringir en producción (lista CSV) |
| `KIWI_HOST` / `KIWI_PORT` | `127.0.0.1` / `8000` | Bind del `python run.py` local |

> Nota Iteración 1: `KIWI_DEFAULT_USER` fue **eliminado** — ya no existe
> usuario implícito; la identidad viene solo del JWT verificado.
> En modo `supabase` sin configurar, los endpoints privados responden
> `503 auth_not_configured` (fail-closed): nunca hay identidad implícita.

## Backup / migración de datos

**Modo archivos:** todo el estado del estudiante vive bajo `KIWI_DATA_DIR`:
```
data/users/<user>/progress.json     # avance actual
data/users/<user>/attempts/*.json   # historial append-only (1 archivo = 1 intento)
data/users/<user>/reports/*         # reportes exportados
```
Copiar esa carpeta = backup completo. Restaurarla en otro despliegue =
migración completa.

**Modo SQL:** backup = dump de las tablas `kiwi_progress`, `kiwi_attempts`,
`kiwi_reports` (en Supabase: Database → Backups, o `pg_dump`). La herramienta
`tools/migrate_json_to_sql.py` mueve archivos → SQL cuando quieras dar el salto.

## Camino a producción multi-estudiante (1000+/día)

Con el adaptador SQL ya implementado, la secuencia es:

1. ✅ `SqlProgressStore` contra **Postgres/Supabase** — hecho (este commit).
2. Autenticación real: sustituir `get_user_id()` (hoy header `X-User-Id`) por
   JWT/sesiones (Supabase Auth encaja natural).
3. Contenido estático + API detrás de CDN; el contenido YAML sigue en git
   (content-as-code) o migra a un CMS propio.
4. Si el tráfico crece: pooling de conexiones (Supabase pooler ya lo cubre)
   y caché de contenido compartida.
