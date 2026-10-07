import json
import pandas as pd
from pathlib import Path
from datetime import datetime


# ============================================================
# CONFIGURACIÓN
# ============================================================

CARPETA_BRONCE = Path("data/bronze")
CARPETA_SILVER = Path("data/silver")
ARCHIVO_SILVER = CARPETA_SILVER / "economic_indicators.parquet"


# ============================================================
# 1. LEER ARCHIVOS BRONZE
# ============================================================

def leer_archivo_bronze(ruta):
    with open(ruta, "r", encoding="utf-8") as archivo:
        return json.load(archivo)


# ============================================================
# 2. FECHA DE EXTRACCIÓN DESDE EL NOMBRE DEL ARCHIVO
# ============================================================

def obtener_fecha_extraccion(ruta):
    # Nombre esperado: dolar_2025_20261006_083000.json
    # ruta.stem quita la extensión; split("_") separa por guion bajo
    partes = ruta.stem.split("_")

    # Las dos últimas partes son la fecha y la hora de la extracción
    texto = f"{partes[-2]}_{partes[-1]}"

    # Convierte el texto "20261006_083000" en un objeto datetime
    return datetime.strptime(texto, "%Y%m%d_%H%M%S")


# ============================================================
# 3. TRANSFORMAR UN INDICADOR
# ============================================================

def transformar_indicador(datos, ruta):
    serie = datos.get("serie", [])

    if not serie:
        print(f"ADVERTENCIA: {ruta.name} no contiene datos")
        return pd.DataFrame()

    # CAMBIO: fecha de extracción, necesaria para elegir la versión más reciente
    fecha_extraccion = obtener_fecha_extraccion(ruta)

    filas = []

    for registro in serie:
        filas.append({
            "indicador": datos.get("codigo"),
            "nombre_indicador": datos.get("nombre"),
            "unidad_medida": datos.get("unidad_medida"),
            "fecha": registro.get("fecha"),
            "valor": registro.get("valor"),
            "fuente": "mindicador.cl",
            # CAMBIO: trazabilidad (de qué archivo y cuándo salió cada fila)
            "archivo_origen": ruta.name,
            "fecha_extraccion": fecha_extraccion,
        })

    return pd.DataFrame(filas)


# ============================================================
# 4. LIMPIEZA Y ESTANDARIZACIÓN
# ============================================================

def limpiar_datos(df):
    if df.empty:
        return df

    # --------------------------------------------------------
    # Fecha: texto -> datetime (utc=True evita errores con la "Z" final)
    # --------------------------------------------------------
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce", utc=True)

    # Convertir a hora de Chile, quitar la zona horaria
    # y CAMBIO: normalizar a día (00:00:00), porque estos indicadores no tienen hora
    df["fecha"] = (
        df["fecha"]
        .dt.tz_convert("America/Santiago")
        .dt.tz_localize(None)
        .dt.normalize()
    )

    # --------------------------------------------------------
    # Valor a número
    # --------------------------------------------------------
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")

    # --------------------------------------------------------
    # Eliminar registros inválidos
    # --------------------------------------------------------
    df = df.dropna(subset=["indicador", "fecha", "valor"])

    # --------------------------------------------------------
    # CAMBIO: duplicados conservando la extracción MÁS RECIENTE
    # --------------------------------------------------------
    # Primero ordenamos: la extracción más nueva queda al final de cada grupo
    df = df.sort_values(["indicador", "fecha", "fecha_extraccion"])

    # keep="last" se queda con la última fila de cada (indicador, fecha)
    df = df.drop_duplicates(subset=["indicador", "fecha"], keep="last")

    # --------------------------------------------------------
    # Fecha de procesamiento y orden final
    # --------------------------------------------------------
    df["fecha_procesamiento"] = datetime.now()

    df = df.sort_values(["indicador", "fecha"]).reset_index(drop=True)

    return df


# ============================================================
# 5. VALIDACIONES DE CALIDAD
# ============================================================

def validar_datos(df):
    if df.empty:
        raise ValueError("El DataFrame Silver está vacío")

    for columna in ["indicador", "fecha", "valor"]:
        nulos = df[columna].isna().sum()
        if nulos > 0:
            raise ValueError(f"La columna {columna} contiene {nulos} valores nulos")

    duplicados = df.duplicated(subset=["indicador", "fecha"]).sum()
    if duplicados > 0:
        raise ValueError(f"Se encontraron {duplicados} registros duplicados")

    print("\nVALIDACIÓN DE CALIDAD")
    print("---------------------")
    print(f"Registros: {len(df)}")
    print(f"Indicadores: {df['indicador'].nunique()}")
    print(f"Fecha mínima: {df['fecha'].min()}")
    print(f"Fecha máxima: {df['fecha'].max()}")
    print("\nRegistros por indicador:")
    print(df["indicador"].value_counts())


# ============================================================
# 6. GUARDAR SILVER
# ============================================================

def guardar_silver(df):
    CARPETA_SILVER.mkdir(parents=True, exist_ok=True)
    df.to_parquet(ARCHIVO_SILVER, index=False)
    print(f"\nSilver guardada en: {ARCHIVO_SILVER}")


# ============================================================
# 7. PROCESO PRINCIPAL
# ============================================================

def main():
    print("=" * 60)
    print("INICIO TRANSFORMACIÓN BRONZE → SILVER")
    print("=" * 60)

    archivos = sorted(CARPETA_BRONCE.glob("*.json"))

    if not archivos:
        raise FileNotFoundError("No existen archivos JSON en Bronze")

    dataframes = []

    for archivo in archivos:
        try:
            print(f"Procesando: {archivo.name}")
            datos = leer_archivo_bronze(archivo)
            df = transformar_indicador(datos, archivo)

            if not df.empty:
                dataframes.append(df)

        except json.JSONDecodeError as error:
            print(f"ERROR JSON: {archivo.name} - {error}")

        except Exception as error:
            print(f"ERROR procesando {archivo.name}: {error}")

    if not dataframes:
        raise ValueError("No se pudieron procesar datos")

    df_silver = pd.concat(dataframes, ignore_index=True)
    df_silver = limpiar_datos(df_silver)

    validar_datos(df_silver)
    guardar_silver(df_silver)

    print("\nPROCESO FINALIZADO")


if __name__ == "__main__":
    main()