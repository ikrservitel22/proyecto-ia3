import subprocess

import os

import sqlite3

import requests

import wave

import numpy as np

import json

import queue

import threading

import uuid

import time

from datetime import datetime, timedelta
import re as _re

from typing import Optional

from collections import OrderedDict
import concurrent.futures

from flask import Flask, render_template, Response, jsonify, request as flask_request

from faster_whisper import WhisperModel

from jira_tools import obtener_board, crear_tarea



TOOLS = {

    "obtener_board": {

        "descripcion": "Obtiene información de un board Jira usando el ID",

        "funcion": obtener_board

    }

}









# =========================
# UTILIDADES DE FECHA
# =========================

_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    # abreviaturas
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}

def parsear_fecha_es(texto_fecha: str) -> str:
    """
    Convierte expresiones de fecha en español a ISO 8601 (YYYY-MM-DD).
    Soporta: '20 de junio', '20/06', '20/06/2027', '2026-06-20',
             'del 20 de junio al 31 de junio' (toma la PRIMERA fecha),
             'maximo 31 de junio' / 'hasta 31 de junio' (para fecha fin).
    Devuelve None si no puede parsear.
    """
    if not texto_fecha or str(texto_fecha).strip().lower() in ("null", "none", ""):
        return None

    s = str(texto_fecha).strip().lower()
    hoy = datetime.now().date()

    def _resolver(dia, nombre_mes, anio_hint=None):
        num_mes = _MESES_ES.get(nombre_mes)
        if not num_mes:
            return None
        anio = anio_hint or hoy.year
        try:
            d = datetime(anio, num_mes, int(dia)).date()
            if not anio_hint and d < hoy:
                d = datetime(anio + 1, num_mes, int(dia)).date()
            return d.isoformat()
        except ValueError:
            return None

    # Ya es ISO YYYY-MM-DD
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass

    # dd/mm/yyyy o dd-mm-yyyy
    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass

    # dd/mm sin año
    m = _re.match(r"^(\d{1,2})[/\-](\d{1,2})$", s)
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        try:
            d = datetime(hoy.year, mes, dia).date()
            if d < hoy:
                d = datetime(hoy.year + 1, mes, dia).date()
            return d.isoformat()
        except ValueError:
            pass

    # Año explícito: "20 de junio de 2027" / "20 junio 2027"
    m = _re.search(r"(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+)\s+(?:de\s+)?(\d{4})", s)
    if m:
        r = _resolver(m.group(1), m.group(2), int(m.group(3)))
        if r:
            return r

    # Rango "del X de MES al Y de MES" — devuelve la PRIMERA fecha del rango
    m = _re.search(r"del\s+(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+)", s)
    if m:
        r = _resolver(m.group(1), m.group(2))
        if r:
            return r

    # "hasta/máximo/maximo X de MES" — devuelve esa fecha
    m = _re.search(r"(?:hasta|m[aá]ximo|max|antes del?)\s+(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+)", s)
    if m:
        r = _resolver(m.group(1), m.group(2))
        if r:
            return r

    # "X de MES" / "X MES" genérico
    m = _re.search(r"(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+)", s)
    if m:
        r = _resolver(m.group(1), m.group(2))
        if r:
            return r

    # "MES X" (ej: "junio 20")
    m = _re.search(r"([a-záéíóú]+)\s+(\d{1,2})", s)
    if m and m.group(1) in _MESES_ES:
        r = _resolver(m.group(2), m.group(1))
        if r:
            return r

    # ── Expresiones relativas ──────────────────────────────────────────
    # "el 12 de este mes" / "el 12 de este año" / "día 12"
    m = _re.search(r"(?:el\s+|d[ií]a\s+)?(\d{1,2})\s+(?:de\s+)?(?:este\s+mes|el\s+mes)", s)
    if m:
        try:
            d = datetime(hoy.year, hoy.month, int(m.group(1))).date()
            if d < hoy:
                # si ya pasó en este mes, asume próximo mes
                mes_siguiente = hoy.month + 1 if hoy.month < 12 else 1
                anio_sig = hoy.year if hoy.month < 12 else hoy.year + 1
                d = datetime(anio_sig, mes_siguiente, int(m.group(1))).date()
            return d.isoformat()
        except ValueError:
            pass

    # "hoy"
    if _re.search(r"\bhoy\b", s):
        return hoy.isoformat()

    # "mañana" / "manana"
    if _re.search(r"\bma[nñ]ana\b", s):
        return (hoy + timedelta(days=1)).isoformat()

    # "pasado mañana"
    if _re.search(r"pasado\s+ma[nñ]ana", s):
        return (hoy + timedelta(days=2)).isoformat()

    # "esta semana" → viernes de esta semana
    if _re.search(r"esta\s+semana", s):
        dias_hasta_viernes = (4 - hoy.weekday()) % 7 or 7
        return (hoy + timedelta(days=dias_hasta_viernes)).isoformat()

    # "próxima semana" / "proxima semana" → lunes siguiente
    if _re.search(r"pr[oó]xima\s+semana|la\s+semana\s+que\s+viene", s):
        dias_hasta_lunes = (7 - hoy.weekday()) % 7 or 7
        return (hoy + timedelta(days=dias_hasta_lunes)).isoformat()

    # "el lunes/martes/..." → próximo día de la semana
    _DIAS_SEMANA = {
        "lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2,
        "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6
    }
    for dia_nombre, dia_num in _DIAS_SEMANA.items():
        if _re.search(rf"\b{dia_nombre}\b", s):
            dias = (dia_num - hoy.weekday()) % 7 or 7
            return (hoy + timedelta(days=dias)).isoformat()

    # "fin de mes" → último día del mes actual
    if _re.search(r"fin\s+de\s+(?:este\s+)?mes", s):
        if hoy.month == 12:
            return datetime(hoy.year + 1, 1, 1).date().isoformat()
        return datetime(hoy.year, hoy.month + 1, 1).date().isoformat()

    return None


def _extraer_fecha_fin_rango(s: str):
    """
    De un string tipo 'Del 20 de junio al 31 de junio' extrae la SEGUNDA fecha (la de fin).
    """
    if not s:
        return None
    s = s.lower()
    # "al X de MES" / "hasta X de MES"
    m = _re.search(r"(?:al|hasta)\s+(\d{1,2})\s+(?:de\s+)?([a-záéíóú]+)", s)
    if m:
        num_mes = _MESES_ES.get(m.group(2))
        if num_mes:
            hoy = datetime.now().date()
            try:
                d = datetime(hoy.year, num_mes, int(m.group(1))).date()
                if d < hoy:
                    d = datetime(hoy.year + 1, num_mes, int(m.group(1))).date()
                return d.isoformat()
            except ValueError:
                pass
    return None


def _fechas_con_defaults(fecha_inicio_raw, fecha_fin_raw):
    """
    Resuelve fecha_inicio y fecha_fin:
      - inicio  → hoy si no se especifica
      - fin     → inicio + 20 días si no se especifica
    Maneja rangos como 'Del 20 de junio al 31 de junio':
      - inicio toma la primera fecha del rango
      - fin toma la segunda fecha del rango
    """
    hoy = datetime.now().date()

    # Detectar rango en fecha_inicio_raw (ej: "Del 20 de junio al 31 de junio")
    fin_desde_rango = None
    if fecha_inicio_raw and _re.search(r"\bal\b|\bhasta\b", str(fecha_inicio_raw).lower()):
        fin_desde_rango = _extraer_fecha_fin_rango(str(fecha_inicio_raw))

    inicio = parsear_fecha_es(fecha_inicio_raw)
    if not inicio:
        inicio = hoy.isoformat()

    # Para la fecha fin: primero intentar fin_raw, luego rango detectado en inicio_raw
    fin = parsear_fecha_es(fecha_fin_raw)
    if not fin:
        fin = fin_desde_rango
    if not fin:
        fin = (datetime.strptime(inicio, "%Y-%m-%d").date() + timedelta(days=20)).isoformat()

    return inicio, fin


def _formato_resumen(proyecto: str, tarea: str, subtarea: str = "") -> str:
    """
    Construye el resumen: PROYECTO - tarea  (o PROYECTO - tarea - subtarea)
    Evita prefijos duplicados: si tarea ya empieza con "PROYECTO - " lo elimina.
    """
    proyecto = (proyecto or "GENERAL").upper().strip()
    tarea    = (tarea or "").strip()
    subtarea = (subtarea or "").strip()

    # Quitar prefijo duplicado exacto: "PROYECTO - tarea" → "tarea"
    prefijo = proyecto + " - "
    if tarea.upper().startswith(prefijo):
        tarea = tarea[len(prefijo):].strip()

    if not tarea:
        tarea = "tarea sin nombre"

    if subtarea:
        return f"{proyecto} - {tarea} - {subtarea}"
    return f"{proyecto} - {tarea}"


def build_task_struct(task, proyecto="GENERAL"):

    inicio, fin = _fechas_con_defaults(
        task.get("fecha_inicio"),
        task.get("fecha_vencimiento") or task.get("plazo")
    )

    nombre_tarea = task.get("texto") or task.get("task") or ""
    subtarea     = task.get("subtarea") or ""
    responsable  = task.get("responsable") or task.get("persona_asignada") or ""

    return {
        "resumen":        _formato_resumen(proyecto, nombre_tarea, subtarea),
        "descripcion":    task.get("descripcion") or task.get("contexto") or "",
        "persona_asignada": responsable,
        "principal":      proyecto,
        "prioridad":      "media",
        "fecha_inicio":   inicio,
        "fecha_vencimiento": fin,
        "sprint":         "actual"
    }





# =========================

# CONFIG

# =========================



UMBRAL_SILENCIO = 300          # RMS mínimo para considerar que hay voz

# VAD — controla los chunks que van a Whisper
#   MIN: mínimo antes de poder cortar por silencio (evita fragmentos sin contexto)
#   PAUSA: pausa natural entre frases que dispara el corte
#   MAX: límite duro para no acumular demasiado audio sin transcribir
VAD_CHUNK_MIN       = 10.0   # seg mínimos — menos chunks = menos carga CPU
VAD_SILENCIO_CORTO  = 2.0    # seg de silencio → cortar
VAD_CHUNK_MAX       = 25.0   # seg máximos sin corte
SILENCIO_DURACION   = VAD_SILENCIO_CORTO  # alias para grabar_hasta_silencio

# Fin de llamada
SILENCIO_FIN_LLAMADA = 60.0  # seg de silencio total → fin de llamada

CHUNK = 4096                  # lectura ~256ms por iteración — menos CPU en bucle VAD
RATE  = 16000
MIN_DURACION = 1.5

# Streaming / low-latency settings
STREAMING_MODE = True
STREAM_CHUNK_SECONDS = 20     # ya no se usa en el nuevo loop, se mantiene por compatibilidad

MAX_SUMMARY_CHARS = 1500   # ~375 tokens — deja espacio para system prompt + respuesta



BASE_DIR = os.path.dirname(__file__)

DB_FILE = os.path.join(BASE_DIR, "database", "database.db")



MONITOR = (

    "alsa_output.usb-GN_Netcom_A_S_Jabra_BIZ_2300_00028079E35A07-00.mono-fallback.monitor"

)



MICROFONO = (

    "alsa_input.usb-GN_Netcom_A_S_Jabra_BIZ_2300_00028079E35A07-00.mono-fallback"

)



MAX_RESULTADOS_WEB = 3



# Palabras clave que indican consulta a datos internos sensibles

INTENT_KEYWORDS = {

    "usuarios": [

        "usuario", "usuaria", "cliente", "clientes",

        "cedula", "cédula", "cuenta", "plan del cliente",

        "datos del cliente", "información del cliente",

    ],

    "presupuesto": [

        "presupuesto", "budget", "monto", "gasto", "gastos",

        "costo", "costos", "inversión", "inversion",

        "disponible", "ejecutado",

    ],

    "contratos": [

        "contrato", "contratos", "vigencia", "vencimiento",

        "renovacion", "renovación", "fecha de contrato",

    ],

    "empleados": [

        "empleado", "empleados", "agente", "agentes",

        "extension", "extensión", "departamento", "cargo",

        "colaborador", "funcionario",

    ],

}



LABELS_INTENT = {

    "usuarios":    "datos de usuarios / clientes",

    "presupuesto": "información de presupuesto",

    "contratos":   "datos de contratos",

    "empleados":   "información de empleados",

    "web":         "búsqueda en internet",

}



SYSTEM_PROMPT = """Eres un copiloto IA de soporte. Analiza la conversación y responde breve y directo.
Extrae: tareas (responsable, plazo, estado). Prioriza claridad sobre extensión."""



# =========================

# FLASK

# =========================



app = Flask(__name__)



# Desactivar logging de Werkzeug para /stream (SSE)

import logging

werkzeug_log = logging.getLogger('werkzeug')

class StreamFilter(logging.Filter):

    def filter(self, record):

        return '/stream' not in record.getMessage()

werkzeug_log.addFilter(StreamFilter())



# =========================

# WHISPER

# =========================



print("Cargando Whisper...")

model = WhisperModel("medium", device="cpu", compute_type="int8")

print("Whisper listo.")



# =========================

# CALENTAR OLLAMA

# =========================



print("Calentando Ollama...")

try:

    resp = requests.post(

        "http://localhost:11434/api/chat",

        json={

            "model": "qwen2.5:7b-instruct",

            "messages": [{"role": "user", "content": "hola"}],

            "stream": False

        },

        timeout=60

    )

    resp.raise_for_status()

    print("Ollama listo.")

except requests.Timeout:

    print("WARN: Timeout calentando Ollama (Ollama tardó >60s)")

except Exception as e:

    print(f"WARN: Problema calentando Ollama: {type(e).__name__}: {e}")



# =========================

# SSE SUBSCRIBERS

# =========================



subscribers = []

subscribers_lock = threading.Lock()





def publish(message: dict):

    with subscribers_lock:

        for q in list(subscribers):

            try:

                q.put(message, block=False)

            except queue.Full:

                print(f"[PUBLISH] Advertencia: cola de cliente llena, descartando mensaje", flush=True)

            except Exception as e:

                print(f"[PUBLISH] Error enviando a cliente: {e}", flush=True)



# =========================

# GLOBAL STATE

# =========================



listening = False

proceso_ffmpeg: Optional[subprocess.Popen] = None



# Transcripción acumulada para extracción final
accumulated_transcript = ""
accum_lock = threading.Lock()

transcription_threads: list[threading.Thread] = []
transcription_threads_lock = threading.Lock()

# Semáforo: máximo 2 transcripciones Whisper en paralelo
_transcribe_semaphore = threading.Semaphore(1)  # 1 sola transcripción a la vez — CPU limitada
# Dedup: evita publicar el mismo fragmento dos veces (ffmpeg mezcla monitor+mic)
_dedup_cache: dict = {}
_dedup_lock  = threading.Lock()
# Ollama tiene 1 slot: serializar todas las llamadas al LLM
_ollama_semaphore = threading.Semaphore(1)
# Jira: máximo 2 creaciones simultáneas (evita saturar la API)
_jira_semaphore = threading.Semaphore(2)

# Temporizador para envío periódico a la IA (cada 60s de llamada continua)
_ultimo_envio_ia = 0.0
_envio_ia_lock   = threading.Lock()
IA_ENVIO_INTERVALO = 99999.0  # Desactivado — solo procesar al fin de llamada



# Contexto de la última llamada para preguntas posteriores

last_call_summary = ""

call_context_lock = threading.Lock()



# Solicitudes de autorización pendientes {req_id: {"event": Event, "approved": bool}}

pending_auths: dict = {}

pending_auths_lock = threading.Lock()



# Caché de respuestas recientes (texto → resultado)
_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
CACHE_MAX = 30

# Historial de conversación manual (mensajes del usuario + respuestas IA)
# Se limpia al iniciar/detener escucha. Máx 10 turnos para no saturar el prompt.
_chat_history: list = []   # [{"role": "user"|"assistant", "content": "..."}]
_chat_history_lock = threading.Lock()
CHAT_HISTORY_MAX = 10      # turnos (user+assistant = 1 turno)



def init_db():

    conn = sqlite3.connect(DB_FILE)



    conn.execute("""

    CREATE TABLE IF NOT EXISTS tareas (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        texto TEXT NOT NULL,

        contexto TEXT,

        responsable TEXT,

        plazo TEXT,

        prioridad TEXT,

        estado TEXT,

        creado_en TEXT

    )

    """)



    # agregar jira_key si no existe

    try:

        conn.execute("""

            ALTER TABLE tareas

            ADD COLUMN jira_key TEXT

        """)

        print("[DB] Columna jira_key creada")

    except sqlite3.OperationalError:

        pass



    conn.commit()

    conn.close()



def _cache_get(texto):

    with _cache_lock:

        return _cache.get(texto.lower().strip())



def _cache_set(texto, valor):

    key = texto.lower().strip()

    with _cache_lock:

        _cache[key] = valor

        _cache.move_to_end(key)

        while len(_cache) > CACHE_MAX:

            _cache.popitem(last=False)





def get_db_conn():

    conn = sqlite3.connect(DB_FILE)

    conn.row_factory = sqlite3.Row

    return conn





def fetch_tasks():

    conn = get_db_conn()

    cur = conn.cursor()

    cur.execute(

        "SELECT id, texto, contexto, responsable, plazo, prioridad, estado, creado_en FROM tareas ORDER BY id DESC"

    )

    rows = cur.fetchall()

    conn.close()

    return [dict(row) for row in rows]





def create_task(task, estado="pendiente", proyecto="GENERAL"):

    data = build_task_struct(task, proyecto)



    conn = get_db_conn()

    cur = conn.cursor()



    # evitar duplicados

    cur.execute("""

        SELECT id FROM tareas

        WHERE texto = ? AND responsable = ? AND prioridad = ?

    """, (

        data["resumen"],

        data["persona_asignada"],

        data["prioridad"]

    ))



    if cur.fetchone():

        conn.close()

        return None



    creado_en = datetime.utcnow().isoformat()



    cur.execute("""

        INSERT INTO tareas (

            texto, contexto, responsable, plazo, prioridad, estado, creado_en

        ) VALUES (?, ?, ?, ?, ?, ?, ?)

    """, (

        data["resumen"],

        data["descripcion"],

        data["persona_asignada"],

        data["fecha_vencimiento"],

        data["prioridad"],

        estado,

        creado_en

    ))



    conn.commit()

    row_id = cur.lastrowid

    conn.close()



    return {**data, "id": row_id, "estado": estado}





def _inferir_nombre_proyecto(epic_summary: str) -> str:
    """
    Extrae el nombre del proyecto del summary de una épica.
    Ejemplos:
      'ServiAyuda - Módulo Cotizador'  → 'SERVIAYUDA'
      'Shopify Descuentos al Piso'     → 'SHOPIFY'
      'Desarrollo módulo login'        → 'DESARROLLO'
    Toma la primera palabra significativa (>3 chars) en mayúsculas.
    """
    if not epic_summary:
        return ""
    # Si tiene " - " tomar lo que hay antes del guión
    if " - " in epic_summary:
        parte = epic_summary.split(" - ")[0].strip()
    else:
        parte = epic_summary.strip()
    # Tomar primera palabra de más de 3 letras
    for palabra in parte.split():
        if len(palabra) > 3:
            return palabra.upper()
    return parte.split()[0].upper() if parte else ""


def _sync_tarea_a_jira(task_final: dict, task_id: int):
    """Crea la tarea en Jira con semáforo propio — máx 2 creaciones concurrentes."""
    if not _jira_semaphore.acquire(timeout=120):
        print(f"[JIRA] Timeout esperando turno para tarea {task_id}", flush=True)
        publish({"type": "jira_error", "task_id": task_id, "error": "Timeout en cola Jira"})
        return
    try:
        proyecto_raw = (task_final.get("proyecto") or "").strip()
        summary_raw  = task_final["texto"]

        # Si el proyecto es GENERAL (la IA no lo detectó), inferirlo desde la épica
        # y reemplazar el prefijo GENERAL en el summary
        if not proyecto_raw or proyecto_raw.upper() == "GENERAL":
            from jira_tools import buscar_epica, _norm
            texto_busqueda = summary_raw + " " + (task_final.get("contexto") or "")
            epic_res = buscar_epica(texto_busqueda)
            if epic_res["ok"]:
                # Extraer el nombre del proyecto de la épica (primera palabra o palabras clave)
                epic_summary = epic_res["summary"]
                # Usar el summary de la épica como nombre del proyecto
                # Normalizar: quitar prefijos tipo "SERVITEL -", tomar las primeras 2-3 palabras
                nombre_proyecto = _inferir_nombre_proyecto(epic_summary)
                if nombre_proyecto and nombre_proyecto.upper() != "GENERAL":
                    proyecto_raw = nombre_proyecto
                    # Reemplazar "GENERAL - " por el nombre real en el summary
                    if summary_raw.upper().startswith("GENERAL - "):
                        summary_raw = nombre_proyecto.upper() + " - " + summary_raw[len("GENERAL - "):]
                    print(f"[JIRA] Proyecto inferido desde épica: {proyecto_raw!r}", flush=True)

        jira_res = crear_tarea(
            summary=summary_raw,
            description=task_final["contexto"],
            duedate=task_final.get("plazo") or datetime.utcnow().date().isoformat(),
            startdate=task_final.get("fecha_inicio") or datetime.utcnow().date().isoformat(),
            responsable=(task_final.get("responsable") or "").strip(),
            proyecto=proyecto_raw
        )
        if jira_res["ok"]:
            jira_key = jira_res["data"].get("key")
            print(f"[JIRA OK] {jira_key} → tarea {task_id}", flush=True)
            conn = get_db_conn()
            # Si el summary cambió (proyecto inferido), actualizar también en DB
            if summary_raw != task_final["texto"]:
                conn.execute("UPDATE tareas SET texto = ?, jira_key = ? WHERE id = ?",
                             (summary_raw, jira_key, task_id))
            else:
                conn.execute("UPDATE tareas SET jira_key = ? WHERE id = ?",
                             (jira_key, task_id))
            conn.commit()
            conn.close()
            publish({"type": "jira_synced", "task_id": task_id, "jira_key": jira_key,
                     "texto": summary_raw})
        else:
            print(f"[JIRA ERROR] tarea {task_id}: {jira_res.get('error')}", flush=True)
            publish({"type": "jira_error", "task_id": task_id, "error": jira_res.get("error", "")})
    except Exception as e:
        print(f"[JIRA EXCEPTION] tarea {task_id}: {e}", flush=True)
        publish({"type": "jira_error", "task_id": task_id, "error": str(e)[:100]})
    finally:
        _jira_semaphore.release()


def create_tasks_batch(tasks):
    """
    Guarda en DB de forma síncrona (rápido) y lanza Jira en background.
    Responde al frontend inmediatamente sin esperar a Jira.
    """
    if not isinstance(tasks, dict):
        return []

    created_rows = []

    for key in ["pendientes", "completadas"]:
        estado = "pendiente" if key == "pendientes" else "completada"

        for task in tasks.get(key, []):
            task_final = aplicar_reglas(task)

            # 1. GUARDAR EN DB — síncrono y rápido
            created = create_task(task_final, estado=estado)

            if created:
                created_rows.append((task_final, created["id"]))
                print(f"[DB] Tarea guardada id={created['id']}: {task_final['texto']!r}", flush=True)

    # 2. CREAR EN JIRA — en paralelo, en background, sin bloquear
    for task_final, task_id in created_rows:
        threading.Thread(
            target=_sync_tarea_a_jira,
            args=(task_final, task_id),
            daemon=True
        ).start()

    # Devolver las tareas recién guardadas inmediatamente
    if not created_rows:
        return fetch_tasks()

    ids = [row_id for _, row_id in created_rows]
    conn = get_db_conn()
    cur  = conn.cursor()
    placeholders = ",".join("?" * len(ids))
    cur.execute(
        f"SELECT id, texto, contexto, responsable, plazo, prioridad, estado, creado_en FROM tareas WHERE id IN ({placeholders}) ORDER BY id DESC",
        ids
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows



def aplicar_reglas(task):

    proyecto     = str(task.get("proyecto") or "GENERAL").upper().strip()
    nombre_tarea = (task.get("tarea") or task.get("texto") or "").strip()
    subtarea     = (task.get("subtarea") or "").strip()
    descripcion  = (task.get("contexto") or task.get("descripcion") or "").strip()

    inicio, fin = _fechas_con_defaults(
        task.get("fecha_inicio"),
        task.get("fecha_fin") or task.get("plazo") or task.get("fecha_vencimiento")
    )

    return {
        "texto":        _formato_resumen(proyecto, nombre_tarea, subtarea),
        "contexto":     descripcion,
        "responsable":  (task.get("responsable") or "").strip(),
        "prioridad":    "media",
        "fecha_inicio": inicio,
        "plazo":        fin,
        "proyecto":     proyecto,
        "estado":       "pendiente"
    }



def update_task_db(task_id, data):

    fields = []

    values = []

    for field in ["texto", "contexto", "responsable", "plazo", "prioridad", "estado"]:

        if field in data:

            fields.append(f"{field} = ?")

            values.append(str(data[field]).strip())

    if not fields:

        return None

    values.append(task_id)

    conn = get_db_conn()

    cur = conn.cursor()

    cur.execute(f"UPDATE tareas SET {', '.join(fields)} WHERE id = ?", values)

    conn.commit()

    cur.execute("SELECT id, texto, contexto, responsable, plazo, prioridad, estado, creado_en FROM tareas WHERE id = ?", (task_id,))

    row = cur.fetchone()

    conn.close()

    return dict(row) if row else None



def delete_task_db(task_id):

    conn = get_db_conn()

    cur = conn.cursor()

    cur.execute("DELETE FROM tareas WHERE id = ?", (task_id,))

    conn.commit()

    affected = cur.rowcount

    conn.close()

    return affected > 0



# URLs que respondieron con error: se omiten en el resto de la sesión

_blocked_urls: set = set()



# =========================

# OLLAMA

# =========================



def _stream_ollama(messages, on_token):

    """Llama a Ollama en modo streaming e invoca on_token por cada fragmento."""

    try:

        resp = requests.post(

            "http://localhost:11434/api/chat",

            json={"model": "qwen2.5:7b-instruct", "messages": messages, "stream": True},

            stream=True,

            timeout=300

        )

        resp.raise_for_status()

        

        for line in resp.iter_lines(decode_unicode=True, chunk_size=1):

            if not line:

                continue

            try:

                chunk = json.loads(line)

                token = chunk.get("message", {}).get("content", "")

                if token:

                    on_token(token)

                if chunk.get("done"):

                    break

            except json.JSONDecodeError:

                continue

    except requests.Timeout:

        on_token(f"\n[Error: Timeout de Ollama]")

    except requests.ConnectionError as ce:

        on_token(f"\n[Error: No se puede conectar a Ollama: {ce}]")

    except Exception as e:

        on_token(f"\n[Error Ollama: {type(e).__name__}: {str(e)[:100]}]")



def _call_ollama(messages, timeout=900):
    tokens = []
    def on_token(token):
        tokens.append(token)
    # Serializar: Ollama tiene 1 slot, cola maxima 10 min
    acquired = _ollama_semaphore.acquire(timeout=600)
    if not acquired:
        print("[OLLAMA] Timeout esperando semaforo", flush=True)
        return ""
    try:
        _stream_ollama(messages, on_token)
    finally:
        _ollama_semaphore.release()
    return "".join(tokens).strip()



def _call_ollama_sync(messages, timeout=600):
    # Usar lista mutable para que el finally vea cambios hechos en except
    _held = [False]
    if not _ollama_semaphore.acquire(timeout=600):
        print("[OLLAMA] sync timeout semaforo", flush=True)
        return "[Error: Ollama ocupado]"
    _held[0] = True
    try:
        resp = requests.post(
            "http://localhost:11434/api/chat",
            json={"model": "qwen2.5:7b-instruct", "messages": messages, "stream": False},
            timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            if isinstance(data.get("message"), dict):
                return str(data["message"].get("content", "")).strip()
            choice = data.get("choices")
            if isinstance(choice, list) and choice:
                return str(choice[0].get("message", {}).get("content", "")).strip()
        return json.dumps(data)
    except requests.Timeout:
        # Liberar semáforo ANTES del fallback para que _call_ollama pueda adquirirlo
        _ollama_semaphore.release()
        _held[0] = False
        print("[OLLAMA] Timeout sync, fallback streaming...", flush=True)
        try:
            fallback = _call_ollama(messages)
            return fallback if fallback else "[Error Ollama: Timeout]"
        except Exception as e:
            return f"[Error Ollama sync: fallback falló: {type(e).__name__}: {str(e)[:80]}]"
    except Exception as e:
        return f"[Error Ollama sync: {type(e).__name__}: {str(e)[:120]}]"
    finally:
        if _held[0]:
            _ollama_semaphore.release()


def detectar_tool(texto):

    herramientas = ""

    for nombre, info in TOOLS.items():

        herramientas += f"""

Tool: {nombre}

Descripción: {info['descripcion']}

"""

    prompt = f"""

Tienes acceso a estas herramientas:



{herramientas}



Si necesitas una herramienta responde SOLO en JSON:



{{

    "tool":"obtener_board",

    "arguments": {{

        "board_id":35

    }}

}}



Si no necesitas ninguna herramienta responde:



NO_TOOL



Pregunta:

{texto}

"""

    return _call_ollama_sync([

        {"role": "system", "content": "Eres un selector de herramientas."},

        {"role": "user", "content": prompt}

    ])



def ejecutar_tool(tool_response):

    try:

        tool_data = json.loads(tool_response)

        nombre_tool = tool_data["tool"]

        argumentos = tool_data.get("arguments", {})

        if nombre_tool not in TOOLS:

            return {"ok": False, "error": f"Tool {nombre_tool} no existe"}

        funcion = TOOLS[nombre_tool]["funcion"]

        return funcion(**argumentos)

    except Exception as e:

        return {"ok": False, "error": str(e)}



def _normalize_task(raw_task):

    if not isinstance(raw_task, dict):
        return None

    texto = str(raw_task.get("texto") or "").strip()

    if len(texto.split()) > 18:
        return None

    return {
        "texto":        texto,
        "contexto":     str(raw_task.get("descripcion") or "").strip(),
        "responsable":  str(raw_task.get("responsable") or "").strip(),
        "proyecto":     str(raw_task.get("proyecto")    or "").strip(),
        "subtarea":     str(raw_task.get("subtarea")    or "").strip(),
        # Fechas en texto natural — se parsean en aplicar_reglas/_fechas_con_defaults
        "fecha_inicio": str(raw_task.get("fecha_inicio") or "").strip() or None,
        "fecha_fin":    str(raw_task.get("fecha_fin")    or "").strip() or None,
        "plazo":        str(raw_task.get("plazo")        or "").strip(),
        "prioridad":    "media",
    }




# Verbos que indican tarea específica — no necesitan análisis macro
_VERBOS_ESPECIFICOS = {
    "revisar","enviar","llamar","actualizar","crear","subir","bajar","verificar",
    "confirmar","responder","notificar","informar","llevar","traer","buscar",
    "pedir","solicitar","entregar","firmar","aprobar","rechazar","archivar",
    "agregar","eliminar","borrar","copiar","mover","cambiar","corregir",
    "reportar","documentar","registrar","anotar","escribir","leer"
}


def _desglosar_si_macro(task: dict) -> list:
    """
    Recibe una tarea normalizada. Si es una actividad macro/general,
    la desglosa en subtareas concretas. Si ya es específica, la devuelve tal cual.

    Retorna una lista de tareas (1 si es específica, N si se desglosó).
    Cada tarea en la lista mantiene responsable, proyecto y fechas del original.
    """
    texto = (task.get("texto") or "").strip()
    if not texto:
        return [task]

    # Atajo rápido: si empieza con verbo específico → no llamar a Ollama
    primer_verbo = texto.lower().split()[0] if texto else ""
    if primer_verbo in _VERBOS_ESPECIFICOS:
        print(f"[MACRO] Atajo específica (verbo): {texto!r}", flush=True)
        return [task]

    # Atajo rápido: menos de 4 palabras → demasiado corta para ser macro
    if len(texto.split()) < 4:
        print(f"[MACRO] Atajo específica (corta): {texto!r}", flush=True)
        return [task]

    proyecto    = (task.get("proyecto") or "").strip()
    responsable = (task.get("responsable") or "").strip()
    contexto_extra = ""
    if proyecto:
        contexto_extra += f"\nProyecto: {proyecto}"
    if responsable:
        contexto_extra += f"\nResponsable: {responsable}"

    prompt = f"""INSTRUCCION: Responde SIEMPRE en español. Nunca uses otro idioma.

Eres un experto en gestión de proyectos de software hispanohablante.
Analiza esta tarea y clasifícala como MACRO o ESPECIFICA.

Tarea: "{texto}"{contexto_extra}

MACRO = actividad general que necesita varios pasos técnicos para completarse.
Ejemplos MACRO: "montar contenedor Docker de microservicio X", "implementar autenticacion JWT", "migrar base de datos PostgreSQL", "revisar proyecto ServiAyuda completo"

ESPECIFICA = una sola accion ejecutable directamente en menos de 2 horas.
Ejemplos ESPECIFICOS: "revisar la DB", "enviar correo a cliente", "actualizar contrasena", "crear ticket"

IMPORTANTE: Si la tarea es ambigua, social o personal (cumpleaños, permisos, reuniones), clasifícala como ESPECIFICA.

Responde UNICAMENTE con JSON valido en español, sin explicaciones:

Si es ESPECIFICA:
{{"tipo": "especifica", "tareas": []}}

Si es MACRO:
{{"tipo": "macro", "tareas": [
  {{"texto": "verbo + objeto ESPECIFICO con contexto del proyecto", "descripcion": "detalle técnico"}},
  {{"texto": "verbo + objeto ESPECIFICO con contexto del proyecto", "descripcion": "detalle técnico"}}
]}}

REGLAS OBLIGATORIAS:
- Todo el texto en ESPAÑOL
- Cada subtarea debe incluir el contexto específico (nombre del sistema/componente)
- VERBO + OBJETO concreto (máximo 8 palabras)
- Máximo 5 subtareas, mínimo 2
- Solo JSON válido, sin texto antes ni después"""

    try:
        # Streaming para análisis macro — sin timeout de conexión
        respuesta = _call_ollama([
            {"role": "system", "content": "Eres un asistente en español. SIEMPRE respondes en español. Respondes SOLO con JSON valido sin texto adicional."},
            {"role": "user", "content": prompt}
        ])

        # Extraer JSON
        parsed = None
        try:
            parsed = json.loads(respuesta)
        except Exception:
            i = respuesta.find('{')
            j = respuesta.rfind('}')
            if i != -1 and j > i:
                try:
                    parsed = json.loads(respuesta[i:j+1])
                except Exception:
                    pass

        if not parsed or not isinstance(parsed, dict):
            print(f"[MACRO] No se pudo parsear respuesta para: {texto!r}", flush=True)
            return [task]

        tipo = parsed.get("tipo", "especifica")

        if tipo == "macro":
            subtareas_raw = parsed.get("tareas", [])
            if not subtareas_raw or not isinstance(subtareas_raw, list):
                return [task]

            print(f"[MACRO] Tarea macro detectada: {texto!r} → {len(subtareas_raw)} subtareas", flush=True)

            resultado = []
            for sub in subtareas_raw:
                if not isinstance(sub, dict):
                    continue
                sub_texto = (sub.get("texto") or "").strip()
                if not sub_texto:
                    continue
                resultado.append({
                    "texto":        sub_texto,
                    "contexto":     (sub.get("descripcion") or "").strip(),
                    "responsable":  task.get("responsable", ""),
                    "proyecto":     task.get("proyecto", ""),
                    "subtarea":     "",
                    "fecha_inicio": task.get("fecha_inicio"),
                    "fecha_fin":    task.get("fecha_fin"),
                    "plazo":        task.get("plazo", ""),
                    "prioridad":    "media",
                    "_parent":      texto,   # referencia a la tarea padre (para logs)
                })
            return resultado if resultado else [task]

        else:
            print(f"[MACRO] Tarea específica: {texto!r}", flush=True)
            return [task]

    except Exception as e:
        print(f"[MACRO] Error analizando tarea {texto!r}: {e}", flush=True)
        return [task]


def extraer_tareas(texto):

    def _extract_json_from_text(s):

        try:

            i = s.find('{')

            j = s.rfind('}')

            if i != -1 and j != -1 and j > i:

                maybe = s[i:j+1]

                return json.loads(maybe)

        except Exception:

            return None

        return None



    prompt = """Eres un extractor de tareas de una reunión o llamada de equipo.

CONTEXTO DE LA CONVERSACIÓN:
La llamada sigue este patrón: un líder llama a cada persona por turno, y esa persona reporta sus tareas/avances. Luego el líder llama a otra persona. Así que QUIEN HABLA es generalmente el responsable de las tareas que menciona, a menos que diga explícitamente que otra persona debe hacer algo.

REGLAS:
- UNA tarea = UNA acción concreta ejecutable (VERBO + OBJETO)
- NO conviertas descripciones o explicaciones en tareas
- Para el responsable: infiere por contexto quién habla o a quién se le asigna
  · Si alguien dice "yo voy a hacer X" o "me toca X" → el responsable es quien habla
  · Si el líder dice "Pedro, necesitas hacer X" → responsable es Pedro
  · Si alguien reporta "hice X" o "ya está X" → tarea completada, responsable quien habla
  · Si no hay forma de inferir el nombre → deja vacío (nunca pongas "X" ni "No especificado")

FORMATO JSON OBLIGATORIO:
{
"pendientes": [
  {
    "texto": "verbo + objeto concreto",
    "descripcion": "contexto breve si aplica",
    "responsable": "nombre inferido del contexto o vacío",
    "proyecto": "nombre del proyecto si se menciona",
    "subtarea": "",
    "fecha_inicio": "texto exacto o null",
    "fecha_fin": "texto exacto o null"
  }
],
"completadas": []
}

REGLAS FINALES:
- "texto": solo la acción, sin nombre de persona ni proyecto
- "responsable": nombre real de persona, NUNCA "X", "Y", "No especificado", "Desconocido"
- "fecha_inicio"/"fecha_fin": copia literal del texto ("mañana", "el viernes"), null si no se menciona
- SI NO HAY TAREAS: {"pendientes":[],"completadas":[]}
"""

    # Usar streaming para el extractor — evita timeout con hardware lento
    # El streaming mantiene la conexión abierta y no tiene límite de tiempo fijo
    respuesta = _call_ollama([
        {"role": "system", "content": "Eres un extractor de tareas de reuniones de equipo. Infiere el responsable por el contexto conversacional: quien habla es generalmente el dueño de la tarea. Nunca uses X, Y, o No especificado como responsable."},
        {"role": "user", "content": f"Transcripción:\n{texto}\n\n{prompt}"}
    ])



    parsed = None

    try:

        parsed = json.loads(respuesta)

    except Exception:

        parsed = _extract_json_from_text(respuesta)



    def _local_extract_tasks(s):

        kws = ["revis", "enviar", "subir", "llam", "agend", "program", "contact", "mand", "crear", "actualiz", "solicit", "verificar", "corr", "archiv", "firm"]

        parts = [p.strip() for p in __import__('re').split(r'[\n\.!?]+', s) if p.strip()]

        tasks = []

        for p in parts:

            low = p.lower()

            if any(k in low for k in kws):

                tasks.append({"texto": p, "contexto": "", "responsable": "", "plazo": "", "prioridad": ""})

        return tasks



    try:

        if not isinstance(parsed, dict):

            local = _local_extract_tasks(texto)

            if local:

                print('[TASKS] Usando extractor local, encontró', len(local), 'tareas', flush=True)

                return {"pendientes": local, "completadas": []}

            return {"pendientes": [], "completadas": [], "raw": respuesta}



        pendientes = parsed.get("pendientes") if isinstance(parsed.get("pendientes"), list) else []

        completadas = parsed.get("completadas") if isinstance(parsed.get("completadas"), list) else []

        pendientes_norm  = [_normalize_task(t) for t in pendientes  if _normalize_task(t)]
        completadas_norm = [_normalize_task(t) for t in completadas if _normalize_task(t)]

        if not pendientes_norm and not completadas_norm:
            local = _local_extract_tasks(texto)
            if local:
                print('[TASKS] IA no detectó tareas, extractor local encontró', len(local), 'tareas', flush=True)
                return {"pendientes": local, "completadas": []}

        # Desglosar tareas macro en paralelo (no en serie para evitar timeouts)
        todas_norm = [
            (t, "pendiente")  for t in pendientes_norm  if t
        ] + [
            (t, "completada") for t in completadas_norm if t
        ]

        pendientes_final  = []
        completadas_final = []

        if todas_norm:
            # Ollama tiene 1 slot: procesar en serie, sin falso paralelismo.
            # Analizar macro solo las primeras 3 — las demás ya son concretas.
            MAX_MACRO_ANALISIS = 3 if len(todas_norm) <= 3 else 0

            for idx_t, (t, estado) in enumerate(todas_norm):
                if idx_t < MAX_MACRO_ANALISIS:
                    try:
                        resultado = _desglosar_si_macro(t)
                    except Exception as e:
                        print(f"[MACRO] Error: {e}, usando tarea original", flush=True)
                        resultado = [t]
                else:
                    resultado = [t]  # pasar directo sin análisis

                if estado == "pendiente":
                    pendientes_final.extend(resultado)
                else:
                    completadas_final.extend(resultado)

        return {"pendientes": pendientes_final, "completadas": completadas_final}

    except Exception:

        return {"pendientes": [], "completadas": [], "raw": respuesta}



# =========================

# GRABACIÓN

# =========================

def grabar_hasta_silencio(archivo_audio):

    global proceso_ffmpeg, listening



    bytes_por_chunk = CHUNK * 2

    chunks_silencio_max = int(SILENCIO_DURACION * RATE / CHUNK)

    chunks_min = int(MIN_DURACION * RATE / CHUNK)

    comando = [

        "ffmpeg", "-y",

        "-f", "pulse", "-i", MONITOR,

        "-f", "pulse", "-i", MICROFONO,

        "-filter_complex", "amix=inputs=2:duration=longest",

        "-ar", str(RATE), "-ac", "1", "-f", "s16le", "pipe:1"

    ]



    proceso_ffmpeg = subprocess.Popen(

        comando,

        stdout=subprocess.PIPE,

        stderr=subprocess.PIPE

    )



    frames = []

    chunks_leidos = 0

    chunks_en_silencio = 0

    try:

        while listening:

            data = proceso_ffmpeg.stdout.read(bytes_por_chunk)

            if not data:

                break

            frames.append(data)

            chunks_leidos += 1

            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)

            rms = np.sqrt(np.mean(samples ** 2))

            if chunks_leidos >= chunks_min:

                if rms < UMBRAL_SILENCIO:

                    chunks_en_silencio += 1

                else:

                    chunks_en_silencio = 0

                if chunks_en_silencio >= chunks_silencio_max:

                    break

    finally:

        proceso_ffmpeg.kill()

        proceso_ffmpeg.wait()

        proceso_ffmpeg = None



    if not frames:

        return False



    os.makedirs("temp", exist_ok=True)

    with wave.open(archivo_audio, "wb") as wf:

        wf.setnchannels(1)

        wf.setsampwidth(2)

        wf.setframerate(RATE)

        wf.writeframes(b"".join(frames))

    return True



def _wait_for_pending_transcriptions(timeout=30):
    """Espera a que la cola de Whisper se vacíe (todos los chunks procesados)."""
    deadline = time.time() + timeout
    while not _whisper_queue.empty():
        if time.time() >= deadline:
            pending = _whisper_queue.qsize()
            print(f"[WHISPER] Timeout esperando cola ({pending} chunks pendientes)", flush=True)
            return
        time.sleep(0.5)
    # Esperar también que el worker termine el chunk actual
    try:
        _whisper_queue.join()
    except Exception:
        pass



def _compact_text_for_ia(texto):
    """
    Comprime la transcripción sin llamar a Ollama.
    Estrategia: toma los últimos MAX_SUMMARY_CHARS del texto.
    - El inicio de una reunión suele ser saludos y check-ins sin tareas
    - El cuerpo y cierre contienen los compromisos reales
    - Si el texto cabe entero, no se toca
    """
    texto = texto.strip()
    if not texto or len(texto) <= MAX_SUMMARY_CHARS:
        return texto

    # Tomar el último bloque — cortar en línea completa si es posible
    fragmento = texto[-MAX_SUMMARY_CHARS:]
    primer_salto = fragmento.find('\n')
    if primer_salto != -1 and primer_salto < 200:
        fragmento = fragmento[primer_salto + 1:]

    comprimido = "[...inicio omitido...]\n" + fragmento.strip()
    print(f"[COMPACT] {len(texto)} → {len(comprimido)} chars (últimos {MAX_SUMMARY_CHARS})", flush=True)
    return comprimido




# Cola para extracción de tareas de chunks — serializa las llamadas a Ollama
_task_extraction_queue: queue.Queue = queue.Queue()
_task_extraction_worker_running = False
_task_extraction_lock = threading.Lock()

def _iniciar_worker_extraccion():
    """Lanza el worker de extracción si no está corriendo. Thread-safe."""
    global _task_extraction_worker_running
    with _task_extraction_lock:
        if _task_extraction_worker_running:
            return  # ya hay uno activo, la cola es compartida
        _task_extraction_worker_running = True
    # Lanzar fuera del lock para evitar deadlock si el thread falla al iniciar
    t = threading.Thread(target=_worker_extraccion, daemon=True, name="extractor-worker")
    t.start()

def _worker_extraccion():
    """
    Worker serializado de extracción de tareas.
    - Solo 1 instancia corre a la vez (_task_extraction_lock garantiza esto)
    - Espera hasta 5 min entre chunks (Ollama puede tardar mucho)
    - Se termina solo tras 5 min de inactividad real (cola vacía)
    """
    global _task_extraction_worker_running
    print("[EXTRACTOR] Worker iniciado", flush=True)
    _IDLE_TIMEOUT = 300  # 5 min sin items → terminar worker

    while True:
        try:
            item = _task_extraction_queue.get(timeout=_IDLE_TIMEOUT)
        except queue.Empty:
            # Cola vacía por IDLE_TIMEOUT — marcar como terminado y salir
            with _task_extraction_lock:
                _task_extraction_worker_running = False
            print("[EXTRACTOR] Worker idle 5min, terminando", flush=True)
            break

        chunk_text = item.get("text", "")
        if not chunk_text.strip():
            _task_extraction_queue.task_done()
            continue

        try:
            _extraer_tareas_chunk(chunk_text)
        except Exception as e:
            print(f"[EXTRACTOR] Error procesando chunk: {e}", flush=True)
        finally:
            _task_extraction_queue.task_done()


def _extraer_tareas_chunk(texto_chunk: str):
    """
    Extrae tareas de un chunk corto de transcripción.
    Prompt ultra-corto para respuesta rápida (~5-15s).
    Si no hay tareas, devuelve vacío sin generar approval request.
    """
    # Prompt mínimo — no pedir formato complejo, solo lo esencial
    prompt = f"""Analiza este fragmento de conversación y extrae SOLO las tareas/compromisos concretos mencionados.
Si no hay tareas claras, responde exactamente: NO_TASKS

Si hay tareas, responde SOLO este JSON (sin texto adicional):
{{"tareas":[{{"texto":"accion concreta","responsable":"nombre o vacio","plazo":"texto o null","estado":"pendiente"}}]}}

REGLAS:
- Solo acciones concretas (verbo+objeto), no opiniones ni explicaciones
- responsable: quien habla suele ser el dueño de la tarea
- Nunca uses X, Y, Todos, No especificado como responsable
- Si hay duda si es tarea, NO la incluyas

Fragmento:
{texto_chunk[:800]}"""

    respuesta = _call_ollama([
        {"role": "system", "content": "Extractor de tareas. Responde solo JSON o NO_TASKS. Sin explicaciones."},
        {"role": "user", "content": prompt}
    ])

    respuesta = respuesta.strip()
    if not respuesta or respuesta == "NO_TASKS" or "NO_TASKS" in respuesta:
        print(f"[EXTRACTOR] Sin tareas en chunk: {texto_chunk[:60]!r}", flush=True)
        return

    # Parsear JSON
    parsed = None
    try:
        parsed = json.loads(respuesta)
    except Exception:
        i = respuesta.find('{')
        j = respuesta.rfind('}')
        if i != -1 and j > i:
            try:
                parsed = json.loads(respuesta[i:j+1])
            except Exception:
                pass

    if not parsed or not isinstance(parsed, dict):
        return

    tareas_raw = parsed.get("tareas", [])
    if not tareas_raw:
        return

    pendientes  = []
    completadas = []
    for t in tareas_raw:
        if not isinstance(t, dict) or not t.get("texto"):
            continue
        t_norm = {
            "texto":        t.get("texto", "").strip(),
            "contexto":     "",
            "responsable":  t.get("responsable", "").strip(),
            "proyecto":     "",
            "subtarea":     "",
            "fecha_inicio": None,
            "fecha_fin":    t.get("plazo") or None,
            "plazo":        t.get("plazo", ""),
            "prioridad":    "media",
            "estado":       t.get("estado", "pendiente"),
        }
        if t_norm["estado"] == "completada":
            completadas.append(t_norm)
        else:
            pendientes.append(t_norm)

    if not pendientes and not completadas:
        return

    print(f"[EXTRACTOR] {len(pendientes)} pendientes, {len(completadas)} completadas en chunk", flush=True)

    tareas = {"pendientes": pendientes, "completadas": completadas}
    req_id = uuid.uuid4().hex[:12]
    publish({
        "type": "task_approval_request",
        "req_id": req_id,
        "tareas": tareas
    })


def encolar_extraccion_chunk(texto: str):
    """Encola un chunk para extracción de tareas en background."""
    if not texto or not texto.strip():
        return
    _task_extraction_queue.put({"text": texto})
    _iniciar_worker_extraccion()


# Cola de transcripción — 1 worker permanente, sin acumulación de threads
_whisper_queue: queue.Queue = queue.Queue(maxsize=20)  # máx 20 chunks pendientes
_whisper_worker_started = False
_whisper_worker_lock    = threading.Lock()


def _iniciar_whisper_worker():
    """Lanza el worker de Whisper si no está corriendo."""
    global _whisper_worker_started
    with _whisper_worker_lock:
        if _whisper_worker_started:
            return
        _whisper_worker_started = True
    threading.Thread(target=_whisper_worker, daemon=True, name="whisper-worker").start()


def _whisper_worker():
    """
    Worker único de Whisper. Procesa chunks de audio de a uno.
    - Sin semáforo (ya es 1 solo worker)
    - Si la cola está llena, el chunk más viejo se descarta silenciosamente
    """
    global _whisper_worker_started
    print("[WHISPER] Worker iniciado", flush=True)
    while True:
        try:
            item = _whisper_queue.get(timeout=120)
        except queue.Empty:
            with _whisper_worker_lock:
                _whisper_worker_started = False
            print("[WHISPER] Worker idle, terminando", flush=True)
            break

        fname = item.get("fname")
        if not fname:
            _whisper_queue.task_done()
            continue

        try:
            segments, _ = model.transcribe(fname, language="es", vad_filter=True)
            text = "".join(seg.text for seg in segments).strip()
            if text:
                _text_hash = hash(text.strip().lower())
                _now = time.time()
                with _dedup_lock:
                    if _now - _dedup_cache.get(_text_hash, 0) < 3.0:
                        pass  # duplicado
                    else:
                        _dedup_cache[_text_hash] = _now
                        viejos = [k for k, v in _dedup_cache.items() if _now - v > 30]
                        for k in viejos:
                            del _dedup_cache[k]
                        publish({"type": "transcription", "text": text})
                        with accum_lock:
                            global accumulated_transcript
                            if accumulated_transcript:
                                accumulated_transcript += "\n"
                            accumulated_transcript += text
        except Exception as e:
            print(f"[WHISPER] Error transcribiendo: {e}", flush=True)
        finally:
            try:
                os.remove(fname)
            except Exception:
                pass
            _whisper_queue.task_done()


def _transcribe_bytes_async(audio_bytes, publish_partial=True):
    """Encola audio para transcripción. Si la cola está llena descarta el chunk más viejo."""
    try:
        fname = f"temp/stream_{uuid.uuid4().hex}.wav"
        os.makedirs("temp", exist_ok=True)
        with wave.open(fname, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes(b"".join(audio_bytes))

        try:
            _whisper_queue.put_nowait({"fname": fname})
        except queue.Full:
            # Cola saturada — descartar chunk más viejo para hacer espacio
            try:
                old_item = _whisper_queue.get_nowait()
                if old_item.get("fname"):
                    os.remove(old_item["fname"])
                _whisper_queue.task_done()
            except Exception:
                pass
            _whisper_queue.put_nowait({"fname": fname})
            print("[WHISPER] Cola llena, descartado chunk más viejo", flush=True)

        _iniciar_whisper_worker()

    except Exception as e:
        print(f"[WHISPER] Error preparando chunk: {e}", flush=True)



# =========================

# LOOP PRINCIPAL

# =========================



def procesar_consulta(texto, publish_transcription=True, include_call_context=True, es_transcripcion=False):
    """
    es_transcripcion=True: viene del VAD/audio.
    - Skip detectar_tool
    - No agrega al historial de chat
    - No extrae tareas (lo hace el worker de chunks)
    """
    # Detección de tools sin llamada a Ollama — keyword simple, instantáneo
    texto_lower_tool = texto.lower()
    if "board" in texto_lower_tool and ("jira" in texto_lower_tool or "tablero" in texto_lower_tool):
        tool_response = '{"tool":"obtener_board","arguments":{"board_id":35}}'
    else:
        tool_response = "NO_TOOL"

    if publish_transcription:
        publish({"type": "transcription", "text": texto})

    cached = _cache_get(texto)
    if cached:
        publish(cached)
        return

    # Construir messages con historial para coherencia conversacional
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if include_call_context:
        with call_context_lock:
            if last_call_summary:
                messages.append({"role": "system",
                                  "content": "Contexto de llamada: " + last_call_summary[:500]})

    # Agregar historial de turnos anteriores (solo para consultas manuales)
    if not es_transcripcion:
        with _chat_history_lock:
            for turn in _chat_history[-(CHAT_HISTORY_MAX * 2):]:
                messages.append(turn)

    messages.append({"role": "user", "content": texto})



    if tool_response.strip() != "NO_TOOL":



        resultado_tool = ejecutar_tool(tool_response)



        respuesta = _call_ollama_sync([

            {"role": "system", "content": SYSTEM_PROMPT},

            {

                "role": "user",

                "content": f"""

Pregunta original:

{texto}

Resultado de la herramienta:

{json.dumps(resultado_tool, ensure_ascii=False)}

Responde usando esta información.

"""

            }

        ])



        publish({"type": "suggestion", "source": "ia", "text": respuesta})

        return



    print("TOOL RESPONSE:", tool_response)

    texto_lower = texto.lower()

    print("TEXTO RECIBIDO:", texto_lower)



    if "jira" in texto_lower and "board" in texto_lower:

        print("ENTRO A JIRA")

        resultado = obtener_board(35)

        if resultado["ok"]:

            publish({"type": "suggestion", "source": "ia", "text": json.dumps(resultado["data"], indent=2)})

        else:

            publish({"type": "notification", "level": "error", "message": resultado["error"]})

        return



    # SIEMPRE IA

    publish({"type": "status", "message": "Consultando IA...", "loading": True})



    if include_call_context:

        with call_context_lock:

            if last_call_summary:

                messages.append({

                    "role": "system",

                    "content": "Contexto de la última llamada: " + last_call_summary

                })

    messages.append({"role": "user", "content": texto})



    card_id = uuid.uuid4().hex[:8]

    publish({"type": "suggestion_start", "source": "ia", "card_id": card_id})



    respuesta_completa = []



    def on_token(token):

        respuesta_completa.append(token)

        publish({"type": "suggestion_chunk", "card_id": card_id, "text": token})



    _stream_ollama(messages, on_token)
    publish({"type": "suggestion_end", "card_id": card_id})
    respuesta_ia = "".join(respuesta_completa)

    # Guardar turno en historial (solo consultas manuales)
    if not es_transcripcion and respuesta_ia.strip():
        with _chat_history_lock:
            _chat_history.append({"role": "user",      "content": texto})
            _chat_history.append({"role": "assistant", "content": respuesta_ia})
            # Mantener máx CHAT_HISTORY_MAX turnos
            while len(_chat_history) > CHAT_HISTORY_MAX * 2:
                _chat_history.pop(0)
                _chat_history.pop(0)

    # Extraer tareas de la respuesta IA en background (no bloquea)
    if not es_transcripcion and respuesta_ia.strip():
        def _extraer_manual():
            try:
                publish({"type": "tasks_analyzing"})
                tareas = extraer_tareas(texto[-500:] + "\n" + respuesta_ia)
                if isinstance(tareas, dict) and (tareas.get("pendientes") or tareas.get("completadas")):
                    publish({"type": "task_approval_request",
                             "req_id": uuid.uuid4().hex[:12],
                             "tareas": tareas})
                else:
                    publish({"type": "tasks_none"})
            except Exception as e:
                print(f"[TASKS] Error: {e}", flush=True)
                publish({"type": "tasks_none"})
        threading.Thread(target=_extraer_manual, daemon=True).start()

    publish({"type": "status", "message": "Listo.", "loading": False})



def loop_escucha():

    global listening



    print("[LOOP] Iniciado escucha")



    while listening:

        print("[LOOP] Esperando audio...")

        publish({"type": "status", "message": "Escuchando...", "loading": False})

        grabado = grabar_hasta_silencio("temp/segmento.wav")

        print(f"[LOOP] grabado={grabado}")

        if not grabado or not listening:

            continue

        print("[LOOP] Transcribiendo...")

        publish({"type": "status", "message": "Transcribiendo...", "loading": True})

        segments, _ = model.transcribe("temp/segmento.wav", language="es", vad_filter=True)

        texto = "".join(seg.text for seg in segments).strip()

        if not texto:

            continue

        procesar_consulta(texto, True, False)




def _procesar_transcripcion_final(texto: str):
    """
    Procesa la transcripción acumulada al fin de una llamada.
    A diferencia de procesar_consulta(), esta función:
    - NO genera respuesta IA en streaming (innecesario para audio)
    - Solo extrae tareas directamente
    - Cada llamada Ollama tiene su propio timeout independiente
    - El resultado aparece en el panel de aprobación
    """
    if not texto or not texto.strip():
        publish({"type": "status", "message": "Listo.", "loading": False})
        return

    publish({"type": "tasks_analyzing"})
    publish({"type": "status", "message": "Extrayendo tareas de la llamada...", "loading": True})

    try:
        tareas = extraer_tareas(texto)

        pendientes  = tareas.get("pendientes",  [])
        completadas = tareas.get("completadas", [])

        if pendientes or completadas:
            req_id = uuid.uuid4().hex[:12]
            print(f"[FINAL] {len(pendientes)} pendientes, {len(completadas)} completadas → aprobación", flush=True)
            publish({
                "type": "task_approval_request",
                "req_id": req_id,
                "tareas": tareas
            })
        else:
            print("[FINAL] Sin tareas detectadas en la llamada", flush=True)
            publish({"type": "tasks_none"})

    except Exception as e:
        print(f"[FINAL] Error extrayendo tareas: {e}", flush=True)
        publish({"type": "tasks_none"})

    publish({"type": "status", "message": "Listo.", "loading": False})


def loop_escucha_streaming():
    """
    Escucha continua con VAD. Arquitectura de llamadas a la IA:

    - Whisper:  transcribe cada chunk en background (máx 2 paralelos)
                solo acumula texto en accumulated_transcript
    - Ollama:   se llama UNA sola vez al detectar fin de llamada
                O cada IA_ENVIO_INTERVALO segundos si la llamada es muy larga
                → nunca más de 1 llamada a IA concurrente por este loop

    Corte de chunks (solo para Whisper, no para IA):
      · silencio >= VAD_SILENCIO_CORTO tras >= VAD_CHUNK_MIN de contenido
      · OR acumulado >= VAD_CHUNK_MAX (corte forzado)
    """
    global listening, proceso_ffmpeg, accumulated_transcript, _ultimo_envio_ia

    print("[LOOP-STREAM] Iniciado", flush=True)

    comando = [
        "ffmpeg", "-y",
        "-f", "pulse", "-i", MONITOR,
        "-f", "pulse", "-i", MICROFONO,
        "-filter_complex", "amix=inputs=2:duration=longest",
        "-ar", str(RATE), "-ac", "1", "-f", "s16le", "pipe:1"
    ]

    proceso = subprocess.Popen(comando, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proceso_ffmpeg = proceso

    bytes_per_read = CHUNK * 2
    seg_por_read   = CHUNK / RATE   # ~0.064s por lectura con CHUNK=1024

    buf                = []
    seg_activos        = 0.0
    seg_silencio       = 0.0
    seg_silencio_total = 0.0

    # Para llamadas largas: enviamos a IA cada IA_ENVIO_INTERVALO sin esperar fin
    _ia_thread = None   # único thread de IA activo en este loop

    def _flush_whisper():
        """Manda el buffer acumulado a Whisper. No llama a Ollama."""
        nonlocal buf, seg_activos, seg_silencio
        if buf and seg_activos >= VAD_CHUNK_MIN:
            print(f"[VAD] Flush Whisper {round(seg_activos,1)}s", flush=True)
            _transcribe_bytes_async(list(buf))
        buf          = []
        seg_activos  = 0.0
        seg_silencio = 0.0

    def _enviar_a_ia(limpiar_acumulado=True):
        """
        Al fin de llamada o cada IA_ENVIO_INTERVALO:
        - Espera que Whisper termine los chunks pendientes
        - Encola el texto acumulado para extracción de tareas (worker serializado)
        - NO hace streaming de respuesta IA — solo extrae tareas
        - Cada chunk YA fue encolado individualmente en _transcribe_bytes_async
        - Este envío final sirve para asegurarse de no perder nada al final
        """
        nonlocal _ia_thread
        with _envio_ia_lock:
            global _ultimo_envio_ia
            _ultimo_envio_ia = time.time()

        # Si hay un thread IA corriendo, no lanzar otro
        if _ia_thread and _ia_thread.is_alive():
            print("[VAD] IA ocupada, omitiendo envío final (chunks ya encolados)", flush=True)
            return

        _wait_for_pending_transcriptions(timeout=20)

        with accum_lock:
            global accumulated_transcript
            texto = accumulated_transcript.strip()
            if limpiar_acumulado:
                accumulated_transcript = ""

        if not texto:
            return

        print(f"[VAD] Fin de llamada: {len(texto)} chars acumulados", flush=True)
        publish({"type": "status", "message": "Procesando llamada...", "loading": True})

        final_text = _compact_text_for_ia(texto)
        with call_context_lock:
            global last_call_summary
            last_call_summary = final_text

        # Encolar para extracción — el worker procesa de a 1, sin timeout de conexión
        _ia_thread = threading.Thread(
            target=_procesar_transcripcion_final,
            args=(final_text,),
            daemon=True
        )
        _ia_thread.start()

    try:
        while listening:
            data = proceso.stdout.read(bytes_per_read)
            if not data:
                time.sleep(0.005)
                continue

            samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            rms     = np.sqrt(np.mean(samples ** 2))
            hay_voz = rms >= UMBRAL_SILENCIO

            buf.append(data)
            seg_activos += seg_por_read

            if hay_voz:
                seg_silencio       = 0.0
                seg_silencio_total = 0.0
            else:
                seg_silencio       += seg_por_read
                seg_silencio_total += seg_por_read

            # ══ BLOQUE 1: Corte de chunk para Whisper ════════════════
            # Independiente del estado de la IA — siempre debe fluir
            if seg_activos >= VAD_CHUNK_MIN and seg_silencio >= VAD_SILENCIO_CORTO:
                _flush_whisper()
            elif seg_activos >= VAD_CHUNK_MAX:
                _flush_whisper()

            # ══ BLOQUE 2: Lógica de envío a IA ═══════════════════════
            # Fin de llamada (silencio muy largo)
            if seg_silencio_total >= SILENCIO_FIN_LLAMADA:
                print("[VAD] Silencio largo → fin de llamada", flush=True)
                _flush_whisper()
                _enviar_a_ia(limpiar_acumulado=True)
                seg_silencio_total = 0.0

            # Envío periódico durante llamada larga (solo si IA libre)
            elif not (_ia_thread and _ia_thread.is_alive()):
                with _envio_ia_lock:
                    tiempo_desde_ultimo = time.time() - _ultimo_envio_ia
                if tiempo_desde_ultimo >= IA_ENVIO_INTERVALO:
                    _flush_whisper()
                    _enviar_a_ia(limpiar_acumulado=False)

    except Exception as e:
        print(f"[VAD] Error: {e}", flush=True)
    finally:
        try:
            proceso.kill()
            proceso.wait()
        except Exception:
            pass
        proceso_ffmpeg = None
        _flush_whisper()
        _enviar_a_ia(limpiar_acumulado=True)



# =========================

# RUTAS

# =========================



@app.route('/')

def index():

    return render_template('copiloto.html')



@app.route('/consultar', methods=['POST'])

def consultar():

    from flask import request as req

    data = req.get_json(silent=True) or {}

    texto = (data.get('texto') or '').strip()

    if not texto:

        return jsonify({"ok": False, "error": "Texto vacío"}), 400

    threading.Thread(target=procesar_consulta, args=(texto,), daemon=True).start()

    return jsonify({"ok": True})



@app.route('/iniciar', methods=['POST'])
def iniciar():
    global listening
    if not listening:
        listening = True
        # Limpiar historial al empezar nueva sesión de escucha
        with _chat_history_lock:
            _chat_history.clear()
        if STREAMING_MODE:
            threading.Thread(target=loop_escucha_streaming, daemon=True).start()
        else:
            threading.Thread(target=loop_escucha, daemon=True).start()
        publish({"type": "state", "listening": True})
    return jsonify({"ok": True})



@app.route('/autorizar/<req_id>', methods=['POST'])

def autorizar(req_id):

    with pending_auths_lock:

        if req_id in pending_auths:

            pending_auths[req_id]["approved"] = True

            pending_auths[req_id]["event"].set()

    return jsonify({"ok": True})



@app.route('/denegar/<req_id>', methods=['POST'])

def denegar(req_id):

    with pending_auths_lock:

        if req_id in pending_auths:

            pending_auths[req_id]["approved"] = False

            pending_auths[req_id]["event"].set()

    return jsonify({"ok": True})



@app.route('/detener', methods=['POST'])

def detener():

    global listening, proceso_ffmpeg

    listening = False

    if proceso_ffmpeg:

        proceso_ffmpeg.kill()

    publish({"type": "state", "listening": False})

    return jsonify({"ok": True})



@app.route('/tasks', methods=['GET'])

def tasks():

    return jsonify({"tasks": fetch_tasks()})



@app.route('/tasks/<int:task_id>', methods=['PUT'])

def update_task(task_id):

    data = flask_request.get_json(silent=True) or {}

    updated = update_task_db(task_id, data)

    if not updated:

        return jsonify({"ok": False, "error": "Tarea no encontrada o datos inválidos"}), 400

    return jsonify({"ok": True, "task": updated})



@app.route('/tasks/<int:task_id>', methods=['DELETE'])

def delete_task(task_id):

    success = delete_task_db(task_id)

    return jsonify({"ok": success})



# =====================================================
# NUEVA RUTA: recibir tareas aprobadas por el usuario
# =====================================================

@app.route('/reset_chat', methods=['POST'])
def reset_chat():
    """Limpia el historial de conversación. Llamar al recargar la página."""
    with _chat_history_lock:
        _chat_history.clear()
    return jsonify({"ok": True})


@app.route('/extraer_tareas_chat', methods=['POST'])
def extraer_tareas_chat():
    """Extrae tareas del historial de conversación manual bajo demanda."""
    with _chat_history_lock:
        if not _chat_history:
            return jsonify({"ok": False, "error": "Sin historial de conversación"})
        # Construir texto del historial completo
        texto_historial = "\n".join(
            f"{'Usuario' if t['role']=='user' else 'Asistente'}: {t['content']}"
            for t in _chat_history
        )

    publish({"type": "tasks_analyzing"})

    def _extraer():
        try:
            tareas = extraer_tareas(texto_historial[-2000:])
            if isinstance(tareas, dict) and (tareas.get("pendientes") or tareas.get("completadas")):
                publish({"type": "task_approval_request",
                         "req_id": uuid.uuid4().hex[:12],
                         "tareas": tareas})
            else:
                publish({"type": "tasks_none"})
        except Exception as e:
            print(f"[EXTRAER CHAT] Error: {e}", flush=True)
            publish({"type": "tasks_none"})

    threading.Thread(target=_extraer, daemon=True).start()
    return jsonify({"ok": True})


@app.route('/aprobar_tareas', methods=['POST'])

def aprobar_tareas():

    data = flask_request.get_json(silent=True) or {}

    tareas = data.get("tareas")  # {"pendientes": [...], "completadas": [...]}

    if not tareas or not isinstance(tareas, dict):

        return jsonify({"ok": False, "error": "Sin tareas válidas"}), 400



    print(f"[APPROVAL] Guardando tareas aprobadas: pendientes={len(tareas.get('pendientes', []))}, completadas={len(tareas.get('completadas', []))}")



    saved = create_tasks_batch(tareas)

    publish({"type": "tasks_saved", "tasks": saved})

    return jsonify({"ok": True, "tasks": saved})



@app.route('/stream')

def stream():

    client_queue = queue.Queue(maxsize=100)

    with subscribers_lock:

        subscribers.append(client_queue)

    print(f"[STREAM] Nuevo cliente. Total subscribers: {len(subscribers)}", flush=True)



    def generate():

        timeout_count = 0

        try:

            while True:

                try:

                    msg = client_queue.get(timeout=30)

                    timeout_count = 0

                    yield f"data: {json.dumps(msg)}\n\n"

                except queue.Empty:

                    timeout_count += 1

                    if timeout_count > 40:  # 40 x 30s = 20 min sin actividad

                        print(f"[STREAM] Cliente inactivo >20min, cerrando", flush=True)

                        break

                    yield 'data: {"type":"ping"}\n\n'

                except Exception as e:

                    print(f"[STREAM] Error en generador: {e}", flush=True)

                    break

        except GeneratorExit:

            pass

        except Exception as e:

            print(f"[STREAM] Error general: {e}", flush=True)

        finally:

            with subscribers_lock:

                try:

                    if client_queue in subscribers:

                        subscribers.remove(client_queue)

                        print(f"[STREAM] Cliente desconectado. Total subscribers: {len(subscribers)}", flush=True)

                except Exception as e:

                    print(f"[STREAM] Error removiendo subscriber: {e}", flush=True)



    return Response(

        generate(),

        mimetype='text/event-stream',

        headers={

            'Cache-Control': 'no-cache',

            'X-Accel-Buffering': 'no',

            'Connection': 'keep-alive'

        }

    )



# =========================

# START

# =========================



if __name__ == '__main__':

    init_db()

    app.run(

        debug=False,

        host='0.0.0.0',

        port=5001,

        threaded=True

    )