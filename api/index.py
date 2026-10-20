"""Entrypoint serverless para Vercel.

Vercel (@vercel/python) busca un objeto ASGI llamado `app` en este archivo.
Reutilizamos la MISMA aplicación FastAPI que en local/Docker: cero duplicación
de lógica. vercel.json incluye backend/, content/ y frontend/ en el bundle y
reenruta todas las peticiones aquí.

⚠ Persistencia: el filesystem de una función Vercel es efímero. KIWI_DATA_DIR
apunta a /tmp, así que el progreso se conserva entre peticiones "calientes"
pero se reinicia en arranques en frío. Para uso serio en Vercel hace falta el
adaptador Postgres/KV (la arquitectura de puertos lo permite sin tocar la
lógica de negocio) — ver docs/deployment.md.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("KIWI_DATA_DIR", "/tmp/kiwi-academy-data")

from backend.app.main import app  # noqa: E402  (import tras preparar path/env)
