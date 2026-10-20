#!/usr/bin/env python3
"""Genera los módulos 2-4 del curso SOC N1 (lecciones en estado draft).

Las fases 2-4 existen en el roadmap con su estructura completa; el contenido
detallado (teoría + ejemplos + ejercicios) se autorará progresivamente, igual
que la Fase 1. Los archivos generados son la fuente de verdad: editarlos
directamente o regenerar desde este script.
"""
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "content" / "courses" / "soc-n1"

MODULES = {
    "02-deteccion": {
        "id": "02-deteccion",
        "course_id": "soc-n1",
        "title": "Detección y análisis de amenazas",
        "phase": 2,
        "subtitle": "El núcleo del trabajo SOC N1",
        "duration": "Meses 3-4",
        "outcome": "Al terminar podrás hacer triage de una alerta real: leer el log, buscar el IoC y determinar si es falso positivo o incidente.",
        "lessons": ["01-lectura-logs", "02-siem-basico", "03-triage-iocs"],
        "exam": None,
        "resources": [
            {"title": "TryHackMe — SOC Level 1 Path", "url": "https://tryhackme.com/path/outline/soclevel1", "type": "lab", "note": "Recurso principal de esta fase"},
            {"title": "Splunk Free Training — Splunk Fundamentals 1", "url": "https://www.splunk.com/en_us/training/courses/splunk-fundamentals-1.html", "type": "curso"},
            {"title": "John Hammond (YouTube) — análisis de malware y logs", "url": "https://www.youtube.com/@_JohnHammond", "type": "video"},
        ],
    },
    "03-herramientas": {
        "id": "03-herramientas",
        "course_id": "soc-n1",
        "title": "Herramientas y laboratorio práctico",
        "phase": 3,
        "subtitle": "Donde más brillará tu background de dev",
        "duration": "Meses 5-7",
        "outcome": "Al terminar tendrás un portfolio: scripts Python para SOC + 5-10 labs resueltos que mostrar en entrevistas.",
        "lessons": ["01-wireshark", "02-python-para-soc", "03-ctfs-blue-team"],
        "exam": None,
        "resources": [
            {"title": "CyberDefenders.org — Labs gratuitos de análisis", "url": "https://cyberdefenders.org/", "type": "lab"},
            {"title": "Blue Team Labs Online (BTLO)", "url": "https://blueteamlabs.online/", "type": "lab"},
            {"title": "The Hacker Playbook (perspectiva ofensiva para entender qué detectar)", "url": None, "type": "libro"},
        ],
    },
    "04-entrevista": {
        "id": "04-entrevista",
        "course_id": "soc-n1",
        "title": "Preparación para la entrevista",
        "phase": 4,
        "subtitle": "Traducir el conocimiento a oportunidades reales",
        "duration": "Meses 8-10",
        "outcome": "Al terminar tendrás CV listo, portfolio con labs y respuestas preparadas para las preguntas más comunes de entrevistas SOC N1.",
        "lessons": ["01-mitre-kill-chain", "02-proceso-ir", "03-cv-portfolio-simulacros"],
        "exam": None,
        "resources": [
            {"title": "attack.mitre.org — referencia gratuita y oficial", "url": "https://attack.mitre.org/", "type": "referencia"},
            {"title": "Gerald Auger (YouTube) — preparación entrevistas SOC", "url": "https://www.youtube.com/@SimplyCyber", "type": "video"},
            {"title": "LinkedIn Jobs — filtrar: SOC Analyst + Junior + tu región", "url": "https://www.linkedin.com/jobs/", "type": "empleo"},
        ],
    },
}

LESSONS = {
    "02-deteccion": [
        {
            "id": "01-lectura-logs",
            "title": "Lectura e interpretación de logs",
            "kind": "practica",
            "estimated_minutes": 150,
            "summary": "Windows Event Logs, Syslog, logs de firewall y proxy. Identificar patrones anómalos: logins fallidos, escalada de privilegios.",
            "objectives": [
                "Interpretar Windows Event Logs (Security/System) y sus Event IDs clave (4624, 4625, 4672, 4688...)",
                "Normalizar mentalmente syslog vs eventos Windows",
                "Detectar patrones anómalos: brute-force, escalada de privilegios, horarios imposibles",
            ],
            "planned": "Event IDs de Windows, XML de eventos, canales (Security/Sysmon), normalización, ejercicios con logs reales de un DC y de un proxy.",
        },
        {
            "id": "02-siem-basico",
            "title": "SIEM: conceptos y uso básico",
            "kind": "herramienta",
            "estimated_minutes": 150,
            "summary": "Qué es un SIEM (Splunk, Microsoft Sentinel, Elastic): ingesta, normalización, correlación. Queries básicas y reglas de detección.",
            "objectives": [
                "Explicar qué es un SIEM y sus componentes (colección, normalización, correlación, alertas)",
                "Escribir queries básicas en SPL (Splunk) y KQL (Sentinel/Elastic)",
                "Entender cómo una regla de correlación genera una alerta",
            ],
            "planned": "Arquitectura SIEM, lab con Splunk Free, SPL esencial (search, stats, eval), KQL, reglas de correlación, ejercicios con datasets de eventos.",
        },
        {
            "id": "03-triage-iocs",
            "title": "Triage de alertas e indicadores (IoC)",
            "kind": "practica",
            "estimated_minutes": 150,
            "summary": "Clasificar alertas (falso positivo vs real), buscar IoCs en VirusTotal, AbuseIPDB y Shodan. Flujo básico de respuesta y documentación.",
            "objectives": [
                "Aplicar un flujo de triage estándar a cualquier alerta",
                "Enriquecer IoCs con threat intel pública (VT, AbuseIPDB, Shodan, URLhaus)",
                "Documentar y escalar un incidente con línea de tiempo",
            ],
            "planned": "Playbooks de triage, laboratorios con 8-10 alertas reales simuladas (phishing reportado, beaconing, brute-force, DLP), decisión FP/TP, tickets y escalado.",
        },
    ],
    "03-herramientas": [
        {
            "id": "01-wireshark",
            "title": "Análisis de tráfico con Wireshark",
            "kind": "herramienta",
            "estimated_minutes": 180,
            "summary": "Capturar y filtrar paquetes. Detectar escaneos de puertos, exfiltración de datos y credenciales en texto plano. PCAPs de CTFs.",
            "objectives": [
                "Dominar filtros de captura y de visualización de Wireshark",
                "Seguir streams TCP/UDP/HTTP y extraer artefactos",
                "Resolver PCAPs de investigación (CTF/Blue Team Labs)",
            ],
            "planned": "UI de Wireshark, filtros (display vs capture), follow stream, estadísticas, ejercicios con pcaps públicos de malware/CTF, detección de credenciales en claro.",
        },
        {
            "id": "02-python-para-soc",
            "title": "Scripting para SOC con Python",
            "kind": "practica",
            "estimated_minutes": 240,
            "summary": "Scripts para parsear logs, consumir APIs de threat intel (VirusTotal) y automatizar búsqueda de IoCs. Tu mayor diferenciador frente a otros candidatos N1.",
            "objectives": [
                "Parsear logs (syslog, CSV de proxy, JSON de EDR) con Python",
                "Consumir la API de VirusTotal/AbuseIPDB para enriquecer IoCs en lote",
                "Construir un mini-herramienta de triage automatizado para tu portfolio",
            ],
            "planned": "regex en Python, pathlib, json/csv, requests + rate limits, proyecto guiado: 'ioc-checker' que recibe una lista de IPs/hashes y devuelve un reporte. Ejercicios de código evaluables.",
        },
        {
            "id": "03-ctfs-blue-team",
            "title": "CTFs y labs de Blue Team",
            "kind": "practica",
            "estimated_minutes": 240,
            "summary": "Blue Team Labs Online, CyberDefenders, Hack The Box (defensivo). Casos reales de investigación forense y detección de intrusiones.",
            "objectives": [
                "Resolver labs guiados de forensic/detection de principio a fin",
                "Documentar cada lab como write-up de portfolio",
                "Consolidar 5-10 labs resueltos para entrevistas",
            ],
            "planned": "Metodología de investigación, walkthrough de labs gratuitos (BTLO/CyberDefenders), plantilla de write-up profesional, checklist de portfolio.",
        },
    ],
    "04-entrevista": [
        {
            "id": "01-mitre-kill-chain",
            "title": "Frameworks: MITRE ATT&CK y Cyber Kill Chain",
            "kind": "teoria",
            "estimated_minutes": 120,
            "summary": "Hablar el lenguaje del SOC. Asociar técnicas a IDs de MITRE (ej. T1059 Command Scripting) es exactamente lo que los entrevistadores buscan.",
            "objectives": [
                "Navegar la matriz ATT&CK (tácticas, técnicas, sub-técnicas)",
                "Mapear escenarios de logs a IDs de MITRE",
                "Explicar la Kill Chain y su relación con las defensas",
            ],
            "planned": "Matriz Enterprise, ejercicios de mapeo log→técnica, detecciones por técnica (Data Sources), narrativa de entrevista con ATT&CK.",
        },
        {
            "id": "02-proceso-ir",
            "title": "Proceso de respuesta a incidentes (IR)",
            "kind": "teoria",
            "estimated_minutes": 120,
            "summary": "Las 6 fases del IR: Preparación, Identificación, Contención, Erradicación, Recuperación y Lecciones aprendidas. Construir un runbook de ejemplo.",
            "objectives": [
                "Describir las 6 fases de IR (NIST SP 800-61) con ejemplos",
                "Entender roles, severidades y comunicación en un incidente",
                "Escribir un runbook básico para un escenario (phishing/brute-force)",
            ],
            "planned": "NIST vs SANS, severidad/priorización, chain of custody, runbooks, ejercicios de simulación de mesa (tabletop) con escenarios escritos.",
        },
        {
            "id": "03-cv-portfolio-simulacros",
            "title": "CV, portfolio y simulacros de entrevista",
            "kind": "practica",
            "estimated_minutes": 180,
            "summary": "Destacar scripting, labs resueltos y proyectos Python orientados a seguridad. Preparar respuestas para preguntas típicas SOC N1.",
            "objectives": [
                "Construir un CV orientado a SOC N1 sin experiencia formal previa",
                "Publicar tu portfolio (GitHub + write-ups + scripts)",
                "Responder con soltura las 20 preguntas típicas de entrevista N1",
            ],
            "planned": "Plantillas de CV, banco de 20 preguntas con respuestas modelo evaluables (free_text + auto-revisión), simulacro cronometrado final, guión de storytelling de labs.",
        },
    ],
}

DRAFT_BODY = """\
Esta lección forma parte del roadmap del curso y su contenido detallado
está **en construcción** (igual que la Fase 1: teoría + ejemplos + ejercicios
+ feedback).

**Contenido planeado:** {planned}

Mientras tanto, puedes adelantar con los recursos recomendados del módulo.
Cuando esté publicada, esta tarjeta se habilitará automáticamente: la
plataforma detecta el cambio de `status: draft` a `status: published` sin
tocar código."""


def main() -> None:
    for mod_id, mod in MODULES.items():
        mod_dir = ROOT / "modules" / mod_id
        (mod_dir / "lessons").mkdir(parents=True, exist_ok=True)
        with open(mod_dir / "module.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(mod, f, allow_unicode=True, sort_keys=False, width=100)
        for lmeta in LESSONS[mod_id]:
            lesson = {
                "id": lmeta["id"],
                "module_id": mod_id,
                "course_id": "soc-n1",
                "title": lmeta["title"],
                "kind": lmeta["kind"],
                "estimated_minutes": lmeta["estimated_minutes"],
                "status": "draft",
                "summary": lmeta["summary"],
                "objectives": lmeta["objectives"],
                "theory": [
                    {
                        "id": "t1",
                        "section": "Contenido en construcción",
                        "format": "markdown",
                        "body": DRAFT_BODY.format(planned=lmeta["planned"]),
                    }
                ],
                "examples": [],
                "exercises": [],
            }
            with open(mod_dir / "lessons" / f"{lmeta['id']}.yaml", "w", encoding="utf-8") as f:
                yaml.safe_dump(lesson, f, allow_unicode=True, sort_keys=False, width=100)
        print(f"✔ módulo {mod_id}: module.yaml + {len(LESSONS[mod_id])} lecciones draft")


if __name__ == "__main__":
    main()
