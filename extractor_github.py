import requests
from bs4 import BeautifulSoup
import json
import re
import os

TEMPORADA = "26"
JORNADA_DEFECTO = "13"
URL_FALLBACK = "https://www.comuniate.com/partido/26/2/13/alaves-deportivo"

URL_AJAX = "https://www.comuniate.com/ajax/partidos/carga_puntos_relevo.php"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.comuniate.com/"
}

MAPA_EQUIPOS = {
    "athletic club": "Athletic",
    "atletico": "Atleti",
    "atlético": "Atleti",
    "atletico de madrid": "Atleti",
    "atlético de madrid": "Atleti",
    "rayo vallecano": "Rayo",
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

def obtener_jornada_configurada():
    if os.path.exists("config_jornada.json"):
        try:
            with open("config_jornada.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                j = str(data.get("jornada", "")).strip()
                if j.isdigit():
                    return j
        except Exception as e:
            print(f"Aviso leyendo config_jornada.json: {e}")
    return JORNADA_DEFECTO

def normalizar_equipo(nombre):
    clave = nombre.strip().lower()
    return MAPA_EQUIPOS.get(clave, nombre.strip())

def extraer_id_sofascore(html):
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

def buscar_enlaces_jornada(soup, temporada, jornada):
    partidos = []
    urls_vistas = set()
    patron_url = re.compile(rf"/partido/{temporada}/\d+/{jornada}/[a-z0-9-]+", re.IGNORECASE)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if patron_url.search(href):
            url_completa = href if href.startswith("http") else "https://www.comuniate.com" + href
            if url_completa in urls_vistas:
                continue
            texto = a.get_text(" ", strip=True)
            if " - " in texto:
                partes = texto.split(" - ", 1)
                urls_vistas.add(url_completa)
                partidos.append({
                    "url": url_completa,
                    "local": normalizar_equipo(partes[0]),
                    "visitante": normalizar_equipo(partes[1]),
                    "id_sofascore": None
                })
    return partidos

def descubrir_partidos_jornada(jornada):
    urls_exploracion = [
        f"https://www.comuniate.com/jornada/{TEMPORADA}/2/{jornada}",
        f"https://www.comuniate.com/jornada/{TEMPORADA}/1/{jornada}",
        "https://www.comuniate.com/partidos",
        "https://www.comuniate.com/",
        URL_FALLBACK
    ]

    partidos = []
    for url_idx in urls_exploracion:
        try:
            r = requests.get(url_idx, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            encontrados = buscar_enlaces_jornada(soup, TEMPORADA, jornada)
            if encontrados:
                partidos = encontrados
                break
        except Exception as e:
            print(f"Error explorando {url_idx}: {e}")

    for p in partidos:
        try:
            resp = requests.get(p["url"], headers=HEADERS, timeout=15)
            p["id_sofascore"] = extraer_id_sofascore(resp.text)
        except Exception:
            pass

    return partidos

def contar_goles_columna(columna):
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

def ejecutar_una_vez():
    jornada_actual = obtener_jornada_configurada()
    print(f"Extrayendo datos de Temporada {TEMPORADA}, Jornada {jornada_actual}...")
    partidos = descubrir_partidos_jornada(jornada_actual)
    jugadores = {}
    jugadores_equipos = {}
    entrenadores = {}
    partidos_limpios = []

    for p in partidos:
        partidos_limpios.append({
            "local": p["local"],
            "visitante": p["visitante"]
        })

        if not p["id_sofascore"]:
            continue

        payload = {
            "id_sofascore": p["id_sofascore"],
            "temporada": TEMPORADA,
            "login": ""
        }
        try:
            r = requests.post(URL_AJAX, data=payload, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            filas = soup.select(".playerRow")
            if not filas:
                continue

            columnas = soup.select("div.col-md-6.col-xs-6")
            for col_idx, fila in enumerate(filas):
                nombre_el = fila.select_one(".playerName")
                puntos_el = fila.select_one(".playerPoints")
                if nombre_el and puntos_el:
                    nombre = nombre_el.get_text(strip=True)
                    try:
                        puntos = int(puntos_el.get_text(strip=True))
                    except ValueError:
                        puntos = 0
                    jugadores[nombre] = puntos

                    # Intentar detectar equipo de la fila o de la columna local/visitante
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

            columnas = soup.select("div.col-md-6.col-xs-6")
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

    salida = {
        "jornada": jornada_actual,
        "jugadores": jugadores,
        "jugadores_equipos": jugadores_equipos,
        "entrenadores": entrenadores,
        "partidos": partidos_limpios
    }

    with open("puntos.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(f"OK (J{jornada_actual}): {len(jugadores)} jugadores, {len(entrenadores)} entrenadores, {len(partidos_limpios)} partidos.")

if __name__ == "__main__":
    ejecutar_una_vez()
