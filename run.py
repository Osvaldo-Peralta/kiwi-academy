#!/usr/bin/env python3
"""Arranque local de Kiwi Academy: `python run.py` -> http://localhost:8000

Robusto a propósito:
- Funciona desde cualquier directorio de trabajo.
- Preflight con diagnósticos claros (versión de Python, dependencias, puerto).
- Mensajes de error accionables en español.
- Desarrollo local: KIWI_AUTH_MODE=local por defecto SOLO aquí (run.py es el
  lanzador de desarrollo). En producción (Docker/Vercel/uvicorn directo) el
  default de la aplicación sigue siendo "supabase".
"""
import os
import socket
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Default de DESARROLLO LOCAL: modo local (emisor dev de JWT). La app en sí
# tiene como default "supabase"; esto solo aplica al lanzador de desarrollo.
os.environ.setdefault("KIWI_AUTH_MODE", "local")

HOST = os.environ.get("KIWI_HOST", "127.0.0.1")
PORT = int(os.environ.get("KIWI_PORT", "8000"))

FAIL = "\033[91m✗\033[0m" if sys.stdout.isatty() else "✗"
OK = "\033[92m✓\033[0m" if sys.stdout.isatty() else "✓"


def preflight() -> None:
    print("🥝 Kiwi Academy — verificaciones previas\n")

    # 1. Versión de Python
    v = sys.version_info
    if (v.major, v.minor) < (3, 9):
        sys.exit(
            f"{FAIL} Python {v.major}.{v.minor}.{v.micro} detectado.\n"
            f"   Se necesita Python 3.9+ (recomendado 3.11+).\n"
            f"   Instala una versión reciente: https://www.python.org/downloads/"
        )
    print(f"{OK} Python {v.major}.{v.minor}.{v.micro}")

    # 2. Dependencias
    faltan = []
    for mod, pkg in [("fastapi", "fastapi"), ("uvicorn", "uvicorn"),
                     ("pydantic", "pydantic"), ("yaml", "pyyaml")]:
        try:
            __import__(mod)
        except ImportError:
            faltan.append(pkg)
    if faltan:
        sys.exit(
            f"{FAIL} Faltan dependencias: {', '.join(faltan)}\n"
            f"   Solución:  pip install -r requirements.txt\n"
            f"   (si usas un entorno virtual, actívalo primero: "
            f"python -m venv .venv && source .venv/bin/activate "
            f"— en Windows: .venv\\Scripts\\activate)"
        )
    print(f"{OK} Dependencias (fastapi, uvicorn, pydantic, pyyaml)")

    # 3. Contenido presente
    content = PROJECT_ROOT / "content" / "courses"
    if not content.exists() or not any(content.iterdir()):
        sys.exit(f"{FAIL} No se encuentra contenido en {content}\n"
                 f"   ¿Clonaste el repositorio completo?")
    n = sum(1 for _ in content.iterdir())
    print(f"{OK} Contenido ({n} curso(s) en content/courses/)")

    # 4. Puerto disponible
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((HOST, PORT)) == 0:
            sys.exit(
                f"{FAIL} El puerto {PORT} ya está en uso en {HOST}.\n"
                f"   ¿Otra instancia corriendo? Prueba:  KIWI_PORT=8001 python run.py\n"
                f"   (Windows:  set KIWI_PORT=8001 && python run.py)"
            )
    print(f"{OK} Puerto {PORT} libre en {HOST}")

    # 5. La app importa correctamente
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from backend.app.main import app  # noqa: F401
        from backend.app.config import settings as _s
    except Exception as exc:
        sys.exit(f"{FAIL} La aplicación falló al importar:\n   {type(exc).__name__}: {exc}")
    print(f"{OK} Aplicación FastAPI lista")
    mode = _s.AUTH_MODE
    if mode in ("local", "test"):
        print(f"{OK} KIWI_AUTH_MODE={mode} (desarrollo — emisor dev de JWT; "
              f"en producción el default es 'supabase')")
    else:
        print(f"{OK} KIWI_AUTH_MODE={mode} (requiere SUPABASE_URL y secreto/JWKS)")


def main() -> None:
    preflight()
    import uvicorn

    print(f"\n🚀 Servidor en http://localhost:{PORT}  (Ctrl+C para detener)")
    print(f"   API docs interactivas: http://localhost:{PORT}/api/docs\n")
    uvicorn.run(
        "backend.app.main:app",
        host=HOST,
        port=PORT,
        reload=True,
        reload_dirs=[str(PROJECT_ROOT / "backend"), str(PROJECT_ROOT / "content")],
    )


if __name__ == "__main__":
    main()
