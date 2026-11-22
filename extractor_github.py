import requests
from bs4 import BeautifulSoup
import json
import re

TEMPORADA = "26"
JORNADA = "13"
URL_SEMILLA = "https://www.comuniate.com/partido/26/2/13/alaves-deportivo"

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
    "celta": "Celta",
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

def descubrir_partidos_jornada():
    partidos = []
    urls_vistas = set()

    try:
        r = requests.get(URL_SEMILLA, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        patron_url = re.compile(rf"/partido/{TEMPORADA}/\d+/{JORNADA}/[a-z0-9-]+", re.IGNORECASE)

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
    except Exception as e:
        print(f"Error descubriendo partidos: {e}")

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
    partidos = descubrir_partidos_jornada()
    jugadores = {}
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

            for fila in filas:
                nombre_el = fila.select_one(".playerName")
                puntos_el = fila.select_one(".playerPoints")
                if nombre_el and puntos_el:
                    nombre = nombre_el.get_text(strip=True)
                    try:
                        puntos = int(puntos_el.get_text(strip=True))
                    except ValueError:
                        puntos = 0
                    jugadores[nombre] = puntos

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
        "jugadores": jugadores,
        "entrenadores": entrenadores,
        "partidos": partidos_limpios
    }

    with open("puntos.json", "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(jugadores)} jugadores, {len(entrenadores)} entrenadores, {len(partidos_limpios)} partidos.")

if __name__ == "__main__":
    ejecutar_una_vez()
