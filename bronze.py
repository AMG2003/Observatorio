# requests: librería para hacer peticiones HTTP a la API
import requests
# json: para guardar el diccionario de Python como archivo .json
import json
# time: para hacer una pausa corta entre peticiones
import time
# Path: forma moderna de manejar rutas de carpetas y archivos
from pathlib import Path
# datetime: para la marca de tiempo y para saber el año actual
from datetime import datetime


# ============================================================
# CONFIGURACIÓN
# ============================================================

# {indicador} y {anio} son huecos que se rellenan con .format()
# Ejemplo final: https://mindicador.cl/api/dolar/2025
URL_BASE = "https://mindicador.cl/api/{indicador}/{anio}"

# Indicadores a extraer; cada uno es un "endpoint" distinto de la API
INDICADORES = ["dolar", "uf", "utm", "ipc", "tpm"]

# Primer año de historia que queremos (editable)
ANIO_INICIO = 2024

# Último año: el actual, calculado automáticamente
ANIO_FIN = datetime.now().year

# Carpeta donde se guardan los JSON crudos (capa bronce)
CARPETA_BRONCE = Path("data/bronze")


# ============================================================
# 1. EXTRAER DESDE LA API
# ============================================================

def extraer_indicador(indicador, anio):
    # Arma la URL completa reemplazando los huecos
    url = URL_BASE.format(indicador=indicador, anio=anio)

    # GET = "pedir datos"; timeout=30 evita esperar infinito si el servidor no responde
    respuesta = requests.get(url, timeout=30)

    # Si el servidor devolvió error (404, 500...), lanza una excepción
    respuesta.raise_for_status()

    # Convierte el texto JSON de la respuesta en un diccionario de Python
    return respuesta.json()


# ============================================================
# 2. GUARDAR EN BRONCE (dato crudo, sin transformar)
# ============================================================

def guardar_bronce(indicador, anio, datos, marca):
    # Crea la carpeta (y las intermedias) si no existen
    CARPETA_BRONCE.mkdir(parents=True, exist_ok=True)

    # Nombre: indicador_anio_fecha_hora.json
    # Plata usará las dos últimas partes (fecha y hora) como fecha de extracción
    ruta = CARPETA_BRONCE / f"{indicador}_{anio}_{marca}.json"

    # Abre el archivo para escribir con UTF-8 (respeta tildes)
    with open(ruta, "w", encoding="utf-8") as archivo:
        # indent=2 lo deja legible; ensure_ascii=False evita convertir tildes a códigos
        json.dump(datos, archivo, indent=2, ensure_ascii=False)

    # Devuelve la ruta para mostrarla en el log
    return ruta


# ============================================================
# 3. PROCESO PRINCIPAL
# ============================================================

def main():
    # Una sola marca de tiempo para toda la corrida: todos los archivos
    # de esta ejecución comparten la misma fecha de extracción
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Contadores para el resumen final
    ok = 0
    errores = 0

    # Recorre cada indicador
    for indicador in INDICADORES:
        # Recorre cada año; range excluye el final, por eso se suma 1
        for anio in range(ANIO_INICIO, ANIO_FIN + 1):
            # try/except: si una petición falla, el resto sigue funcionando
            try:
                # Pide los datos de ese indicador y año
                datos = extraer_indicador(indicador, anio)

                # Los guarda tal cual llegaron
                ruta = guardar_bronce(indicador, anio, datos, marca)

                print(f"OK    {indicador} {anio} -> {ruta.name}")
                ok += 1

            # Captura errores de red o HTTP (sin internet, API caída, año inválido)
            except requests.RequestException as error:
                print(f"ERROR {indicador} {anio}: {error}")
                errores += 1

            # Pausa de medio segundo para no saturar la API
            time.sleep(0.5)

    # Resumen final
    print(f"\nExtracción terminada: {ok} correctas, {errores} con error")


# Solo ejecuta main() si corres este archivo directamente
if __name__ == "__main__":
    main()