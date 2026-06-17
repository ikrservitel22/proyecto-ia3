import requests
import unicodedata
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

load_dotenv()

JIRA_URL = os.getenv("JIRA_URL")
USER = os.getenv("JIRA_USER")
TOKEN = os.getenv("JIRA_TOKEN")

class APIClient:
    def __init__(self, user, token):
        self.auth = HTTPBasicAuth(user, token)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

    def request(self, url, method="GET", path="", params=None, json=None, headers=None):
        full_url = url.rstrip("/") + path
        final_headers = self.headers.copy()
        if headers:
            final_headers.update(headers)

        response = requests.request(
            method=method.upper(),
            url=full_url,
            auth=self.auth,
            headers=final_headers,
            params=params,
            json=json
        )

        if not response.ok:
            return {
                "ok": False,
                "status": response.status_code,
                "error": response.text
            }

        try:
            return {"ok": True, "data": response.json()}
        except Exception:
            return {"ok": True, "data": response.text}


# =========================
# CONFIG GLOBAL
# =========================


client   = APIClient(USER, TOKEN)

# Caché en memoria: displayName.lower() → accountId
# Se llena la primera vez que se llama a buscar_usuario o resolver_account_id
_usuarios_cache: dict = {}   # {"iker rivera": "712020:dd73a148-..."}
_cache_cargado = False


# =========================
# USUARIOS
# =========================

def _norm(s: str) -> str:
    """Normaliza texto: minúsculas y sin acentos para comparación robusta."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def _cargar_usuarios(forzar=False):
    """Carga todos los usuarios activos de Jira y llena el caché."""
    global _cache_cargado
    if _cache_cargado and not forzar:
        return

    print("[JIRA-USERS] Cargando usuarios desde Jira...")
    res = client.request(
        url=JIRA_URL,
        method="GET",
        path="/rest/api/3/users/search",
        params={"maxResults": 200}
    )

    if not res["ok"]:
        print(f"[JIRA-USERS] Error cargando usuarios: {res.get('error', '')} | status={res.get('status')}")
        # NO marcar como cargado para que reintente en la próxima tarea
        return

    usuarios = res["data"] if isinstance(res["data"], list) else []
    print(f"[JIRA-USERS] API devolvió {len(usuarios)} usuarios")

    _usuarios_cache.clear()
    activos = 0
    ignorados = 0
    for u in usuarios:
        # SOLO usuarios activos
        if not u.get("active", False):
            ignorados += 1
            continue
        # SOLO tipo atlassian (excluir bots, apps, cuentas de sistema)
        if u.get("accountType") != "atlassian":
            ignorados += 1
            continue

        nombre    = (u.get("displayName") or "").strip()
        account_id = u.get("accountId", "")
        email     = (u.get("emailAddress") or "").strip().lower()

        if nombre and account_id:
            _usuarios_cache[_norm(nombre)] = account_id
            activos += 1
        # Indexar también por email sin dominio como alias (ej: "iker.rivera" → "iker rivera")
        if email and account_id:
            alias = email.split("@")[0].replace(".", " ").replace("_", " ")
            key_alias = _norm(alias)
            if key_alias not in _usuarios_cache:
                _usuarios_cache[key_alias] = account_id

    _cache_cargado = True
    print(f"[JIRA-USERS] Caché listo: {activos} usuarios activos, {ignorados} ignorados → {list(_usuarios_cache.keys())}") 


def buscar_usuario(nombre: str) -> dict:
    """
    Busca un usuario por nombre (displayName, parcial o completo).
    Estrategias en orden:
      1. Coincidencia exacta
      2. El nombre buscado está contenido en el displayName
      3. El displayName está contenido en el nombre buscado
      4. Coincidencia por palabras individuales (al menos 1 palabra del nombre coincide)
    """
    _cargar_usuarios()

    if not nombre:
        return {"ok": False, "error": "Nombre vacío"}

    # Normalizar el nombre buscado (sin acentos, minúsculas)
    nombre_norm = _norm(nombre)
    palabras_busqueda = set(nombre_norm.split())

    print(f"[JIRA-USERS] Buscando: {nombre_norm!r} | caché: {list(_usuarios_cache.keys())}")

    # 1. Coincidencia exacta (ya normalizado)
    if nombre_norm in _usuarios_cache:
        return {"ok": True, "accountId": _usuarios_cache[nombre_norm], "displayName": nombre_norm}

    # 2. El nombre buscado está contenido en el displayName  (ej: "david" en "david roa")
    for display, account_id in _usuarios_cache.items():
        if nombre_norm in display:
            print(f"[JIRA-USERS] Parcial: {nombre_norm!r} → {display!r}")
            return {"ok": True, "accountId": account_id, "displayName": display}

    # 3. El displayName está contenido en el nombre buscado
    for display, account_id in _usuarios_cache.items():
        if display in nombre_norm:
            print(f"[JIRA-USERS] Parcial inverso: {display!r} → {display!r}")
            return {"ok": True, "accountId": account_id, "displayName": display}

    # 4. Al menos UNA palabra coincide — priorizamos el que más palabras comparte
    mejor_match = None
    mejor_score = 0
    for display, account_id in _usuarios_cache.items():
        palabras_display = set(display.split())
        coincidencias = len(palabras_busqueda & palabras_display)
        if coincidencias > mejor_score:
            mejor_score = coincidencias
            mejor_match = (display, account_id)

    if mejor_match and mejor_score > 0:
        print(f"[JIRA-USERS] Por palabras ({mejor_score} coincidencia(s)): {nombre_norm!r} → {mejor_match[0]!r}")
        return {"ok": True, "accountId": mejor_match[1], "displayName": mejor_match[0]}

    print(f"[JIRA-USERS] No encontrado: {nombre_norm!r}")
    return {"ok": False, "error": f"Usuario '{nombre}' no encontrado en Jira"}


def resolver_account_id(nombre: str, fallback_account_id: str = None) -> str:
    """
    Dado un nombre de persona, devuelve su accountId en Jira.
    Si no se encuentra, devuelve fallback_account_id (o None).
    """
    if not nombre:
        return fallback_account_id

    res = buscar_usuario(nombre)
    if res["ok"]:
        print(f"[JIRA-USERS] Asignando a '{res['displayName']}' → {res['accountId']}")
        return res["accountId"]

    print(f"[JIRA-USERS] {res['error']} — usando fallback")
    return fallback_account_id


# =========================
# GET BOARD
# =========================

def obtener_board(board_id=35):
    return client.request(
        url=JIRA_URL,
        method="GET",
        path=f"/rest/agile/1.0/board/{board_id}"
    )


# =========================
# POST TAREA
# =========================

# accountId por defecto si no se puede resolver el responsable
DEFAULT_ACCOUNT_ID = "712020:dd73a148-eb0f-45b0-9538-84b2f441e190"

def crear_tarea(summary, description, project_id="10034", issuetype_id="10044",
                duedate=None, startdate=None, responsable=None):

    hoy     = datetime.utcnow().date()
    hoy_iso = hoy.isoformat()
    fin_iso = (hoy + timedelta(days=20)).isoformat()

    startdate_final = startdate if startdate and startdate != "None" else hoy_iso
    duedate_final   = duedate   if duedate   and duedate   != "None" else fin_iso
    desc_text       = (description or "").strip() or "Sin descripción"

    # Resolver accountId del responsable
    account_id = resolver_account_id(responsable, fallback_account_id=DEFAULT_ACCOUNT_ID)

    body = {
        "fields": {
            "project":   {"id": project_id},
            "summary":   summary,
            "issuetype": {"id": issuetype_id},

            "assignee": {"accountId": account_id},

            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": desc_text}]
                    }
                ]
            },

            "parent": {"key": "SERVITEL-18"},

            "duedate":            duedate_final,
            "customfield_10015":  startdate_final
        }
    }

    print(f"[JIRA] summary={summary!r}  assignee={account_id!r}  start={startdate_final}  due={duedate_final}")

    return client.request(
        url=JIRA_URL,
        method="POST",
        path="/rest/api/3/issue",
        json=body
    )


# Precargar usuarios al importar el módulo para que la primera tarea
# ya tenga el caché listo sin esperar
try:
    _cargar_usuarios()
except Exception as _e:
    print(f"[JIRA-USERS] Advertencia: no se pudo precargar usuarios: {_e}")


# =========================
# USO DIRECTO
# =========================

if __name__ == "__main__":
    # Ver usuarios disponibles
    _cargar_usuarios()
    print("Usuarios en caché:", list(_usuarios_cache.keys()))

    # Probar búsqueda
    print(buscar_usuario("iker"))

    # Crear tarea asignada por nombre
    task = crear_tarea(
        summary="Tarea de prueba con asignación automática",
        description="Creada automáticamente por el sistema",
        startdate="2026-06-20",
        duedate="2026-06-30",
        responsable="iker rivera"
    )
    print("POST TASK:", task)