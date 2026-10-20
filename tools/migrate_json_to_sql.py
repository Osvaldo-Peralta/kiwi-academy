#!/usr/bin/env python3
"""Migra los datos de estudiante de archivos JSON a SQL.

Uso:
    # a SQLite local:
    KIWI_DATABASE_URL="sqlite:///data/kiwi.db" python tools/migrate_json_to_sql.py

    # a Supabase/Postgres:
    KIWI_DATABASE_URL="postgresql://postgres.xxx:PASS@aws-0-xxx.pooler.supabase.com:6543/postgres" \
        python tools/migrate_json_to_sql.py

Idempotente: los intentos ya migrados (mismo id) se ignoran; progreso y
reportes se sobrescriben con la versión de los archivos.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    url = os.environ.get("KIWI_DATABASE_URL", "").strip()
    if not url:
        print("✗ Define KIWI_DATABASE_URL (postgresql://... o sqlite:///ruta.db)")
        print('  Ejemplo:  KIWI_DATABASE_URL="sqlite:///data/kiwi.db" python tools/migrate_json_to_sql.py')
        return 1

    from backend.app.storage.sql_store import SqlProgressStore

    store = SqlProgressStore(url)
    users_dir = ROOT / "data" / "users"
    if not users_dir.exists():
        print(f"✗ No existe {users_dir} — nada que migrar.")
        return 1

    totals = {"users": 0, "progress": 0, "attempts": 0, "reports": 0}
    for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
        user_id = user_dir.name
        totals["users"] += 1
        print(f"→ usuario '{user_id}'")

        prog_file = user_dir / "progress.json"
        if prog_file.exists():
            store.save_progress(user_id, json.loads(prog_file.read_text(encoding="utf-8")))
            totals["progress"] += 1
            print("  ✓ progress.json")

        att_dir = user_dir / "attempts"
        if att_dir.exists():
            for f in sorted(att_dir.glob("*.json")):
                attempt = json.loads(f.read_text(encoding="utf-8"))
                store.save_attempt(user_id, attempt)
                totals["attempts"] += 1
            print(f"  ✓ {totals['attempts']} intento(s) acumulado(s)")

        rep_dir = user_dir / "reports"
        if rep_dir.exists():
            for f in sorted(rep_dir.iterdir()):
                if not f.is_file():
                    continue
                # nombre de archivo: <attempt_id>.<name>
                attempt_id, _, name = f.stem.partition(".")
                name = (name + f.suffix) if name else f.name
                store.save_report(user_id, attempt_id, name or "report.md",
                                  f.read_text(encoding="utf-8"))
                totals["reports"] += 1
            print(f"  ✓ reportes: {totals['reports']} acumulado(s)")

    print("\n✅ Migración completa:", totals)
    print("   Ahora la app usará SQL automáticamente mientras KIWI_DATABASE_URL esté definida.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
