import json
import os
import re
from pathlib import Path
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

# URLs de partidos de la jornada en curso en FútbolFantasy
# (puedes rellenarlas desde calendario/portada o pasarlas vía env/archivo)
PARTIDOS_URLS = [
    "https://www.futbolfantasy.com/partidos/22547-levante-elche",
]

ESTADO_FILE = Path("estado_relevo.json")
SALIDA_FILE = Path("puntos.json")


def limpiar_texto(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "")).strip()


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


def extraer_desglose_lf(desg_lf) -> tuple[list[str], str | None, int | None]:
    """Devuelve (lineas_desglose, nombre_completo, puntos_dazn_relevo)."""
    if not desg_lf:
        return [], None, None

    nombre_completo = None
    ficha_a = desg_lf.select_one("a.link[href*='/jugadores/']")
    if ficha_a:
        m = re.search(r"/jugadores/([^/]+)/", ficha_a.get("href", ""))
        if m:
            nombre_completo = m.group(1).replace("-", " ").title()

    lineas = []
    puntos_dazn = None

    # Recorre hijos directos para capturar también texto previo a un <div class="estadistica">
    texto_acumulado_previo = ""
    for child in desg_lf.children:
        if isinstance(child, str):
            t = limpiar_texto(child)
            if t:
                texto_acumulado_previo = f"{texto_acumulado_previo} {t}".strip()
            continue

        if getattr(child, "name", None) == "div" and "estadistica" in child.get("class", []):
            # Clonar/leer texto y reemplazar el icono de flecha por '->'
            copia = BeautifulSoup(str(child), "html.parser")
            for icono in copia.select("i.fa-arrow-right"):
                icono.replace_with(" -> ")
            linea = limpiar_texto(copia.get_text(" ", strip=True))
            if texto_acumulado_previo and not re.search(r"\d+\s+Minutos|Balones|Posesiones|Goles|Tarjetas|Despejes|Regates|Asistencias|Puntos DAZN", linea, re.I):
                linea = f"{texto_acumulado_previo} {linea}".strip()
            texto_acumulado_previo = ""
            linea = re.sub(r"\s*->\s*", " -> ", linea)
            if linea:
                lineas.append(linea)

        elif getattr(child, "name", None) == "div" and "relevo-points" in child.get("class", []):
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


def parsear_partido(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Jornada
    jornada = None
    for el in soup.select(".partido-header .fecha"):
        m = re.search(r"Jornada\s+(\d+)", limpiar_texto(el.get_text()), re.I)
        if m:
            jornada = int(m.group(1))
            break

    # Equipos, marcador y entrenadores
    eq_local = limpiar_texto(soup.select_one(".partido-header .equipo.local .nombre").get_text()) if soup.select_one(".partido-header .equipo.local .nombre") else "Local"
    eq_visit = limpiar_texto(soup.select_one(".partido-header .equipo.visitante .nombre").get_text()) if soup.select_one(".partido-header .equipo.visitante .nombre") else "Visitante"

    goles_local = parsear_numero(soup.select_one(".partido-header .local.score").get_text()) if soup.select_one(".partido-header .local.score") else 0
    goles_visit = parsear_numero(soup.select_one(".partido-header .visitante.score").get_text()) if soup.select_one(".partido-header .visitante.score") else 0
    gf_l = int(goles_local or 0)
    gf_v = int(goles_visit or 0)

    entrenadores_nombres = [limpiar_texto(el.get_text()) for el in soup.select(".entrenador-widget .nombre-entrenador")]
    entrenadores = {}
    if len(entrenadores_nombres) >= 1 and entrenadores_nombres[0]:
        entrenadores[entrenadores_nombres[0]] = {
            "equipo": eq_local,
            "resultado": f"{gf_l}-{gf_v}",
            "puntos": puntos_coach_por_goles(gf_l, gf_v),
        }
    if len(entrenadores_nombres) >= 2 and entrenadores_nombres[1]:
        entrenadores[entrenadores_nombres[1]] = {
            "equipo": eq_visit,
            "resultado": f"{gf_v}-{gf_l}",
            "puntos": puntos_coach_por_goles(gf_v, gf_l),
        }

    jugadores = {}
    jugadores_con_participacion = 0
    jugadores_con_relevo = 0

    for tabla in soup.select("table.tablestats"):
        # Detectar equipo asociado a la tabla si es posible
        equipo_tabla = None
        side = tabla.find_parent(attrs={"data-match-p11-team": True})
        if side:
            team_id = side.get("data-match-p11-team")
            if team_id == "10":
                equipo_tabla = "Levante"
            elif team_id == "21":
                equipo_tabla = "Elche"

        filas = tabla.select("tbody > tr")
        i = 0
        while i < len(filas):
            tr = filas[i]
            if "plegable" not in tr.get("class", []):
                i += 1
                continue

            tr_desglose = filas[i + 1] if (i + 1 < len(filas) and "desglose" in filas[i + 1].get("class", [])) else None

            td_name = tr.select_one("td.name")
            nombre_corto = ""
            posicion = None
            if td_name:
                posicion = td_name.get("data-posicion-laliga-fantasy")
                # Clonar sin iconos ni minuto para sacar el nombre limpio
                copia_name = BeautifulSoup(str(td_name), "html.parser")
                for tag in copia_name.select("i, img, span.minuto"):
                    tag.decompose()
                nombre_corto = limpiar_texto(copia_name.get_text(" ", strip=True))

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

            if puntos_lf is not None:
                jugadores_con_participacion += 1
                if val_relevo is not None or pts_dazn_desglose is not None:
                    jugadores_con_relevo += 1

            clave = nombre_completo or nombre_corto
            if clave:
                jugadores[clave] = {
                    "nombre": nombre_corto,
                    "nombre_completo": nombre_completo or nombre_corto,
                    "equipo": equipo_tabla,
                    "posicion": posicion,
                    "puntos": puntos_lf if puntos_lf is not None else 0,
                    "jugo": puntos_lf is not None,
                    "relevo": val_relevo if val_relevo is not None else pts_dazn_desglose,
                    "desglose": desglose_lineas,
                }

            i += 2 if tr_desglose else 1

    relevo_listo = (
        jugadores_con_participacion > 0
        and jugadores_con_relevo == jugadores_con_participacion
    )

    return {
        "url": url,
        "jornada": jornada,
        "partido": f"{eq_local} {gf_l}-{gf_v} {eq_visit}",
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

    salida = {
        "fuente": "futbolfantasy",
        "jornada": None,
        "partidos": [],
        "jugadores": {},
        "entrenadores": {},
    }
    nuevo_estado = dict(estado_previo)

    for url in PARTIDOS_URLS:
        datos = parsear_partido(url)
        if datos["jornada"] and salida["jornada"] is None:
            salida["jornada"] = datos["jornada"]

        salida["partidos"].append({
            "partido": datos["partido"],
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