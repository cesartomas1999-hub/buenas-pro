"""
Descarga las buenas pro (adjudicaciones) del Perú desde la API pública OCDS del OECE.

Uso normal (lo hace GitHub Actions cada mañana):
    python descargar.py

Probar con una fecha pasada:
    python descargar.py --fecha 2026-10-29

Qué hace, en simple:
  1. La API no permite pedir "las buenas pro del día X". Solo permite pedir los procesos
     agrupados por el MES en que se convocaron. Por eso el script revisa todos los procesos
     convocados en los últimos meses y se queda con los que tienen buena pro en las fechas
     que nos interesan.
  2. El OECE publica con algo de retraso, así que en cada ejecución el script vuelve a
     calcular los últimos días (no solo ayer). Así, si una buena pro aparece tarde, igual
     se agrega a su día.
  3. Guarda un archivo por día en data/AAAA-MM-DD.json, el de ayer también como
     data/latest.json, y una lista de días disponibles en data/index.json.
     Borra los días con más de 30 días de antigüedad.
"""

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

API = "https://contratacionesabiertas.oece.gob.pe/api/v1/recordsAfter"
ENLACE_PROCESO = "https://contratacionesabiertas.oece.gob.pe/proceso/{ocid}"
LIMA = timezone(timedelta(hours=-5))  # Perú no usa horario de verano

MESES_ATRAS = 12       # cuántos meses de convocatorias revisar (algunas obras tardan meses)
DIAS_A_REVISAR = 7     # cada día se recalculan los últimos 7 días (por publicaciones tardías)
DIAS_HISTORIAL = 30    # cuántos días se guardan

CARPETA_DATOS = Path(__file__).parent / "data"

TIPOS = {"works": "Obra", "goods": "Bien", "services": "Servicio"}
TEXTO_SIN_DATO = re.compile(r"ver dato|ficha del proveedor|^1+$|^0+$", re.I)


def hoy_lima() -> date:
    return datetime.now(LIMA).date()


def meses_a_revisar(fecha_mas_reciente: date, cuantos: int) -> list[str]:
    """Devuelve ['2026-11', '2026-10', ...] hacia atrás."""
    año, mes = fecha_mas_reciente.year, fecha_mas_reciente.month
    salida = []
    for _ in range(cuantos):
        salida.append(f"{año:04d}-{mes:02d}")
        mes -= 1
        if mes == 0:
            año, mes = año - 1, 12
    return salida


def pedir(sesion: requests.Session, url: str, params=None) -> dict:
    """Pide una página a la API, reintentando si falla."""
    for intento in range(6):
        try:
            r = sesion.get(url, params=params, timeout=180)
            if r.status_code == 404:
                return {"records": [], "links": {}}
            r.raise_for_status()
            return r.json()
        except Exception as e:  # red lenta, servidor ocupado, etc.
            espera = 5 * (intento + 1)
            print(f"   aviso: {e} — reintento en {espera}s", flush=True)
            time.sleep(espera)
    raise RuntimeError(f"La API no respondió: {url}")


def recorrer_mes(segmento: str):
    """Recorre todos los procesos convocados en un mes (página por página)."""
    sesion = requests.Session()
    sesion.headers["User-Agent"] = "buenas-pro-personal/1.0"
    datos = pedir(sesion, API, {"dataSegmentationID": segmento, "size": 100})
    paginas = 0
    while datos.get("records"):
        paginas += 1
        for record in datos["records"]:
            yield record.get("compiledRelease") or {}
        siguiente = (datos.get("links") or {}).get("next")
        if not siguiente:
            break
        datos = pedir(sesion, siguiente)
    print(f"   mes {segmento}: {paginas} páginas revisadas", flush=True)


def ruc_de(id_ocds: str) -> str:
    """'PE-RUC-20611932767' -> '20611932767'. Los consorcios traen un código interno, no RUC."""
    numero = (id_ocds or "").replace("PE-RUC-", "")
    return numero if re.fullmatch(r"\d{11}", numero) else ""


def telefono_de(parte: dict) -> str:
    tel = ((parte or {}).get("contactPoint") or {}).get("telephone", "") or ""
    return "" if TEXTO_SIN_DATO.search(tel.strip()) else tel.strip()


def tipo_de(tender: dict) -> str:
    categoria = TIPOS.get(tender.get("mainProcurementCategory"), "Otro")
    metodo = (tender.get("procurementMethodDetails") or "").lower()
    titulo = (tender.get("title") or "").upper()
    descripcion = (tender.get("description") or "").upper()
    if categoria == "Servicio" and (
        "consultor" in metodo
        or re.match(r"^(CP|LP|CONV)?\s*CON[-\s]", titulo)
        or descripcion.startswith(("CONSULTOR", "SERVICIO DE CONSULTOR", "SUPERVISI", "ELABORACI"))
        or "EXPEDIENTE TÉCNICO" in descripcion
        or "EXPEDIENTE TECNICO" in descripcion
    ):
        return "Consultoría"
    return categoria


def convertir(proceso: dict, adjudicacion: dict) -> dict:
    """Arma una fila limpia con los datos que se muestran en la página."""
    tender = proceso.get("tender") or {}
    partes = {p.get("id"): p for p in proceso.get("parties") or []}
    comprador = partes.get((proceso.get("buyer") or {}).get("id")) or {}
    direccion = comprador.get("address") or {}

    ganadores = []
    for proveedor in adjudicacion.get("suppliers") or []:
        parte = partes.get(proveedor.get("id")) or {}
        nombre = proveedor.get("name") or parte.get("name") or ""
        ganadores.append({
            "nombre": nombre,
            "codigo": proveedor.get("id") or "",   # p. ej. PE-RUC-20611932767 (sirve para enlazar su historial)
            "ruc": ruc_de(proveedor.get("id")),
            "consorcio": nombre.upper().startswith("CONSORCIO") or not ruc_de(proveedor.get("id")),
            # La API del OECE no publica los integrantes de los consorcios; se deja el
            # campo listo por si algún día los agrega.
            "integrantes": [],
            "telefono": telefono_de(parte),
        })

    # Documentos del SEACE: el acta de buena pro trae los integrantes de los consorcios
    def ultimo_documento(tipo):
        docs = [d for d in tender.get("documents") or [] if d.get("documentType") == tipo and d.get("url")]
        return docs[-1]["url"] if docs else ""

    items = [i.get("description") for i in adjudicacion.get("items") or [] if i.get("description")]
    valor = adjudicacion.get("value") or {}
    entidad_ruc = next((x.get("id") for x in comprador.get("additionalIdentifiers") or []
                        if x.get("scheme") == "PE-RUC"), "")

    return {
        "id": adjudicacion.get("id"),
        "ocid": proceso.get("ocid"),
        "entidad": (proceso.get("buyer") or {}).get("name", ""),
        "entidad_ruc": entidad_ruc,
        "departamento": (direccion.get("department") or "").upper(),
        "provincia": direccion.get("region") or "",
        "nomenclatura": tender.get("title", ""),
        "procedimiento": tender.get("procurementMethodDetails", ""),
        "tipo": tipo_de(tender),
        "descripcion": tender.get("description", ""),
        "items": items[:5],
        "fecha_buena_pro": (adjudicacion.get("date") or "")[:10],
        "monto": valor.get("amount"),
        "moneda": valor.get("currency") or "PEN",
        "valor_referencial": (tender.get("value") or {}).get("amount"),
        "postores": tender.get("numberOfTenderers"),
        "ganadores": ganadores,
        "enlace": ENLACE_PROCESO.format(ocid=proceso.get("ocid")),
        "acta": ultimo_documento("awardNotice"),
        "evaluacion": ultimo_documento("evaluationReports"),
    }


def descargar(fechas: set[str], meses: list[str]) -> dict[str, list[dict]]:
    """Revisa los meses indicados (en paralelo) y agrupa las buenas pro por fecha."""
    por_fecha = {f: [] for f in fechas}
    vistos = set()

    def trabajar(segmento):
        encontrados = []
        for proceso in recorrer_mes(segmento):
            for adj in proceso.get("awards") or []:
                fecha = (adj.get("date") or "")[:10]
                if fecha in fechas and adj.get("status") not in ("cancelled", "unsuccessful"):
                    encontrados.append(convertir(proceso, adj))
        return encontrados

    with ThreadPoolExecutor(max_workers=4) as grupo:
        for lista in grupo.map(trabajar, meses):
            for fila in lista:
                clave = (fila["ocid"], fila["id"])
                if clave not in vistos:
                    vistos.add(clave)
                    por_fecha[fila["fecha_buena_pro"]].append(fila)

    for filas in por_fecha.values():
        filas.sort(key=lambda f: -(f["monto"] or 0))
    return por_fecha


def guardar(por_fecha: dict[str, list[dict]], ultima: str, hoy: date):
    CARPETA_DATOS.mkdir(exist_ok=True)
    generado = datetime.now(LIMA).isoformat(timespec="seconds")

    for fecha, filas in por_fecha.items():
        contenido = {"fecha": fecha, "generado": generado, "total": len(filas), "buenas_pro": filas}
        texto = json.dumps(contenido, ensure_ascii=False, separators=(",", ":"))
        (CARPETA_DATOS / f"{fecha}.json").write_text(texto, encoding="utf-8")
        if fecha == ultima:
            (CARPETA_DATOS / "latest.json").write_text(texto, encoding="utf-8")

    # Borrar días antiguos y armar la lista de días disponibles
    limite = (hoy - timedelta(days=DIAS_HISTORIAL)).isoformat()
    dias = []
    for archivo in CARPETA_DATOS.glob("????-??-??.json"):
        if archivo.stem < limite:
            archivo.unlink()
            continue
        total = json.loads(archivo.read_text(encoding="utf-8")).get("total", 0)
        dias.append({"fecha": archivo.stem, "total": total})
    dias.sort(key=lambda d: d["fecha"], reverse=True)
    indice = {"generado": generado, "ultima": ultima, "dias": dias}
    (CARPETA_DATOS / "index.json").write_text(json.dumps(indice, ensure_ascii=False, indent=1),
                                              encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", help="Día a descargar (AAAA-MM-DD). Por defecto: ayer en Lima.")
    ap.add_argument("--dias", type=int, default=DIAS_A_REVISAR,
                    help="Cuántos días hacia atrás recalcular (incluye --fecha).")
    ap.add_argument("--meses", type=int, default=MESES_ATRAS)
    args = ap.parse_args()

    hoy = hoy_lima()
    ultima = date.fromisoformat(args.fecha) if args.fecha else hoy - timedelta(days=1)
    fechas = {(ultima - timedelta(days=i)).isoformat() for i in range(args.dias)}
    meses = meses_a_revisar(ultima, args.meses)

    print(f"Buscando buenas pro de {min(fechas)} a {ultima} en convocatorias de {meses[-1]} a {meses[0]}…",
          flush=True)
    inicio = time.time()
    por_fecha = descargar(fechas, meses)
    # Si ayer fue domingo o feriado (0 buenas pro), la página abre en el último día con datos.
    con_datos = [f for f in sorted(por_fecha, reverse=True) if por_fecha[f]]
    mostrar = con_datos[0] if con_datos else ultima.isoformat()
    guardar(por_fecha, mostrar, hoy)

    for f in sorted(por_fecha):
        print(f"  {f}: {len(por_fecha[f])} buenas pro")
    print(f"Listo en {int(time.time() - inicio)} s. La página abrirá en {mostrar} "
          f"({len(por_fecha.get(mostrar, []))} buenas pro).")


if __name__ == "__main__":
    sys.exit(main())
