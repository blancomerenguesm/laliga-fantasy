import json
import os
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import requests

TEMPORADA = "26"
JORNADA_DEFECTO = "14"
MAX_PARTIDOS_JORNADA = 10

URL_AJAX = "https://www.comuniate.com/ajax/partidos/carga_puntos_relevo.php"
URL_DESGLOSE = "https://www.comuniate.com/ajax/partidos/estadistica_jugador_partido_relevo.php"
CONFIG_JORNADA_FILE = "config_jornada.json"
SALIDA_FILE = "puntos.json"
TELEGRAM_STATE_FILE = "telegram_notificados.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.comuniate.com/",
}

MAPA_EQUIPOS = {
    "athletic club": "Athletic",
    "athletic": "Athletic",
    "atletico": "Atleti",
    "atlético": "Atleti",
    "atletico de madrid": "Atleti",
    "atlético de madrid": "Atleti",
    "atletico madrid": "Atleti",
    "rayo vallecano": "Rayo",
    "rayo": "Rayo",
    "alaves": "Alavés",
    "alavés": "Alavés",
    "barcelona": "Barcelona",
    "betis": "Betis",
    "real betis": "Betis",
    "celta": "Celta",
    "celta de vigo": "Celta",
    "deportivo": "Deportivo",
    "elche": "Elche",
    "espanyol": "Espanyol",
    "getafe": "Getafe",
    "levante": "Levante",
    "malaga": "Málaga",
    "málaga": "Málaga",
    "osasuna": "Osasuna",
    "racing": "Racing",
    "real madrid": "Real Madrid",
    "real sociedad": "Real Sociedad",
    "sevilla": "Sevilla",
    "valencia": "Valencia",
    "villarreal": "Villarreal",
}


def limpiar_texto(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "")).strip()


def normalizar_clave(txt: str) -> str:
    s = limpiar_texto(txt).lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def obtener_jornada_configurada() -> str:
    if os.path.exists(CONFIG_JORNADA_FILE):
        try:
            with open(CONFIG_JORNADA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                j = str(data.get("jornada", "")).strip()
                if j.isdigit():
                    return j
        except Exception as e:
            print(f"Aviso leyendo config_jornada.json: {e}")
    return JORNADA_DEFECTO


def cargar_estado_previo_salida(jornada_actual: str) -> tuple[dict, dict]:
    if not os.path.exists(SALIDA_FILE):
        return {}, {}
    try:
        with open(SALIDA_FILE, "r", encoding="utf-8") as f:
            prev = json.load(f)
        if str(prev.get("jornada", "")) != str(jornada_actual):
            return {}, {}
        jugadores_prev = {
            normalizar_clave(k): v
            for k, v in (prev.get("jugadores") or {}).items()
        }
        desgloses_prev = prev.get("desgloses") or {}
        return jugadores_prev, desgloses_prev
    except Exception:
        return {}, {}


def desglose_tiene_dazn(lineas: list[dict] | None) -> bool:
    if not isinstance(lineas, list) or not lineas:
        return False
    for it in lineas:
        if not isinstance(it, dict):
            continue
        if it.get("dazn"):
            return True
        if re.search(r"dazn|relevo", str(it.get("concepto") or ""), re.IGNORECASE):
            return True
    return False


def cargar_notificados_telegram() -> dict:
    if not os.path.exists(TELEGRAM_STATE_FILE):
        return {"enviados": []}
    try:
        with open(TELEGRAM_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("enviados"), list):
            return {"enviados": [str(x) for x in data["enviados"]]}
    except Exception as e:
        print(f"Aviso leyendo {TELEGRAM_STATE_FILE}: {e}")
    return {"enviados": []}


def guardar_notificados_telegram(estado: dict) -> None:
    try:
        unicos = sorted(set(str(x) for x in (estado.get("enviados") or [])))
        with open(TELEGRAM_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"enviados": unicos}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Aviso guardando {TELEGRAM_STATE_FILE}: {e}")


def enviar_mensaje_telegram(session: requests.Session, texto: str) -> bool:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        print("Telegram omitido: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
    }
    try:
        resp = session.post(url, data=payload, timeout=(4, 8))
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            print(f"Aviso Telegram API no ok: {data}")
            return False
        return True
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")
        return False


def procesar_notificaciones_definitivas(
    session: requests.Session,
    jornada_actual: str,
    jugadores: dict,
    jugadores_equipos: dict,
    desgloses: dict,
) -> None:
    estado = cargar_notificados_telegram()
    enviados_set = set(estado.get("enviados") or [])
    nuevos_avisos = []

    desgloses_por_clave = {}
    for k, v in (desgloses or {}).items():
        nk = normalizar_clave(k)
        if nk:
            desgloses_por_clave[nk] = v

    for nombre, puntos in jugadores.items():
        nk = normalizar_clave(nombre)
        if not nk:
            continue
        lineas = desgloses_por_clave.get(nk)
        if not desglose_tiene_dazn(lineas):
            continue

        clave_unica = f"J{jornada_actual}:{nk}"
        if clave_unica in enviados_set:
            continue

        equipo = jugadores_equipos.get(nombre) or ""
        nuevos_avisos.append({
            "clave_unica": clave_unica,
            "nombre": nombre,
            "equipo": equipo,
            "puntos": puntos,
        })

    if not nuevos_avisos:
        print("Telegram: sin nuevos puntos definitivos (DAZN) que notificar.")
        return

    lineas_msg = [f"✅ Puntos definitivos (DAZN) · Jornada {jornada_actual}:"]
    for item in nuevos_avisos:
        eq_txt = f" ({item['equipo']})" if item["equipo"] else ""
        lineas_msg.append(f"• {item['nombre']}{eq_txt}: {item['puntos']} pts")

    mensaje = "\n".join(lineas_msg)
    if len(mensaje) > 3900:
        mensaje = mensaje[:3880] + "\n• ... (más jugadores definitivos)"

    if enviar_mensaje_telegram(session, mensaje):
        for item in nuevos_avisos:
            enviados_set.add(item["clave_unica"])
        estado["enviados"] = sorted(enviados_set)
        guardar_notificados_telegram(estado)
        print(f"Telegram: notificados {len(nuevos_avisos)} jugadores definitivos.")


def normalizar_equipo(nombre: str) -> str:
    clave = limpiar_texto(nombre).lower().replace("-", " ")
    clave = re.sub(r"\s+", " ", clave).strip()
    return MAPA_EQUIPOS.get(clave, limpiar_texto(nombre).replace("-", " ").title())


def parsear_equipos_desde_url(url: str) -> tuple[str, str]:
    m = re.search(r"/partido/\d+/\d+/\d+/([a-z0-9-]+)", url, re.IGNORECASE)
    if not m:
        return "Local", "Visitante"
    slug = m.group(1).lower().strip("-")
    claves_ordenadas = sorted(MAPA_EQUIPOS.keys(), key=len, reverse=True)
    for c in claves_ordenadas:
        slug_c = c.replace(" ", "-")
        if slug.startswith(slug_c + "-"):
            resto = slug[len(slug_c) + 1 :]
            if resto in MAPA_EQUIPOS:
                return normalizar_equipo(c), normalizar_equipo(resto)
    if "-" in slug:
        partes = slug.split("-", 1)
        return normalizar_equipo(partes[0]), normalizar_equipo(partes[1])
    return "Local", "Visitante"


def parsear_equipos_desde_html(html: str, url_final: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    titulo = limpiar_texto(soup.title.get_text()) if soup.title else ""
    candidatos = [titulo]
    h1 = soup.find("h1")
    if h1:
        candidatos.append(limpiar_texto(h1.get_text()))

    for txt in candidatos:
        m = re.search(
            r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ .'-]+?)\s+(?:\d+\s*-\s*\d+|vs\.?|[-–])\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ .'-]+?)(?:\s*[|–-]\s*Comuniate|$)",
            txt,
        )
        if m:
            loc_raw = re.sub(r"\s+\d+$", "", m.group(1)).strip()
            vis_raw = re.sub(r"^\d+\s+", "", m.group(2)).strip()
            loc_norm = normalizar_equipo(loc_raw)
            vis_norm = normalizar_equipo(vis_raw)
            if loc_norm.lower() in MAPA_EQUIPOS and vis_norm.lower() in MAPA_EQUIPOS:
                return loc_norm, vis_norm

    return parsear_equipos_desde_url(url_final)


def extraer_id_sofascore(html: str):
    patrones = [
        r"id_sofascore['\"]?\s*[:=]\s*['\"]?(\d+)",
        r'name=["\']id_sofascore["\'][^>]*value=["\'](\d+)["\']',
        r'value=["\'](\d+)["\'][^>]*name=["\']id_sofascore["\']',
        r"carga_puntos_relevo\.php[^;]{0,200}?(\d{7,9})",
    ]
    for patron in patrones:
        m = re.search(patron, html, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    return None


def extraer_id_jugador_fila(fila) -> str | None:
    for attr in ("data-id-jugador", "data-id_jugador", "data-jugador", "data-id"):
        val = fila.get(attr)
        if val and str(val).strip().isdigit():
            return str(val).strip()

    for tag in fila.select("[onclick], [data-id-jugador], a[href]"):
        onclick = tag.get("onclick") or ""
        m_ver = re.search(r"ver_estadistica\(\s*['\"](\d+)['\"]", onclick, re.IGNORECASE)
        if m_ver:
            return m_ver.group(1)

        m = re.search(
            r"id_jugador['\"]?\s*[:=]\s*['\"]?(\d+)|estadistica_jugador_partido_relevo[^0-9]*(\d+)",
            onclick,
            re.IGNORECASE,
        )
        if m:
            return m.group(1) or m.group(2)

        for a_attr in ("data-id-jugador", "data-id"):
            v = tag.get(a_attr)
            if v and str(v).strip().isdigit():
                return str(v).strip()

        href = tag.get("href") or ""
        m_href = re.search(r"/jugador(?:es)?/[^/]+-(\d+)", href)
        if m_href:
            return m_href.group(1)

    return None


def buscar_enlaces_jornada(soup, temporada: str, jornada: str) -> list[dict]:
    partidos = []
    urls_vistas = set()
    patron_url = re.compile(rf"/partido/{temporada}/\d+/{jornada}/[a-z0-9-]+", re.IGNORECASE)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if patron_url.search(href):
            url_completa = href if href.startswith("http") else "https://www.comuniate.com" + href
            if url_completa in urls_vistas:
                continue
            urls_vistas.add(url_completa)

            texto = a.get_text(" ", strip=True)
            if " - " in texto:
                partes = texto.split(" - ", 1)
                local = normalizar_equipo(partes[0])
                visitante = normalizar_equipo(partes[1])
            else:
                local, visitante = parsear_equipos_desde_url(url_completa)

            partidos.append({
                "url": url_completa,
                "local": local,
                "visitante": visitante,
                "id_sofascore": None,
            })
    return partidos


def extraer_slot_partido(url: str) -> int | None:
    m = re.search(r"/partido/\d+/(\d+)/\d+", url)
    return int(m.group(1)) if m else None


def descubrir_partidos_jornada(session: requests.Session, jornada: str) -> list[dict]:
    urls_exploracion = [
        f"https://www.comuniate.com/jornada/{TEMPORADA}/2/{jornada}",
        f"https://www.comuniate.com/jornada/{TEMPORADA}/1/{jornada}",
        "https://www.comuniate.com/partidos",
        "https://www.comuniate.com/",
    ]

    partidos_por_slot = {}
    for url_idx in urls_exploracion:
        try:
            r = session.get(url_idx, timeout=(4, 8))
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            encontrados = buscar_enlaces_jornada(soup, TEMPORADA, jornada)
            for p in encontrados:
                slot = extraer_slot_partido(p["url"])
                if slot is not None and slot not in partidos_por_slot:
                    partidos_por_slot[slot] = p
            if len(partidos_por_slot) >= MAX_PARTIDOS_JORNADA:
                break
        except Exception as e:
            print(f"Error explorando {url_idx}: {e}")

    for slot in range(1, MAX_PARTIDOS_JORNADA + 1):
        if slot in partidos_por_slot:
            continue
        url_slot = f"https://www.comuniate.com/partido/{TEMPORADA}/{slot}/{jornada}"
        try:
            resp = session.get(url_slot, timeout=(4, 8), allow_redirects=True)
            if resp.status_code != 200:
                continue
            url_final = resp.url or url_slot
            if not re.search(rf"/partido/{TEMPORADA}/\d+/{jornada}/", url_final):
                continue
            id_sofa = extraer_id_sofascore(resp.text)
            if not id_sofa:
                continue
            local, visitante = parsear_equipos_desde_html(resp.text, url_final)
            partidos_por_slot[slot] = {
                "url": url_final,
                "local": local,
                "visitante": visitante,
                "id_sofascore": id_sofa,
            }
        except Exception as e:
            print(f"Error en slot {slot} ({url_slot}): {e}")

    partidos = [partidos_por_slot[s] for s in sorted(partidos_por_slot.keys())]
    for p in partidos:
        if p.get("id_sofascore"):
            continue
        try:
            resp = session.get(p["url"], timeout=(4, 8))
            p["id_sofascore"] = extraer_id_sofascore(resp.text)
            if p["local"] == "Local" or p["visitante"] == "Visitante":
                p["local"], p["visitante"] = parsear_equipos_desde_html(resp.text, resp.url or p["url"])
        except Exception:
            pass

    return partidos


def contar_goles_columna(columna) -> tuple[int, int]:
    goles_favor = 0
    goles_propia = 0
    for hl in columna.select(".playerHighlights .hl"):
        clases = hl.get("class", [])
        icono = hl.select_one("i")
        if not icono:
            continue
        icono_clases = icono.get("class", [])

        if "c-success" in clases and ("fa-circle" in icono_clases or "fa-arrow-circle-up" in icono_clases):
            goles_favor += 1
        elif "c-danger" in clases and "fa-circle" in icono_clases:
            goles_propia += 1

    return goles_favor, goles_propia


def consultar_desglose_jugador(session: requests.Session, id_jugador: str, jornada: str, temporada: str) -> list[dict]:
    payload = {
        "id_jugador": id_jugador,
        "jornada": jornada,
        "temporada": temporada,
    }
    resp = session.post(URL_DESGLOSE, data=payload, timeout=(4, 8))
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    for row in soup.select("div.row"):
        label_el = row.select_one("strong")
        val_el = row.select_one("[class*='col-xs-4']")
        if not label_el or not val_el:
            continue
        concepto = limpiar_texto(label_el.get_text())
        puntos_txt = limpiar_texto(val_el.get_text())
        if concepto and puntos_txt:
            es_dazn = bool(row.select_one("img[src*='dazn']")) or ("dazn" in concepto.lower())
            items.append({
                "concepto": concepto,
                "puntos": puntos_txt,
                "dazn": es_dazn,
            })
    return items


def ejecutar_una_vez():
    jornada_actual = obtener_jornada_configurada()
    print(f"Extrayendo datos de Temporada {TEMPORADA}, Jornada {jornada_actual}...")

    jugadores_prev, desgloses_prev = cargar_estado_previo_salida(jornada_actual)

    session = requests.Session()
    session.headers.update(HEADERS)

    partidos = descubrir_partidos_jornada(session, jornada_actual)
    jugadores = {}
    jugadores_equipos = {}
    entrenadores = {}
    desgloses = {}
    partidos_limpios = []
    ids_consultados = set()
    reutilizados = 0
    consultados_red = 0

    for p in partidos:
        partidos_limpios.append({
            "local": p["local"],
            "visitante": p["visitante"],
        })

        if not p["id_sofascore"]:
            continue

        payload = {
            "id_sofascore": p["id_sofascore"],
            "temporada": TEMPORADA,
            "login": "",
        }
        try:
            r = session.post(URL_AJAX, data=payload, timeout=(4, 8))
            soup = BeautifulSoup(r.text, "html.parser")
            filas = soup.select(".playerRow")
            if not filas:
                continue

            columnas = soup.select("div.col-md-6.col-xs-6")
            for fila in filas:
                nombre_el = fila.select_one(".playerName")
                puntos_el = fila.select_one(".playerPoints")
                if nombre_el and puntos_el:
                    nombre = limpiar_texto(nombre_el.get_text())
                    try:
                        puntos = int(limpiar_texto(puntos_el.get_text()))
                    except ValueError:
                        puntos = 0
                    jugadores[nombre] = puntos

                    equipo_detectado = None
                    eq_el = fila.select_one(".playerTeam, .teamName, [data-team]")
                    if eq_el:
                        raw_eq = eq_el.get("data-team") or eq_el.get_text(strip=True)
                        if raw_eq:
                            equipo_detectado = normalizar_equipo(raw_eq)
                    if not equipo_detectado and len(columnas) >= 2:
                        if fila in columnas[0].select(".playerRow"):
                            equipo_detectado = p["local"]
                        elif fila in columnas[1].select(".playerRow"):
                            equipo_detectado = p["visitante"]
                    if equipo_detectado:
                        jugadores_equipos[nombre] = equipo_detectado

                    norm_nombre = normalizar_clave(nombre)
                    prev_desglose = desgloses_prev.get(norm_nombre)
                    prev_pts = jugadores_prev.get(norm_nombre)

                    if prev_desglose and prev_pts == puntos and desglose_tiene_dazn(prev_desglose):
                        desgloses[norm_nombre] = prev_desglose
                        reutilizados += 1
                        continue

                    id_jug = extraer_id_jugador_fila(fila)
                    if id_jug and id_jug not in ids_consultados:
                        try:
                            lineas = consultar_desglose_jugador(session, id_jug, jornada_actual, TEMPORADA)
                            if lineas:
                                desgloses[norm_nombre] = lineas
                            ids_consultados.add(id_jug)
                            consultados_red += 1
                        except Exception as exc_d:
                            print(f"Aviso desglose {nombre} ({id_jug}): {exc_d}")

            if len(columnas) >= 2:
                gf_loc, gp_loc = contar_goles_columna(columnas[0])
                gf_vis, gp_vis = contar_goles_columna(columnas[1])
                goles_local = gf_loc + gp_vis
                goles_visitante = gf_vis + gp_loc

                if goles_local > goles_visitante:
                    entrenadores[p["local"]] = 5
                    entrenadores[p["visitante"]] = 0
                elif goles_local < goles_visitante:
                    entrenadores[p["local"]] = 0
                    entrenadores[p["visitante"]] = 5
                else:
                    entrenadores[p["local"]] = 2
                    entrenadores[p["visitante"]] = 2
        except Exception as e:
            print(f"Error en {p['local']}-{p['visitante']}: {e}")

    ahora_es = datetime.now(ZoneInfo("Europe/Madrid")).strftime("%d/%m %H:%M:%S")

    salida = {
        "jornada": jornada_actual,
        "actualizado_en": ahora_es,
        "jugadores": jugadores,
        "jugadores_equipos": jugadores_equipos,
        "entrenadores": entrenadores,
        "desgloses": desgloses,
        "partidos": partidos_limpios,
    }

    with open(SALIDA_FILE, "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    procesar_notificaciones_definitivas(
        session=session,
        jornada_actual=jornada_actual,
        jugadores=jugadores,
        jugadores_equipos=jugadores_equipos,
        desgloses=desgloses,
    )

    print(
        f"OK (J{jornada_actual}): {len(jugadores)} jugadores, "
        f"{len(desgloses)} desgloses ({consultados_red} red, {reutilizados} caché), "
        f"{len(entrenadores)} entrenadores, {len(partidos_limpios)} partidos."
    )


if __name__ == "__main__":
    ejecutar_una_vez()
