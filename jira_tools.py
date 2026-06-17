import requests
import unicodedata
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta


from dotenv import load_dotenv
import os

load_dotenv()

USER = os.getenv("JIRA_USER")
TOKEN = os.getenv("JIRA_TOKEN")
JIRA_URL = os.getenv("JIRA_URL")

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
            return {"ok": False, "status": response.status_code, "error": response.text}
        try:
            return {"ok": True, "data": response.json()}
        except Exception:
            return {"ok": True, "data": response.text}


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────


client   = APIClient(USER, TOKEN)


DEFAULT_ACCOUNT_ID = "712020:dd73a148-eb0f-45b0-9538-84b2f441e190"
DEFAULT_EPIC_KEY   = "SERVITEL-76"   # épica por defecto si no se encuentra ninguna


# ─────────────────────────────────────────────
# UTILIDAD: normalización de texto
# ─────────────────────────────────────────────

def _norm(s: str) -> str:
    """Minúsculas y sin acentos para comparación robusta."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )


# ─────────────────────────────────────────────
# CACHÉ DE USUARIOS
# ─────────────────────────────────────────────

_usuarios_cache: dict = {}   # {nombre_norm: accountId}
_usuarios_cargado = False


def _cargar_usuarios(forzar=False):
    global _usuarios_cargado
    if _usuarios_cargado and not forzar:
        return

    print("[JIRA-USERS] Cargando usuarios desde Jira...")
    res = client.request(url=JIRA_URL, method="GET",
                         path="/rest/api/3/users/search",
                         params={"maxResults": 200})

    if not res["ok"]:
        print(f"[JIRA-USERS] Error: {res.get('error', '')} | status={res.get('status')}")
        return

    usuarios = res["data"] if isinstance(res["data"], list) else []
    print(f"[JIRA-USERS] API devolvió {len(usuarios)} usuarios")

    _usuarios_cache.clear()
    activos = ignorados = 0

    for u in usuarios:
        if not u.get("active", False):
            ignorados += 1
            continue
        if u.get("accountType") != "atlassian":
            ignorados += 1
            continue

        nombre     = (u.get("displayName") or "").strip()
        account_id = u.get("accountId", "")
        email      = (u.get("emailAddress") or "").strip().lower()

        if nombre and account_id:
            _usuarios_cache[_norm(nombre)] = account_id
            activos += 1

        if email and account_id:
            alias = email.split("@")[0].replace(".", " ").replace("_", " ")
            key_alias = _norm(alias)
            if key_alias not in _usuarios_cache:
                _usuarios_cache[key_alias] = account_id

    _usuarios_cargado = True
    print(f"[JIRA-USERS] Caché listo: {activos} activos, {ignorados} ignorados → {list(_usuarios_cache.keys())}")


def buscar_usuario(nombre: str) -> dict:
    """Busca usuario por nombre con coincidencia flexible."""
    _cargar_usuarios()

    if not nombre or not nombre.strip():
        return {"ok": False, "error": "Nombre vacío"}

    nombre_norm    = _norm(nombre)
    palabras_query = set(nombre_norm.split())

    # 1. Exacto
    if nombre_norm in _usuarios_cache:
        return {"ok": True, "accountId": _usuarios_cache[nombre_norm], "displayName": nombre_norm}

    # 2. Query contenida en displayName ("david" → "david roa martinez")
    for display, aid in _usuarios_cache.items():
        if nombre_norm in display:
            print(f"[JIRA-USERS] Parcial: {nombre_norm!r} → {display!r}")
            return {"ok": True, "accountId": aid, "displayName": display}

    # 3. DisplayName contenido en query
    for display, aid in _usuarios_cache.items():
        if display in nombre_norm:
            return {"ok": True, "accountId": aid, "displayName": display}

    # 4. Mayor coincidencia por palabras
    mejor, score = None, 0
    for display, aid in _usuarios_cache.items():
        c = len(palabras_query & set(display.split()))
        if c > score:
            score, mejor = c, (display, aid)

    if mejor and score > 0:
        print(f"[JIRA-USERS] Por palabras ({score}): {nombre_norm!r} → {mejor[0]!r}")
        return {"ok": True, "accountId": mejor[1], "displayName": mejor[0]}

    print(f"[JIRA-USERS] No encontrado: {nombre_norm!r}")
    return {"ok": False, "error": f"Usuario '{nombre}' no encontrado"}


def resolver_account_id(nombre: str, fallback_account_id: str = None) -> str:
    if not nombre:
        return fallback_account_id or DEFAULT_ACCOUNT_ID
    res = buscar_usuario(nombre)
    if res["ok"]:
        print(f"[JIRA-USERS] Asignando a '{res['displayName']}' → {res['accountId']}")
        return res["accountId"]
    print(f"[JIRA-USERS] {res['error']} — usando fallback")
    return fallback_account_id or DEFAULT_ACCOUNT_ID


# ─────────────────────────────────────────────
# CACHÉ DE ÉPICAS
# ─────────────────────────────────────────────

# Lista de épicas: [{"key": "SERVITEL-19", "summary": "Shopify Descuentos", "summary_norm": "shopify descuentos", "status": "Finalizada"}]
_epicas_cache: list = []
_epicas_cargado = False


def _cargar_epicas(forzar=False):
    """Carga todas las épicas activas del proyecto y las guarda en caché."""
    global _epicas_cargado
    if _epicas_cargado and not forzar:
        return

    print("[JIRA-EPICAS] Cargando épicas desde Jira...")
    res = client.request(
        url=JIRA_URL,
        method="POST",
        path="/rest/api/3/search/jql",
        json={
            "jql": "project = SERVITEL AND issuetype = Epic ORDER BY created DESC",
            "fields": ["summary", "issuetype", "status"],
            "maxResults": 100
        }
    )

    if not res["ok"]:
        print(f"[JIRA-EPICAS] Error: {res.get('error', '')} | status={res.get('status')}")
        return

    issues = res["data"].get("issues", []) if isinstance(res["data"], dict) else []
    print(f"[JIRA-EPICAS] API devolvió {len(issues)} épicas")

    _epicas_cache.clear()
    for issue in issues:
        key     = issue.get("key", "")
        fields  = issue.get("fields", {})
        summary = (fields.get("summary") or "").strip()
        status  = fields.get("status", {}).get("name", "")
        if key and summary:
            _epicas_cache.append({
                "key":          key,
                "summary":      summary,
                "summary_norm": _norm(summary),
                "status":       status,
            })

    _epicas_cargado = True
    print(f"[JIRA-EPICAS] Caché listo: {len(_epicas_cache)} épicas")
    for e in _epicas_cache:
        print(f"  {e['key']:15} [{e['status']:12}] {e['summary']}")


def buscar_epica(texto: str) -> dict:
    """
    Busca la épica más relevante para un texto dado.
    Estrategia:
      1. Alguna palabra del texto coincide con palabras del nombre de la épica
      2. Prioriza épicas activas (no Finalizada/Done)
      3. Devuelve la épica con mayor score

    Devuelve {"ok": True, "key": "SERVITEL-XX", "summary": "..."} o {"ok": False}
    """
    _cargar_epicas()

    if not _epicas_cache:
        return {"ok": False, "error": "Sin épicas en caché"}

    if not texto or not texto.strip():
        return {"ok": False, "error": "Texto vacío"}

    texto_norm     = _norm(texto)
    palabras_texto = set(texto_norm.split())

    # Filtrar palabras cortas o muy comunes que no ayudan a discriminar
    STOPWORDS = {"de", "la", "el", "en", "a", "con", "para", "por", "del", "los",
                 "las", "un", "una", "y", "o", "que", "es", "se", "al"}
    palabras_texto -= STOPWORDS

    mejores = []
    for epica in _epicas_cache:
        palabras_epica = set(epica["summary_norm"].split()) - STOPWORDS
        coincidencias  = len(palabras_texto & palabras_epica)
        if coincidencias > 0:
            # Bonus si la épica está activa (no finalizada)
            bonus = 0 if epica["status"].lower() in ("finalizada", "done", "listo", "closed") else 1
            mejores.append((coincidencias + bonus, epica))

    if not mejores:
        print(f"[JIRA-EPICAS] Sin coincidencia para: {texto!r}")
        return {"ok": False, "error": "Sin épica coincidente"}

    mejores.sort(key=lambda x: x[0], reverse=True)
    score, epica = mejores[0]
    print(f"[JIRA-EPICAS] Épica seleccionada (score={score}): {epica['key']} — {epica['summary']!r}")
    return {"ok": True, "key": epica["key"], "summary": epica["summary"], "score": score}


def resolver_epic_key(texto_tarea: str, proyecto: str = "", fallback: str = None) -> str:
    """
    Dado el texto de una tarea (y opcionalmente el nombre de proyecto),
    devuelve la key de la épica más relevante.
    Si no encuentra ninguna, devuelve el fallback o DEFAULT_EPIC_KEY.
    """
    # Combinar texto de tarea + proyecto para mejor matching
    texto_busqueda = f"{proyecto} {texto_tarea}".strip()
    res = buscar_epica(texto_busqueda)
    if res["ok"]:
        return res["key"]
    # Intentar solo con el proyecto si tenemos nombre
    if proyecto:
        res2 = buscar_epica(proyecto)
        if res2["ok"]:
            return res2["key"]
    return fallback or DEFAULT_EPIC_KEY


# ─────────────────────────────────────────────
# GET BOARD
# ─────────────────────────────────────────────

def obtener_board(board_id=35):
    return client.request(url=JIRA_URL, method="GET",
                          path=f"/rest/agile/1.0/board/{board_id}")


# ─────────────────────────────────────────────
# CREAR TAREA
# ─────────────────────────────────────────────

def crear_tarea(summary, description, project_id="10034", issuetype_id="10044",
                duedate=None, startdate=None, responsable=None, proyecto=None):
    """
    Crea una tarea en Jira.
    - Resuelve el accountId del responsable por nombre
    - Detecta automáticamente la épica más relevante por el summary y proyecto
    """
    hoy     = datetime.utcnow().date()
    hoy_iso = hoy.isoformat()
    fin_iso = (hoy + timedelta(days=20)).isoformat()

    startdate_final = startdate if startdate and startdate != "None" else hoy_iso
    duedate_final   = duedate   if duedate   and duedate   != "None" else fin_iso
    desc_text       = (description or "").strip() or "Sin descripción"

    # Resolver assignee
    account_id = resolver_account_id(responsable)

    # Resolver épica — busca por summary de la tarea + nombre del proyecto
    epic_key = resolver_epic_key(
        texto_tarea=summary,
        proyecto=proyecto or "",
        fallback=DEFAULT_EPIC_KEY
    )

    body = {
        "fields": {
            "project":   {"id": project_id},
            "summary":   summary,
            "issuetype": {"id": issuetype_id},
            "assignee":  {"accountId": account_id},
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph",
                              "content": [{"type": "text", "text": desc_text}]}]
            },
            "parent":           {"key": epic_key},
            "duedate":          duedate_final,
            "customfield_10015": startdate_final
        }
    }

    print(f"[JIRA] summary={summary!r}")
    print(f"[JIRA] assignee={account_id!r}  epic={epic_key}  start={startdate_final}  due={duedate_final}")

    return client.request(url=JIRA_URL, method="POST",
                          path="/rest/api/3/issue", json=body)


# ─────────────────────────────────────────────
# PRECARGAR AL IMPORTAR
# ─────────────────────────────────────────────

try:
    _cargar_usuarios()
except Exception as _e:
    print(f"[JIRA-USERS] Advertencia al precargar usuarios: {_e}")

try:
    _cargar_epicas()
except Exception as _e:
    print(f"[JIRA-EPICAS] Advertencia al precargar épicas: {_e}")


# ─────────────────────────────────────────────
# USO DIRECTO
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n── Usuarios ──")
    print(list(_usuarios_cache.keys()))
    print(buscar_usuario("iker"))
    print(buscar_usuario("david"))

    print("\n── Épicas ──")
    for e in _epicas_cache:
        print(f"  {e['key']} [{e['status']}] {e['summary']}")

    print("\n── Búsqueda de épica ──")
    print(buscar_epica("serviayuda documentacion"))
    print(buscar_epica("cotizador proteccion pdf"))
    print(buscar_epica("tarea general sin proyecto"))

    print("\n── Crear tarea de prueba ──")
    task = crear_tarea(
        summary="SERVIAYUDA - revisar documentación del módulo",
        description="Tarea de prueba con épica automática",
        startdate="2026-06-20",
        duedate="2026-06-30",
        responsable="iker rivera",
        proyecto="SERVIAYUDA"
    )
    print("POST TASK:", task)