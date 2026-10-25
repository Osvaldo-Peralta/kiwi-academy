"""Implementación del almacenamiento con archivos JSON.

- Escrituras atómicas (tmp + os.replace) para no corromper datos si el
  proceso muere a mitad de escritura.
- Lock por ruta para concurrencia básica en local (varios workers/hilos).
- Los intentos se guardan append-only (un archivo por intento): historial
  auditable completo, fácil de migrar a DB o analizar con pandas después.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from ..config import settings
from ..core.errors import ContentError
from .base import ContentStore, ProgressStore

_STORE_LOCK = threading.RLock()   # lock global del store JSON (single-proceso)

log = logging.getLogger("kiwi.storage.json")


class TransactionRollbackError(RuntimeError):
    """La operación original falló Y la restauración del diario falló total o
    parcialmente: el almacenamiento puede haber quedado en estado parcial y
    NO debe presentarse como recuperado.

    Atributos (evidencia para diagnóstico):
      original: excepción que abortó la transacción.
      failures: [(ruta, error), ...] archivos que NO pudieron restaurarse/borrarse.
      restored: [ruta, ...] archivos que SÍ se restauraron/borraron.

    El evento queda además logueado (logging.error) y registrado en
    `JsonProgressStore.rollback_failures` (evidencia en memoria del proceso).
    Los archivos temporales `.rbak` de restauraciones fallidas se conservan
    en disco como evidencia forense y material de recuperación manual.

    Consecuencia (corrección de auditoría): el store queda marcado como
    DEGRADADO y rechaza TODAS sus operaciones públicas de escritura —
    `transaction()`, `mutate_progress()`, `save_progress()`,
    `save_attempt()`, `update_attempt()`, `update_attempt_if_status()` y
    `save_report()` (comprobación centralizada `_ensure_not_degraded`) —
    hasta una recuperación explícita y verificada: restauración exacta
    (`JsonProgressStore.verify_recovery()`, que compara el contenido real
    contra el sha256 original registrado antes del rollback) o reparación
    semántica acreditada (`verify_semantic_recovery(validator)`), o la
    reconstrucción de la instancia tras reparar los archivos. La finalidad
    no es garantizar la recuperación ante cualquier fallo físico, sino
    impedir que el sistema siga escribiendo como si estuviera íntegro.

    LIMITACIÓN RESIDUAL (documentada): el rollback basado en archivos no puede
    garantizar la recuperación completa ante cualquier fallo del sistema
    operativo o del almacenamiento (p. ej. disco lleno, permisos revocados,
    muerte del proceso). En ese caso esta excepción informa el fallo con el
    detalle disponible; la reparación asistida es `tools/repair_progress.py`
    (los intentos append-only son la fuente de verdad). No se afirma
    atomicidad absoluta frente a fallos físicos o externos.
    """

    def __init__(self, original: BaseException, failures: list, restored: list):
        self.original = original
        self.failures = list(failures)
        self.restored = list(restored)
        detalle = "; ".join(f"{p}: {e}" for p, e in self.failures[:5])
        super().__init__(
            f"Rollback de transacción JSON INCOMPLETO: {len(self.failures)} "
            f"archivo(s) no restaurado(s) [{detalle}]. Error original: "
            f"{type(original).__name__}: {original}. El estado del almacén "
            f"puede ser inconsistente: NO se asuma recuperado; diagnostique "
            f"(rollback_failures / logs) y considere tools/repair_progress.py."
        )

class StoreDegradedError(RuntimeError):
    """El store JSON está DEGRADADO: un rollback anterior quedó incompleto y
    los datos pueden ser inconsistentes. Se rechazan TODAS las operaciones
    públicas de escritura (transaccionales y directas, vía la comprobación
    centralizada `_ensure_not_degraded`, que se evalúa bajo el lock global
    dentro de la misma sección crítica que la escritura) hasta:
      (a) ejecutar `JsonProgressStore.verify_recovery()` con resultado
          satisfactorio — restauración EXACTA: el contenido de cada archivo
          afectado se compara byte a byte (sha256 registrado en el diario
          antes del rollback) contra el estado original esperado, y los
          archivos que debían eliminarse deben realmente no existir; un
          JSON meramente válido con estado parcial NO la satisface —, o
      (b) ejecutar `JsonProgressStore.verify_semantic_recovery(validator)`
          — ruta EXPLÍCITA de recuperación SEMÁNTICA para reparaciones con
          contenido legítimamente distinto del original (p. ej. agregados
          reconstruidos con tools/repair_progress.py): exige un validador
          que acredite positivamente la reparación, o
      (c) reconstruir una instancia del store después de recuperar los
          archivos (p. ej. restaurando desde los `.rbak` conservados y/o
          usando tools/repair_progress.py).
    Las operaciones de LECTURA no se bloquean (necesarias para diagnosticar)."""

    def __init__(self, degraded_info: dict):
        self.degraded = degraded_info
        super().__init__(
            "JsonProgressStore DEGRADADO (rollback incompleto previo): "
            "escrituras transaccionales y directas rechazadas hasta "
            "recuperación verificada —exacta (verify_recovery) o semántica "
            "acreditada (verify_semantic_recovery)— o reconstrucción de la "
            "instancia. Detalles: " + json.dumps(degraded_info, ensure_ascii=False, default=str)[:600]
        )


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: str) -> threading.RLock:
    # RLock (reentrante): permite componer operaciones CAS que reutilizan
    # _write_json bajo el mismo lock de ruta.
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.RLock())


CONTENT_EXTENSIONS = (".yaml", ".yml", ".json")


def _read_data(path: Path) -> Optional[dict[str, Any]]:
    """Lee un archivo de datos (YAML o JSON según extensión)."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            if path.suffix in (".yaml", ".yml"):
                import yaml
                return yaml.safe_load(f)
            return json.load(f)
    except Exception as exc:  # YAML/JSON malformado -> error claro, no 500 críptico
        raise ContentError(f"Archivo de contenido inválido en {path}: {exc}") from exc


def _resolve(stem: Path) -> Path:
    """Dado un path sin extensión, devuelve el primero que exista (.yaml/.yml/.json)."""
    for ext in CONTENT_EXTENSIONS:
        candidate = stem.with_suffix(ext)
        if candidate.exists():
            return candidate
    return stem.with_suffix(".json")  # no existe; _read_data devolverá None


# alias de compatibilidad
_read_json = _read_data


def _write_json(path: Path, data: Any) -> None:
    with _lock_for(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


class JsonContentStore(ContentStore):
    """Lee el contenido desde content/courses/<course_id>/...

    Estructura esperada:
        course.json
        modules/<module_id>/module.json
        modules/<module_id>/lessons/<lesson_id>.json
        exams/<exam_id>.json
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or settings.CONTENT_DIR) / "courses"

    def _course_dir(self, course_id: str) -> Path:
        return self.root / course_id

    def list_courses(self) -> list[dict[str, Any]]:
        out = []
        if not self.root.exists():
            return out
        for entry in sorted(self.root.iterdir()):
            meta = _read_data(_resolve(entry / "course"))
            if meta:
                out.append(meta)
        return out

    def get_course(self, course_id: str) -> Optional[dict[str, Any]]:
        return _read_data(_resolve(self._course_dir(course_id) / "course"))

    def list_modules(self, course_id: str) -> list[dict[str, Any]]:
        course = self.get_course(course_id)
        if not course:
            return []
        out = []
        for mid in course.get("modules", []):
            mod = _read_data(_resolve(self._course_dir(course_id) / "modules" / mid / "module"))
            if mod:
                out.append(mod)
        return out

    def get_module(self, course_id: str, module_id: str) -> Optional[dict[str, Any]]:
        return _read_data(_resolve(self._course_dir(course_id) / "modules" / module_id / "module"))

    def get_lesson(self, course_id: str, module_id: str, lesson_id: str) -> Optional[dict[str, Any]]:
        stem = self._course_dir(course_id) / "modules" / module_id / "lessons" / lesson_id
        return _read_data(_resolve(stem))

    def get_exam(self, course_id: str, exam_id: str) -> Optional[dict[str, Any]]:
        return _read_data(_resolve(self._course_dir(course_id) / "exams" / exam_id))

    def all_lessons(self, course_id: str) -> list[dict[str, Any]]:
        out = []
        for mod in self.list_modules(course_id):
            for lid in mod.get("lessons", []):
                lesson = self.get_lesson(course_id, mod["id"], lid)
                if lesson:
                    out.append(lesson)
        return out

    def all_exams(self, course_id: str) -> list[dict[str, Any]]:
        out = []
        exams_dir = self._course_dir(course_id) / "exams"
        if exams_dir.exists():
            files = set()
            for ext in CONTENT_EXTENSIONS:
                files.update(exams_dir.glob(f"*{ext}"))
            for f in sorted(files):
                data = _read_data(f)
                if data:
                    out.append(data)
        return out


class JsonProgressStore(ProgressStore):
    """Datos por usuario en data/users/<user_id>/."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or settings.DATA_DIR) / "users"
        self._local = threading.local()   # diario de la transacción en curso
        # Evidencia en memoria de rollbacks incompletos (diagnóstico):
        # [{when, user_id, original, restored, failures}, ...]
        self.rollback_failures: list[dict[str, Any]] = []
        # Estado degradado activo (None = store íntegro/operativo). Se marca
        # cuando un rollback queda incompleto; se limpia SOLO mediante
        # verify_recovery() satisfactorio (o creando una instancia nueva tras
        # reparar los archivos).
        self._degraded: Optional[dict[str, Any]] = None

    @property
    def degraded(self) -> Optional[dict[str, Any]]:
        """Registro del estado degradado vigente, o None si el store está íntegro."""
        return self._degraded

    def _ensure_not_degraded(self) -> None:
        """Comprobación CENTRALIZADA (corrección de auditoría): ningún
        método público de escritura puede ejecutarse con el store degradado.
        Se evalúa ANTES de cualquier modificación de datos.

        Endurecimiento de hilos (corrección de auditoría PR #4): la
        comprobación adquiere por sí misma el lock global del store
        (`_STORE_LOCK`, RLock reentrante: los llamadores que ya lo tienen
        —`transaction()`, `mutate_progress()`, las escrituras directas y las
        verificaciones de recuperación— reentran sin bloqueo adicional).
        Además, TODAS las escrituras directas mantienen `_STORE_LOCK` a lo
        largo de "comprobación + escritura", y la degradación por rollback
        incompleto también se marca bajo ese lock (dentro de `transaction()`):
        las secciones críticas son mutuamente exclusivas, por lo que no
        existe ventana en la que un hilo supere la comprobación y el store
        quede degradado antes de materializar la escritura."""
        with _STORE_LOCK:
            if self._degraded is not None:
                raise StoreDegradedError(self._degraded)

    # -- diario copy-on-write (rollback real en operaciones compuestas) ------

    def _record_original(self, path: Path) -> None:
        """Registra el contenido original (o la ausencia) de un archivo antes
        de la primera escritura dentro de la transacción activa."""
        journal = getattr(self._local, "journal", None)
        if journal is None:
            return
        key = str(path)
        if key not in journal:
            journal[key] = path.read_bytes() if path.exists() else None

    def _write_json_journaled(self, path: Path, data: Any) -> None:
        self._record_original(path)
        _write_json(path, data)

    def _rollback(self, journal: dict) -> tuple[list[str], list[tuple[str, str]]]:
        """Restaura los archivos registrados (borra los creados), en orden
        inverso. Best-effort POR ARCHIVO (un fallo no impide intentar el
        resto) pero NUNCA silencia errores: devuelve (restaurados, fallos)
        y loguea cada fallo. El llamador decide cómo comunicarlo."""
        restored: list[str] = []
        failures: list[tuple[str, str]] = []
        for path_str in reversed(list(journal.keys())):
            original = journal[path_str]
            path = Path(path_str)
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    tmp = path.with_suffix(path.suffix + ".rbak")
                    tmp.write_bytes(original)
                    os.replace(tmp, path)
                restored.append(path_str)
            except OSError as exc:
                failures.append((path_str, f"{type(exc).__name__}: {exc}"))
                log.error("Rollback JSON: no se pudo restaurar %s: %s", path_str, exc)
        return restored, failures

    # -- progreso -----------------------------------------------------------

    def _progress_path(self, user_id: str) -> Path:
        base = self.root / user_id
        base.mkdir(parents=True, exist_ok=True)
        return base / "progress.json"

    def load_progress(self, user_id: str) -> dict[str, Any]:
        data = _read_json(self._progress_path(user_id))
        return data or {"user_id": user_id, "lessons": {}, "exams": {}, "activity": {}}

    def save_progress(self, user_id: str, progress: dict[str, Any]) -> None:
        with _STORE_LOCK:      # comprobación + escritura: una sola sección crítica
            self._ensure_not_degraded()
            self._write_json_journaled(self._progress_path(user_id), progress)

    # -- intentos (append-only) ---------------------------------------------

    def _attempts_dir(self, user_id: str) -> Path:
        d = self.root / user_id / "attempts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_attempt(self, user_id: str, attempt: dict[str, Any]) -> None:
        with _STORE_LOCK:      # comprobación + escritura: una sola sección crítica
            self._ensure_not_degraded()
            self._write_json_journaled(self._attempts_dir(user_id) / f"{attempt['id']}.json", attempt)

    def update_attempt(self, user_id: str, attempt: dict[str, Any]) -> bool:
        with _STORE_LOCK:      # comprobación + lectura + escritura: una sola sección crítica
            self._ensure_not_degraded()
            path = self._attempts_dir(user_id) / f"{attempt['id']}.json"
            if not path.exists():
                return False  # guarda de propiedad: solo actualiza lo existente
            self._write_json_journaled(path, attempt)
            return True

    def update_attempt_if_status(
        self, user_id: str, attempt_id: str,
        expected_status: str, attempt: dict[str, Any],
    ) -> bool:
        """CAS atómico en el proceso: lectura + comprobación + escritura bajo
        el mismo lock de ruta (RLock). Exactamente un hilo ganador."""
        with _STORE_LOCK:      # comprobación + CAS: una sola sección crítica
            self._ensure_not_degraded()
            safe = "".join(c for c in attempt_id if c.isalnum() or c in "-_")
            path = self._attempts_dir(user_id) / f"{safe}.json"
            with _lock_for(str(path)):
                current = _read_json(path)
                if not current:
                    return False
                if (current.get("score") or {}).get("status") != expected_status:
                    return False
                self._write_json_journaled(path, attempt)
                return True

    def get_attempt(self, user_id: str, attempt_id: str) -> Optional[dict[str, Any]]:
        safe = "".join(c for c in attempt_id if c.isalnum() or c in "-_")
        return _read_json(self._attempts_dir(user_id) / f"{safe}.json")

    def find_attempt_any_user(self, attempt_id: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        safe = "".join(c for c in attempt_id if c.isalnum() or c in "-_")
        if not self.root.exists():
            return None, None
        for user_dir in sorted(self.root.iterdir()):
            if not user_dir.is_dir():
                continue
            data = _read_json(user_dir / "attempts" / f"{safe}.json")
            if data:
                return user_dir.name, data
        return None, None

    def list_attempts(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        files = sorted(self._attempts_dir(user_id).glob("*.json"), reverse=True)[:limit]
        out = []
        for f in files:
            data = _read_json(f)
            if data:
                out.append(data)
        return out

    # -- reportes exportados ----------------------------------------------------------------

    def save_report(self, user_id: str, attempt_id: str, name: str, content: str) -> Optional[str]:
        # Propiedad estructural: los reportes viven bajo el directorio del
        # usuario. Además (auditoría Iter 3, punto 3): solo se crea/actualiza
        # un reporte si el intento EXISTE y pertenece a este usuario — se
        # rechaza vincular reportes a intentos ajenos o inexistentes.
        with _STORE_LOCK:      # comprobación + guarda + escritura: una sola sección crítica
            self._ensure_not_degraded()
            safe_attempt = "".join(c for c in attempt_id if c.isalnum() or c in "-_")
            att_path = self.root / user_id / "attempts" / f"{safe_attempt}.json"
            if not att_path.exists():
                return None    # intento inexistente o de otro usuario: denegado
            d = self.root / user_id / "reports"
            d.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(c for c in name if c.isalnum() or c in "-_.")
            path = d / f"{safe_attempt}.{safe_name}"
            with _lock_for(str(path)):
                self._record_original(path)
                path.write_text(content, encoding="utf-8")
            return str(path)

    # -- Iteración 3: atomicidad (single-proceso por diseño) -------------------

    @contextmanager
    def transaction(self, user_id: str) -> Iterator[None]:
        """Serializa Y revierte operaciones compuestas dentro del proceso:
        lock global + diario copy-on-write. Si el bloque lanza, todos los
        archivos escritos durante la transacción se restauran a su estado
        original (o se borran si no existían). El store JSON es
        single-proceso por diseño; la garantía multi-worker la da el SQL."""
        with _STORE_LOCK:
            # Corrección de auditoría: un store degradado NO admite nuevas
            # escrituras transaccionales hasta recuperación verificada.
            self._ensure_not_degraded()
            if getattr(self._local, "journal", None) is not None:
                yield          # anidada: se une a la transacción externa
                return
            self._local.journal = {}
            try:
                yield
            except BaseException as exc:
                journal = self._local.journal
                # Estado definido ANTES de intentar la restauración: ninguna
                # transacción posterior puede reutilizar un diario obsoleto,
                # incluso si la propia restauración falla.
                self._local.journal = None
                restored, failures = self._rollback(journal)
                if failures:
                    # La operación original falló Y la recuperación quedó
                    # incompleta: se comunica explícitamente conservando
                    # ambas evidencias (excepción encadenada + registro) y se
                    # marca el store como DEGRADADO (rechaza nuevas escrituras
                    # transaccionales hasta verify_recovery()).
                    record = {
                        "when": datetime.now(timezone.utc).isoformat(),
                        "user_id": user_id,
                        "original": f"{type(exc).__name__}: {exc}",
                        "restored": restored,
                        "failures": failures,
                        # Estado esperado REAL tras el rollback, para CADA
                        # archivo tocado por la transacción fallida (no solo
                        # los de restauración fallida). Corrección de
                        # auditoría PR #4: la referencia fiable es el sha256
                        # del contenido original registrado en el diario
                        # ANTES del rollback; la recuperación verificable
                        # compara ese estado contra el disco — la mera
                        # existencia o la validez sintáctica del JSON no
                        # bastan (un estado parcial de la transacción fallida
                        # puede ser JSON válido).
                        #   "absent" -> creado durante la transacción: el
                        #               rollback debía borrarlo; debe NO existir
                        #   "sha256" -> existía antes: su contenido debe
                        #               coincidir byte a byte con el original
                        "checks": [
                            {"path": p,
                             "expect": "sha256" if original is not None else "absent",
                             "sha256": (hashlib.sha256(original).hexdigest()
                                        if original is not None else None)}
                            for p, original in journal.items()
                        ],
                    }
                    self.rollback_failures.append(record)
                    self._degraded = record
                    log.error(
                        "Rollback JSON INCOMPLETO (user=%s): %d archivo(s) no "
                        "restaurado(s); estado potencialmente inconsistente. "
                        "Error original: %r", user_id, len(failures), exc,
                    )
                    raise TransactionRollbackError(exc, failures, restored) from exc
                # Rollback completo: se propaga el error original sin alterar
                raise
            else:
                self._local.journal = None

    def mutate_progress(self, user_id: str, fn: Callable[[dict[str, Any]], Any]) -> Any:
        with _STORE_LOCK:
            # misma guarda centralizada que el resto de escrituras públicas
            self._ensure_not_degraded()
            prog = self.load_progress(user_id)
            out = fn(prog)
            self.save_progress(user_id, prog)
            return out

    # -- recuperación explícita y verificable del estado degradado ------------

    def _verify_expected_state(self, exact: bool) -> list[dict[str, Any]]:
        """Compara el disco contra el estado esperado registrado al degradar
        (corrección de auditoría PR #4: verificación del estado REAL, no de
        la mera presencia/validez sintáctica).

        exact=True  -> restauración exacta: cada archivo preexistente debe
                       coincidir BYTE A BYTE (sha256) con el contenido
                       original registrado en el diario antes del rollback.
        exact=False -> ruta semántica: solo invariantes estructurales que
                       NINGUNA reparación semántica puede eximir: los
                       archivos que debían eliminarse siguen sin existir,
                       los preexistentes existen y los .json parsean. La
                       validación del contenido reparado la aporta el
                       validador explícito del llamador.

        Asume `_STORE_LOCK` tomado y `self._degraded` distinto de None.
        Fallo CERRADO: una entrada sin referencia fiable (formato antiguo o
        registro incompleto) se reporta como problema — nunca se asume
        recuperado lo que no se puede verificar."""
        problems: list[dict[str, Any]] = []
        for chk in self._degraded.get("checks", []):
            path = Path(chk["path"])
            expect = chk.get("expect")
            if expect == "absent":
                if path.exists():
                    problems.append({
                        "path": chk["path"],
                        "problem": "sigue existiendo; el rollback debía eliminarlo "
                                   "(fue creado por la transacción fallida): "
                                   "revíselo y elimínelo manualmente",
                    })
                continue
            if expect != "sha256" or not chk.get("sha256"):
                problems.append({
                    "path": chk["path"],
                    "problem": "sin referencia fiable (sha256) del contenido original: "
                               "no es posible verificar la recuperación exacta "
                               "(comprobación fallida por diseño)",
                })
                continue
            if not path.exists():
                problems.append({
                    "path": chk["path"],
                    "problem": "no existe; restáurelo byte a byte (p. ej. desde el .rbak)",
                })
                continue
            try:
                actual_bytes = path.read_bytes()
            except OSError as exc:
                problems.append({
                    "path": chk["path"],
                    "problem": f"no se pudo leer: {type(exc).__name__}: {exc}",
                })
                continue
            if exact:
                actual = hashlib.sha256(actual_bytes).hexdigest()
                if actual != chk["sha256"]:
                    problems.append({
                        "path": chk["path"],
                        "problem": "el contenido DIFIERE del estado original esperado: "
                                   f"sha256 real {actual[:16]}… != sha256 esperado "
                                   f"{chk['sha256'][:16]}… (estado parcial de la "
                                   "transacción fallida o contenido modificado; que "
                                   "sea JSON válido NO basta). Restáurelo byte a byte "
                                   "(p. ej. desde el .rbak) o, si reparó los agregados "
                                   "con contenido legítimamente distinto, use la ruta "
                                   "semántica explícita verify_semantic_recovery(validator)",
                    })
            elif path.suffix == ".json":
                try:
                    json.loads(actual_bytes.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    problems.append({
                        "path": chk["path"],
                        "problem": f"no es JSON válido: {type(exc).__name__}: {exc}",
                    })
        return problems

    def verify_recovery(self) -> tuple[bool, list[dict[str, Any]]]:
        """Procedimiento explícito de recuperación EXACTA (corrección de
        auditoría PR #4: verifica el estado esperado REAL de cada archivo
        afectado, no solo su presencia o validez sintáctica).

        Contra el registro de degradación (construido desde el diario
        copy-on-write, con el sha256 del contenido original tomado ANTES del
        rollback) verifica, para CADA archivo tocado por la transacción
        fallida:
          - expect "sha256": debe existir y coincidir BYTE A BYTE con el
            contenido original. Un archivo que conserve el estado parcial de
            la transacción fallida —aunque sea JSON válido— NO pasa.
          - expect "absent": fue creado por la transacción fallida; debe
            realmente NO existir.

        Esta ruta NO acepta reparaciones con contenido legítimamente distinto
        del original (p. ej. agregados reconstruidos con
        tools/repair_progress.py): para ese caso existe la ruta semántica
        explícita `verify_semantic_recovery(validator)`; no se confunden.

        Si TODAS las comprobaciones pasan: limpia el estado degradado (el
        store vuelve a aceptar escrituras) y devuelve (True, []). Si alguna
        falla: el store SIGUE degradado y devuelve (False, problemas). No
        borra los `.rbak` (evidencia/material de recuperación). Se ejecuta
        bajo `_STORE_LOCK`: serializada con toda escritura. Alternativa
        documentada: reconstruir una instancia nueva del store después de
        reparar los archivos.
        """
        with _STORE_LOCK:
            if self._degraded is None:
                return True, []
            problems = self._verify_expected_state(exact=True)
            if problems:
                log.error("verify_recovery: recuperación exacta NO verificada; el "
                          "store SIGUE degradado. Problemas: %s", problems)
                return False, problems
            log.warning("verify_recovery: recuperación EXACTA verificada (estado "
                        "esperado confirmado byte a byte); el store sale del "
                        "estado degradado y reanuda las escrituras")
            self._degraded = None
            return True, []

    def verify_semantic_recovery(
        self, validator: Callable[["JsonProgressStore"], Any],
    ) -> tuple[bool, list[dict[str, Any]]]:
        """Ruta EXPLÍCITA de recuperación SEMÁNTICA (corrección de auditoría
        PR #4), reservada a reparaciones con contenido legítimamente
        distinto del original — p. ej. el operador reconstruyó los agregados
        con tools/repair_progress.py (los intentos append-only son la fuente
        de verdad) en lugar de restaurar byte a byte desde el `.rbak`.

        NO se confunde con la restauración exacta del rollback
        (`verify_recovery()`): aquí no se exige el sha256 del contenido
        pre-transacción, pero la reparación debe quedar ACREDITADA:

          1. Invariantes estructurales que ninguna reparación semántica
             puede eximir: los archivos que el rollback debía eliminar
             siguen sin existir; los preexistentes existen y los .json
             parsean.
          2. `validator(store)` —callable OBLIGATORIO— debe devolver
             exactamente `True` tras validar la semántica del estado
             reparado (p. ej. `LearningService.validate_progress_repair`:
             los agregados actuales coinciden con la reconstrucción desde
             los intentos). Resultado falso, `None` o excepción => el store
             SIGUE degradado. El validador NO debe escribir vía el store
             (seguirá recibiendo `StoreDegradedError`).

        Si todo se acredita: limpia el estado degradado y devuelve (True, []).
        Si no: devuelve (False, problemas) y las escrituras siguen
        rechazadas. Se ejecuta bajo `_STORE_LOCK` (serializada con toda
        escritura y con `verify_recovery`).
        """
        if not callable(validator):
            raise TypeError(
                "verify_semantic_recovery exige un validador callable que "
                "acredite la reparación (p. ej. consistencia de agregados "
                "contra el historial de intentos); sin validador explícito "
                "no hay ruta semántica")
        with _STORE_LOCK:
            if self._degraded is None:
                return True, []
            problems = self._verify_expected_state(exact=False)
            if not problems:
                try:
                    verdict = validator(self)
                except BaseException as exc:
                    verdict = None
                    problems.append({
                        "path": None,
                        "problem": "el validador semántico lanzó una excepción: "
                                   f"{type(exc).__name__}: {exc}",
                    })
                if verdict is not True and not problems:
                    problems.append({
                        "path": None,
                        "problem": "el validador semántico NO acreditó la reparación "
                                   f"(devolvió {verdict!r}); el store sigue degradado",
                    })
            if problems:
                log.error("verify_semantic_recovery: recuperación semántica NO "
                          "acreditada; el store SIGUE degradado. Problemas: %s",
                          problems)
                return False, problems
            log.warning("verify_semantic_recovery: reparación VALIDADA por el "
                        "validador explícito; el store sale del estado degradado "
                        "y reanuda las escrituras")
            self._degraded = None
            return True, []
