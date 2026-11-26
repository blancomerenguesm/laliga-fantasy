import json
import os
import re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

PUNTOS_ENTRENADOR = {
    "victoria": 5,
    "empate": 2,
    "derrota": 0,
}

BASE_URL = "https://www.futbolfantasy.com"
CONFIG_JORNADA_FILE = Path("config_jornada.json")
ESTADO_FILE = Path("estado_relevo.json")
SALIDA_FILE = Path("puntos.json")


def limpiar_texto(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "")).strip()


def normalizar_clave(txt: str) -> str:
    s = limpiar_texto(txt).lower()
    s = re.sub(r"\b\d+'", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parsear_numero(valor: str):
    limpio = limpiar_texto(valor).replace(",", ".")
    if not limpio or limpio == "-":
        return None
    if re.fullmatch(r"-?\d+", limpio):
        return int(limpio)
    if re.fullmatch(r"-?\d+\.\d+", limpio):
        return float(limpio)
    return None


def puntos_coach_por_goles(gf: int, gc: int) -> int:
    if gf > gc:
        return PUNTOS_ENTRENADOR["victoria"]
    if gf == gc:
        return PUNTOS_ENTRENADOR["empate"]
    return PUNTOS_ENTRENADOR["derrota"]


def obtener_jornada_objetivo() -> int | None:
    env_j = os.environ.get("JORNADA", "").strip()
    if env_j.isdigit():
        return int(env_j)
    if CONFIG_JORNADA_FILE.exists():
        try:
            data = json.loads(CONFIG_JORNADA_FILE.read_text(encoding="utf-8"))
            val = str(data.get("jornada", "")).strip()
            if val.isdigit():
                return int(val)
        except Exception:
            pass
    return None


def descubrir_urls_partidos(jornada_objetivo: int | None) -> list[str]:
    """Busca enlaces /partidos/... de la jornada objetivo en calendario/portada de FútbolFantasy."""
    candidatas_fuente = [
        f"{BASE_URL}/laliga/calendario",
        f"{BASE_URL}/laliga/home",
        BASE_URL,
    ]
    urls_encontradas = []
    vistas = set()

    for fuente_url in candidatas_fuente:
        try:
            resp = requests.get(fuente_url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
        except Exception:
            continue

        soup = BeautifulSoup(resp.text, "html.parser")

        # Si hay bloques de jornada específicos, priorizamos el que coincida con jornada_objetivo
        contenedores = []
        if jornada_objetivo is not None:
            for bloque in soup.select("section, div, article"):
                cab = bloque.find(["h1", "h2", "h3", "h4", "header"])
                if cab and re.search(rf"\bJornada\s+{jornada_objetivo}\b", cab.get_text(" ", strip=True), re.I):
                    if bloque.select("a[href*='/partidos/']"):
                        contenedores.append(bloque)

        alcance = contenedores if contenedores else [soup]
        for root in alcance:
            for a in root.select("a[href*='/partidos/']"):
                href = (a.get("href") or "").strip()
                if not href or "/partidos/" not in href:
                    continue
                full_url = urljoin(BASE_URL, href.split("#")[0].split("?")[0])
                if re.search(r"/partidos/\d+", full_url) and full_url not in vistas:
                    vistas.add(full_url)
                    urls_encontradas.append(full_url)

        if len(urls_encontradas) >= 5:
            break

    if urls_encontradas:
        return urls_encontradas

    # Respaldo si no se pudieron descubrir enlaces dinámicamente
    return [f"{BASE_URL}/partidos/22547-levante-elche"]


def extraer_desglose_lf(desg_lf) -> tuple[list[str], str | None, float | int | None]:
    """Devuelve (lineas_desglose, nombre_completo, puntos_dazn_relevo)."""
    if not desg_lf:
        return [], None, None

    nombre_completo = None
    ficha_a = desg_lf.select_one("a.link[href*='/jugadores/']")
    if ficha_a:
        m = re.search(r"/jugadores/([^/]+)/", ficha_a.get("href", ""))
        if m:
            slug = re.sub(r"-\d+$", "", m.group(1))
            nombre_completo = slug.replace("-", " ").title()

    lineas = []
    puntos_dazn = None
    texto_acumulado_previo = ""

    for child in desg_lf.children:
        if isinstance(child, str):
            t = limpiar_texto(child)
            if t:
                texto_acumulado_previo = f"{texto_acumulado_previo} {t}".strip()
            continue

        clases = child.get("class", []) if getattr(child, "name", None) else []
        if getattr(child, "name", None) == "div" and "estadistica" in clases:
            copia = BeautifulSoup(str(child), "html.parser")
            for icono in copia.select("i.fa-arrow-right"):
                icono.replace_with(" -> ")
            linea = limpiar_texto(copia.get_text(" ", strip=True))
            if texto_acumulado_previo and not re.search(
                r"\d+\s+Minutos|Balones|Posesiones|Goles|Tarjetas|Despejes|Regates|Asistencias|Puntos DAZN",
                linea,
                re.I,
            ):
                linea = f"{texto_acumulado_previo} {linea}".strip()
            texto_acumulado_previo = ""
            linea = re.sub(r"\s*->\s*", " -> ", linea)
            if linea:
                lineas.append(linea)

        elif getattr(child, "name", None) == "div" and "relevo-points" in clases:
            for est in child.select("div.estadistica"):
                copia = BeautifulSoup(str(est), "html.parser")
                for icono in copia.select("i.fa-arrow-right"):
                    icono.replace_with(" -> ")
                linea = limpiar_texto(copia.get_text(" ", strip=True))
                linea = re.sub(r"\s*->\s*", " -> ", linea)
                if linea:
                    lineas.append(linea)
                m_pts = re.search(r"(-?\d+(?:[.,]\d+)?)\s*p\b", linea)
                if m_pts:
                    puntos_dazn = parsear_numero(m_pts.group(1))
            texto_acumulado_previo = ""

    return lineas, nombre_completo, puntos_dazn


def detectar_equipo_tabla(tabla, eq_local: str, eq_visit: str, idx_tabla: int) -> str:
    side = tabla.find_parent(attrs={"data-match-p11-team": True})
    if side:
        side_classes = side.get("class", [])
        if "row-local" in side_classes:
            return eq_local
        if "row-visitante" in side_classes:
            return eq_visit

    prev_header = tabla.find_previous(["h2", "h3", "h4", "header"])
    if prev_header:
        htxt = limpiar_texto(prev_header.get_text()).lower()
        if eq_local.lower() in htxt and eq_visit.lower() not in htxt:
            return eq_local
        if eq_visit.lower() in htxt and eq_local.lower() not in htxt:
            return eq_visit

    return eq_local if idx_tabla % 2 == 0 else eq_visit


def parsear_partido(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    jornada = None
    for el in soup.select(".partido-header .fecha"):
        m = re.search(r"Jornada\s+(\d+)", limpiar_texto(el.get_text()), re.I)
        if m:
            jornada = int(m.group(1))
            break

    eq_local_el = soup.select_one(".partido-header .equipo.local .nombre")
    eq_visit_el = soup.select_one(".partido-header .equipo.visitante .nombre")
    eq_local = limpiar_texto(eq_local_el.get_text()) if eq_local_el else "Local"
    eq_visit = limpiar_texto(eq_visit_el.get_text()) if eq_visit_el else "Visitante"

    goles_local_el = soup.select_one(".partido-header .local.score")
    goles_visit_el = soup.select_one(".partido-header .visitante.score")
    goles_local = parsear_numero(goles_local_el.get_text()) if goles_local_el else None
    goles_visit = parsear_numero(goles_visit_el.get_text()) if goles_visit_el else None

    gf_l = int(goles_local) if goles_local is not None else 0
    gf_v = int(goles_visit) if goles_visit is not None else 0
    marcador = f"{gf_l}-{gf_v}" if (goles_local is not None and goles_visit is not None) else None

    entrenadores_nombres = [limpiar_texto(el.get_text()) for el in soup.select(".entrenador-widget .nombre-entrenador")]
    entrenadores = {}
    if marcador is not None:
        if len(entrenadores_nombres) >= 1 and entrenadores_nombres[0]:
            entrenadores[entrenadores_nombres[0]] = {
                "equipo": eq_local,
                "resultado": marcador,
                "puntos": puntos_coach_por_goles(gf_l, gf_v),
            }
        if len(entrenadores_nombres) >= 2 and entrenadores_nombres[1]:
            entrenadores[entrenadores_nombres[1]] = {
                "equipo": eq_visit,
                "resultado": marcador,
                "puntos": puntos_coach_por_goles(gf_v, gf_l),
            }

    jugadores = {}
    jugadores_con_participacion = 0
    jugadores_con_relevo = 0

    for idx_tabla, tabla in enumerate(soup.select("table.tablestats")):
        equipo_tabla = detectar_equipo_tabla(tabla, eq_local, eq_visit, idx_tabla)
        filas = tabla.select("tbody > tr")
        i = 0

        while i < len(filas):
            tr = filas[i]
            if "plegable" not in tr.get("class", []):
                i += 1
                continue

            tr_desglose = (
                filas[i + 1]
                if (i + 1 < len(filas) and "desglose" in filas[i + 1].get("class", []))
                else None
            )

            td_name = tr.select_one("td.name")
            nombre_corto = ""
            posicion = None
            if td_name:
                posicion = td_name.get("data-posicion-laliga-fantasy")
                copia_name = BeautifulSoup(str(td_name), "html.parser")
                for tag in copia_name.select("i, img, span.minuto"):
                    tag.decompose()
                nombre_corto = limpiar_texto(copia_name.get_text(" ", strip=True))
                nombre_corto = re.sub(r"\s+\d+'(?:\s+\d+')*$", "", nombre_corto).strip()

            td_relevo = tr.select_one("td.relevo")
            val_relevo = parsear_numero(td_relevo.get_text()) if td_relevo else None

            pts_span = tr.select_one("td.data span.point.laliga-fantasy")
            puntos_lf = parsear_numero(pts_span.get_text()) if pts_span else None

            desglose_lineas = []
            nombre_completo = None
            pts_dazn_desglose = None
            if tr_desglose:
                desg_lf = tr_desglose.select_one("div.desg.laliga-fantasy")
                desglose_lineas, nombre_completo, pts_dazn_desglose = extraer_desglose_lf(desg_lf)

            jugo = puntos_lf is not None
            relevo_val = val_relevo if val_relevo is not None else pts_dazn_desglose

            if jugo:
                jugadores_con_participacion += 1
                if relevo_val is not None:
                    jugadores_con_relevo += 1

            clave = nombre_completo or nombre_corto
            if clave:
                existente = jugadores.get(clave)
                nuevo_item = {
                    "nombre": nombre_corto or clave,
                    "nombre_completo": nombre_completo or nombre_corto,
                    "equipo": equipo_tabla,
                    "posicion": posicion,
                    "puntos": puntos_lf if puntos_lf is not None else 0,
                    "jugo": jugo,
                    "relevo": relevo_val,
                    "desglose": desglose_lineas,
                }
                # Conserva el registro que tenga participación/desglose si aparece más de una fila
                if not existente or (not existente.get("jugo") and jugo) or (len(desglose_lineas) > len(existente.get("desglose") or [])):
                    jugadores[clave] = nuevo_item

            i += 2 if tr_desglose else 1

    relevo_listo = (
        jugadores_con_participacion > 0
        and jugadores_con_relevo == jugadores_con_participacion
    )

    return {
        "url": url,
        "jornada": jornada,
        "local": eq_local,
        "visitante": eq_visit,
        "marcador": marcador,
        "partido": f"{eq_local} {marcador or 'vs'} {eq_visit}",
        "relevo_listo": relevo_listo,
        "jugadores": jugadores,
        "entrenadores": entrenadores,
    }


def enviar_telegram(mensaje: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(
        api_url,
        json={"chat_id": chat_id, "text": mensaje, "parse_mode": "HTML"},
        timeout=15,
    )


def main():
    estado_previo = {}
    if ESTADO_FILE.exists():
        estado_previo = json.loads(ESTADO_FILE.read_text(encoding="utf-8"))

    jornada_objetivo = obtener_jornada_objetivo()
    urls = descubrir_urls_partidos(jornada_objetivo)

    salida = {
        "fuente": "futbolfantasy",
        "jornada": jornada_objetivo,
        "partidos": [],
        "jugadores": {},
        "entrenadores": {},
    }
    nuevo_estado = dict(estado_previo)

    for url in urls:
        try:
            datos = parsear_partido(url)
        except Exception as exc:
            print(f"[AVISO] Error parseando {url}: {exc}")
            continue

        if datos["jornada"] and salida["jornada"] is None:
            salida["jornada"] = datos["jornada"]

        salida["partidos"].append({
            "partido": datos["partido"],
            "local": datos["local"],
            "visitante": datos["visitante"],
            "marcador": datos["marcador"],
            "url": url,
            "relevo_listo": datos["relevo_listo"],
        })
        salida["jugadores"].update(datos["jugadores"])
        salida["entrenadores"].update(datos["entrenadores"])

        era_listo = bool(estado_previo.get(url, {}).get("relevo_listo", False))
        ahora_listo = bool(datos["relevo_listo"])
        nuevo_estado[url] = {"relevo_listo": ahora_listo}

        if ahora_listo and not era_listo:
            msg = (
                f"✅ <b>Puntos definitivos (DAZN/Relevo)</b>\n"
                f"Partido: <b>{datos['partido']}</b>\n"
                f"Ya están cargados todos los puntos definitivos en FútbolFantasy."
            )
            enviar_telegram(msg)

    SALIDA_FILE.write_text(
        json.dumps(salida, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ESTADO_FILE.write_text(
        json.dumps(nuevo_estado, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()