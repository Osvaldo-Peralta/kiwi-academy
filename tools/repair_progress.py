#!/usr/bin/env python3
"""Reconstruye los agregados de progreso desde el historial de intentos.

Los intentos (append-only) son la fuente de verdad; los agregados
(exams/lessons del progreso) son datos derivados. Esta herramienta restaura
la consistencia tras un fallo parcial (p. ej. store JSON ante un crash).

Uso:
    python tools/repair_progress.py <user_id> [<user_id> ...]
    python tools/repair_progress.py --all        # solo store JSON (data/users/*)

Respeta KIWI_DATABASE_URL y la configuración estándar. Idempotente: puede
ejecutarse tantas veces como se quiera; no borra datos no derivados
(teoría/ejemplos marcados, actividad, sesiones).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    all_users = "--all" in sys.argv
    if not args and not all_users:
        print(__doc__)
        return 1

    from backend.app.config import settings
    from backend.app.core.services import LearningService
    from backend.app.storage.repositories import ContentRepository, ProgressRepository

    service = LearningService(ContentRepository(), ProgressRepository())

    if all_users:
        if settings.DATABASE_URL:
            print("✗ --all solo está soportado con el store JSON; con SQL "
                  "indica los user_id explícitamente.")
            return 1
        users_dir = settings.DATA_DIR / "users"
        if not users_dir.exists():
            print(f"✗ No existe {users_dir}")
            return 1
        users = sorted(p.name for p in users_dir.iterdir() if p.is_dir())
    else:
        users = args

    if not users:
        print("Sin usuarios que reparar.")
        return 0
    for u in users:
        out = service.rebuild_progress_from_attempts(u)
        print(f"✓ {u}: agregados reconstruidos -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
