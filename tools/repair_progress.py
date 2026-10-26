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

STORE JSON DEGRADADO (rollback incompleto en un proceso en marcha): mientras
está degradado, el store rechaza TODA escritura vía API (StoreDegradedError),
incluido este rebuild, y una instancia nueva de esta herramienta no hereda el
estado degradado (es estado de instancia en memoria). La reparación con
contenido legítimamente distinto del original NO satisface la restauración
exacta (verify_recovery, que exige el sha256 original byte a byte): requiere
la ruta EXPLÍCITA de recuperación semántica sobre la instancia degradada, con
validación de ALCANCE POR ARCHIVO (corrección de auditoría PR #4: un veredicto
genérico sobre los agregados YA NO libera el store; cada archivo afectado que
haya quedado modificado —progreso, intentos, reportes— debe ser acreditado
explícitamente):

    1. reparar progress.json a nivel de archivo con el estado ORIGINAL como
       base (el `.rbak` conservado por el rollback fallido), y recalcular los
       agregados (solo lectura):

           prog = settings.DATA_DIR / "users" / user_id / "progress.json"
           prog.write_bytes(Path(str(prog) + ".rbak").read_bytes())
           reparado = service.compute_repaired_progress(user_id)
           prog.write_text(json.dumps(reparado, ensure_ascii=False, indent=2))

       (o ejecutar esta herramienta con el proceso detenido y reiniciar el
       servidor).
    2. acreditar la recuperación en la instancia degradada con el validador
       de alcance por archivo:

           store.verify_semantic_recovery(service.semantic_recovery_validator())

       que verifica CADA archivo modificado según su tipo: progreso contra
       una reconstrucción independiente (agregados desde los intentos +
       campos no derivados del estado original, `.rbak` verificado por
       sha256 contra el diario), intentos contra el contrato Attempt
       (identidad y pertenencia) y reportes por regeneración determinista
       desde el intento vinculado (existir NO basta). Ante referencias
       incompletas, tipos no reconocidos, errores de lectura o cualquier
       archivo sin verificación suficiente, la recuperación se rechaza y el
       store SIGUE degradado (fallo cerrado).

Los métodos del servicio son de solo lectura y funcionan con el store
degradado; la recuperación solo se concede si TODOS los archivos afectados
pasan su verificación específica.
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
    from backend.app.storage.json_store import StoreDegradedError

    rc = 0
    for u in users:
        try:
            out = service.rebuild_progress_from_attempts(u)
        except StoreDegradedError:
            print(f"✗ {u}: store DEGRADADO (rollback incompleto): rechaza las "
                  "escrituras vía API. Siga el procedimiento de recuperación "
                  "semántica del docstring (compute_repaired_progress + "
                  "verify_semantic_recovery) o repare los archivos y "
                  "reinicie el proceso.")
            rc = 1
            continue
        print(f"✓ {u}: agregados reconstruidos -> {out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
