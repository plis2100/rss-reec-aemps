import hashlib
import html
import json
import re
import sys
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from playwright.sync_api import sync_playwright


PAGINA_INICIO = "https://reec.aemps.es/reec/public/web.html"
PAGINA_LISTADO = "https://reec.aemps.es/reec/public/list.html"

ARCHIVO_RSS = Path("feed.xml")
ARCHIVO_ESTADO = Path("estado.json")

MAXIMO_ESTUDIOS = 500


def limpiar_texto(texto):
    if not texto:
        return ""

    return re.sub(r"\s+", " ", str(texto)).strip()


def descargar_estudios():
    print("Abriendo el Registro Español de Estudios Clínicos...", flush=True)

    with sync_playwright() as playwright:
        navegador = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )

        contexto = navegador.new_context(
            locale="es-ES",
            timezone_id="Europe/Madrid",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
        )

        pagina = contexto.new_page()

        pagina.goto(
            PAGINA_INICIO,
            wait_until="domcontentloaded",
            timeout=90000,
        )

        pagina.wait_for_timeout(3000)

        boton_ultimos = pagina.get_by_text(
            "ir a últimos estudios autorizados",
            exact=False,
        )

        if boton_ultimos.count() == 0:
            navegador.close()
            raise RuntimeError(
                "No se encontró el botón de últimos estudios autorizados."
            )

        print("Solicitando los últimos estudios autorizados...", flush=True)

        boton_ultimos.first.click()

        pagina.wait_for_url(
            re.compile(r".*/public/list\.html.*"),
            timeout=60000,
        )

        pagina.wait_for_selector(
            "li[id^='est']",
            timeout=90000,
        )

        pagina.wait_for_timeout(5000)

        cantidad = pagina.locator("li[id^='est']").count()

        print(
            f"Resultados encontrados en la página: {cantidad}",
            flush=True,
        )

        if cantidad == 0:
            navegador.close()
            raise RuntimeError(
                "La AEMPS no ha devuelto estudios clínicos."
            )

        datos = pagina.locator("li[id^='est']").evaluate_all(
            """
            (elementos) => elementos.map((elemento) => {
                const normalizar = (texto) =>
                    (texto || "").replace(/\\s+/g, " ").trim();

                const textosTitulo = Array.from(
                    elemento.querySelectorAll("p.title")
                )
                .map((nodo) => normalizar(nodo.textContent))
                .filter((texto) => texto.length > 0);

                const titulosSinFecha = textosTitulo.filter(
                    (texto) =>
                        !texto.toLowerCase().startsWith(
                            "fecha autorización"
                        )
                );

                const fechaCompleta =
                    textosTitulo.find(
                        (texto) =>
                            texto.toLowerCase().startsWith(
                                "fecha autorización"
                            )
                    ) || "";

                const fechaCoincidencia = fechaCompleta.match(
                    /(\\d{1,2}\\/\\d{1,2}\\/\\d{4})/
                );

                const identificadorNodo =
                    elemento.querySelector("h6.identificador");

                let identificador = identificadorNodo
                    ? normalizar(identificadorNodo.textContent)
                    : "";

                const estadoNodo =
                    elemento.querySelector("p.desc");

                const fuenteNodo =
                    elemento.querySelector(".ctisTag");

                const idInterno = (elemento.id || "")
                    .replace(/^est/, "");

                return {
                    id_interno: idInterno,
                    titulo: titulosSinFecha[0] || "",
                    resumen: titulosSinFecha[1] || "",
                    enfermedad: titulosSinFecha[2] || "",
                    estado: estadoNodo
                        ? normalizar(estadoNodo.textContent)
                        : "",
                    fecha: fechaCoincidencia
                        ? fechaCoincidencia[1]
                        : "",
                    identificador: identificador,
                    fuente: fuenteNodo
                        ? normalizar(fuenteNodo.textContent)
                        : "REEC"
                };
            })
            """
        )

        navegador.close()

    return datos


def convertir_fecha(fecha_texto):
    coincidencia = re.fullmatch(
        r"(\d{1,2})/(\d{1,2})/(\d{4})",
        fecha_texto or "",
    )

    if not coincidencia:
        return datetime.now(timezone.utc)

    dia = int(coincidencia.group(1))
    mes = int(coincidencia.group(2))
    anio = int(coincidencia.group(3))

    try:
        return datetime(
            anio,
            mes,
            dia,
            9,
            0,
            tzinfo=timezone.utc,
        )
    except ValueError:
        return datetime.now(timezone.utc)


def preparar_estudios(datos):
    estudios = []
    identificadores_vistos = set()

    for dato in datos:
        titulo = limpiar_texto(dato.get("titulo"))
        resumen = limpiar_texto(dato.get("resumen"))
        enfermedad = limpiar_texto(dato.get("enfermedad"))
        estado = limpiar_texto(dato.get("estado"))
        fecha = limpiar_texto(dato.get("fecha"))
        identificador = limpiar_texto(dato.get("identificador"))
        fuente = limpiar_texto(dato.get("fuente")) or "REEC"
        id_interno = limpiar_texto(dato.get("id_interno"))

        if not titulo or not identificador:
            continue

        clave_original = (
            identificador
            or id_interno
            or f"{titulo}|{fecha}"
        )

        identificador_rss = hashlib.sha256(
            clave_original.encode("utf-8")
        ).hexdigest()

        if identificador_rss in identificadores_vistos:
            continue

        identificadores_vistos.add(identificador_rss)

        url = PAGINA_LISTADO

        if id_interno:
            url = f"{PAGINA_LISTADO}#est{id_interno}"

        descripcion = (
            f"<p><strong>Identificador:</strong> "
            f"{html.escape(identificador)}</p>"
            f"<p><strong>Fecha de autorización:</strong> "
            f"{html.escape(fecha)}</p>"
        )

        if resumen and resumen != titulo:
            descripcion += (
                f"<p><strong>Resumen:</strong> "
                f"{html.escape(resumen)}</p>"
            )

        if enfermedad:
            descripcion += (
                f"<p><strong>Enfermedad investigada:</strong> "
                f"{html.escape(enfermedad)}</p>"
            )

        if estado:
            descripcion += (
                f"<p><strong>Estado:</strong> "
                f"{html.escape(estado)}</p>"
            )

        descripcion += (
            f"<p><strong>Procedencia:</strong> "
            f"{html.escape(fuente)}</p>"
            f'<p><a href="{html.escape(url)}">'
            f"Consultar en el Registro Español de Estudios Clínicos"
            f"</a></p>"
        )

        titulo_rss = titulo

        if len(titulo_rss) > 250:
            titulo_rss = titulo_rss[:247] + "..."

        estudios.append(
            {
                "id": identificador_rss,
                "titulo": titulo_rss,
                "titulo_completo": titulo,
                "resumen": resumen,
                "enfermedad": enfermedad,
                "estado": estado,
                "fecha": fecha,
                "fecha_iso": convertir_fecha(fecha).isoformat(),
                "identificador": identificador,
                "fuente": fuente,
                "url": url,
                "descripcion": descripcion,
            }
        )

    estudios.sort(
        key=lambda estudio: estudio["fecha_iso"],
        reverse=True,
    )

    return estudios


def cargar_estado():
    if not ARCHIVO_ESTADO.exists():
        return []

    try:
        contenido = json.loads(
            ARCHIVO_ESTADO.read_text(encoding="utf-8")
        )

        if isinstance(contenido, dict):
            contenido = contenido.get("estudios", [])

        if isinstance(contenido, list):
            return contenido

    except Exception as error:
        print(
            f"No se pudo leer estado.json: {error}",
            flush=True,
        )

    return []


def combinar_estudios(nuevos, anteriores):
    resultado = []
    identificadores = set()

    for estudio in nuevos + anteriores:
        identificador = estudio.get("id")

        if not identificador:
            continue

        if identificador in identificadores:
            continue

        identificadores.add(identificador)
        resultado.append(estudio)

    resultado.sort(
        key=lambda estudio: estudio.get("fecha_iso", ""),
        reverse=True,
    )

    return resultado[:MAXIMO_ESTUDIOS]


def guardar_estado(estudios):
    contenido = {
        "actualizado": datetime.now(timezone.utc).isoformat(),
        "cantidad": len(estudios),
        "estudios": estudios,
    }

    ARCHIVO_ESTADO.write_text(
        json.dumps(
            contenido,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("estado.json guardado correctamente.", flush=True)


def crear_rss(estudios):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
            "xmlns:atom": "http://www.w3.org/2005/Atom",
            "xmlns:content": "http://purl.org/rss/1.0/modules/content/",
        },
    )

    canal = ET.SubElement(rss, "channel")

    ET.SubElement(canal, "title").text = (
        "Últimos estudios clínicos autorizados – REEC AEMPS"
    )

    ET.SubElement(canal, "link").text = PAGINA_LISTADO

    ET.SubElement(canal, "description").text = (
        "Últimos estudios clínicos autorizados publicados en el "
        "Registro Español de Estudios Clínicos de la AEMPS."
    )

    ET.SubElement(canal, "language").text = "es-es"

    ET.SubElement(canal, "generator").text = (
        "GitHub Actions - plis2100"
    )

    ET.SubElement(canal, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc)
    )

    ET.SubElement(canal, "ttl").text = "60"

    enlace_atom = ET.SubElement(canal, "atom:link")

    enlace_atom.set(
        "href",
        "https://raw.githubusercontent.com/"
        "plis2100/rss-reec-aemps/main/feed.xml",
    )

    enlace_atom.set("rel", "self")
    enlace_atom.set("type", "application/rss+xml")

    for estudio in estudios:
        item = ET.SubElement(canal, "item")

        ET.SubElement(item, "title").text = estudio["titulo"]
        ET.SubElement(item, "link").text = estudio["url"]

        guid = ET.SubElement(item, "guid")
        guid.set("isPermaLink", "false")
        guid.text = estudio["id"]

        fecha = datetime.fromisoformat(estudio["fecha_iso"])

        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)

        ET.SubElement(item, "pubDate").text = format_datetime(fecha)

        ET.SubElement(item, "description").text = estudio[
            "descripcion"
        ]

        contenido = ET.SubElement(item, "content:encoded")
        contenido.text = estudio["descripcion"]

    arbol = ET.ElementTree(rss)
    ET.indent(arbol, space="  ")

    arbol.write(
        ARCHIVO_RSS,
        encoding="utf-8",
        xml_declaration=True,
    )

    print(
        f"feed.xml creado con {len(estudios)} estudios.",
        flush=True,
    )


def main():
    try:
        datos = descargar_estudios()
        estudios_nuevos = preparar_estudios(datos)

        print(
            f"Estudios válidos extraídos: {len(estudios_nuevos)}",
            flush=True,
        )

        if not estudios_nuevos:
            raise RuntimeError(
                "No se ha podido extraer ningún estudio clínico."
            )

        estudios_anteriores = cargar_estado()

        estudios = combinar_estudios(
            estudios_nuevos,
            estudios_anteriores,
        )

        guardar_estado(estudios)
        crear_rss(estudios)

        print("Proceso terminado correctamente.", flush=True)

        print(
            "URL para Feedly: "
            "https://raw.githubusercontent.com/"
            "plis2100/rss-reec-aemps/main/feed.xml",
            flush=True,
        )

    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
