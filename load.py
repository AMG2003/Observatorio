# os: para leer variables de entorno (usuario, clave, etc.)
import os
# pandas: para leer el parquet de plata
import pandas as pd
# Path: manejo moderno de rutas
from pathlib import Path
# load_dotenv: lee el archivo .env y deja sus valores como variables de entorno
from dotenv import load_dotenv
# create_engine: crea la "conexión" a la base; URL: arma la dirección de conexión de forma segura
from sqlalchemy import create_engine
from sqlalchemy.engine import URL


# ============================================================
# CONFIGURACIÓN
# ============================================================

# Parquet generado por la capa plata
ARCHIVO_SILVER = Path("data/silver/economic_indicators.parquet")

# Script SQL que construye la capa gold
ARCHIVO_GOLD = Path("sql/gold.sql")

# Columnas que se cargan, en el mismo orden que la tabla de PostgreSQL
COLUMNAS = [
    "indicador",
    "nombre_indicador",
    "unidad_medida",
    "fecha",
    "valor",
    "fuente",
    "archivo_origen",
    "fecha_extraccion",
    "fecha_procesamiento",
]

# SQL que crea los schemas y la tabla de plata si aún no existen
DDL_SILVER = """
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS silver.economic_indicators (
    indicador            TEXT          NOT NULL,
    nombre_indicador     TEXT,
    unidad_medida        TEXT,
    fecha                DATE          NOT NULL,
    valor                NUMERIC(18,6) NOT NULL,
    fuente               TEXT,
    archivo_origen       TEXT,
    fecha_extraccion     TIMESTAMP,
    fecha_procesamiento  TIMESTAMP,
    PRIMARY KEY (indicador, fecha)
);
"""


# ============================================================
# 1. CONEXIÓN
# ============================================================

def crear_engine():
    # Lee el archivo .env (si existe) y carga sus valores como variables de entorno
    load_dotenv()

    # URL.create arma la dirección de conexión y maneja bien claves con caracteres especiales
    url = URL.create(
        drivername="postgresql+pg8000",             # motor + driver (pg8000 es Python puro, sin DLL)
        username=os.getenv("PG_USER", "postgres"),  # usuario (por defecto postgres)
        password=os.getenv("PG_PASSWORD"),          # clave, viene del .env (nunca en el código)
        host=os.getenv("PG_HOST", "localhost"),     # servidor
        port=int(os.getenv("PG_PORT", "5432")),     # puerto estándar de PostgreSQL
        database=os.getenv("PG_DB", "observatorio"),  # nombre de la base de datos
    )

    # Crea el engine; pool_pre_ping verifica que la conexión siga viva antes de usarla
    return create_engine(url, pool_pre_ping=True)


# ============================================================
# EJECUTAR SCRIPTS SQL CON VARIAS SENTENCIAS
# ============================================================

def ejecutar_script(conexion, sql):
    # pg8000 ejecuta una sentencia por llamada, así que separamos el script nosotros.
    # Paso 1: quitamos los comentarios (todo lo que va después de "--" en cada línea)
    lineas = []
    for linea in sql.splitlines():
        linea = linea.split("--")[0]
        # Descartamos las líneas que quedaron vacías
        if linea.strip():
            lineas.append(linea)

    # Paso 2: unimos de nuevo y separamos por ";" (fin de cada sentencia)
    for sentencia in "\n".join(lineas).split(";"):
        # Ejecutamos solo las que tienen contenido
        if sentencia.strip():
            conexion.exec_driver_sql(sentencia)


# ============================================================
# 2. CARGAR PLATA
# ============================================================

def cargar_silver(engine):
    # Verifica que plata exista antes de intentar cargar
    if not ARCHIVO_SILVER.exists():
        raise FileNotFoundError(f"No existe {ARCHIVO_SILVER}. Ejecuta primero silver_transform.py")

    # Lee el parquet y deja solo las columnas que usa la tabla, en orden
    df = pd.read_parquet(ARCHIVO_SILVER)[COLUMNAS]

    # La columna fecha no tiene hora: la pasamos a tipo fecha pura (coincide con DATE en PostgreSQL)
    df["fecha"] = pd.to_datetime(df["fecha"]).dt.date

    # engine.begin() abre una TRANSACCIÓN: si algo falla, se deshace todo (no queda a medias)
    with engine.begin() as conexion:
        # Crea schemas y tabla si no existen
        ejecutar_script(conexion, DDL_SILVER)

        # Reemplazo completo: vacía la tabla para no duplicar datos entre corridas
        conexion.exec_driver_sql("TRUNCATE TABLE silver.economic_indicators")

        # Inserta el DataFrame en la tabla
        df.to_sql(
            name="economic_indicators",  # nombre de la tabla
            con=conexion,                # usa la misma transacción
            schema="silver",             # schema de destino
            if_exists="append",          # agrega filas (la tabla ya existe con sus tipos)
            index=False,                 # no guardar el índice de pandas como columna
            method="multi",              # varios registros por INSERT (más rápido)
            chunksize=1000,              # inserta de a 1000 filas
        )

    print(f"Silver cargada en PostgreSQL: {len(df)} registros")


# ============================================================
# 3. CONSTRUIR GOLD
# ============================================================

def construir_gold(engine):
    # Verifica que exista el script SQL
    if not ARCHIVO_GOLD.exists():
        raise FileNotFoundError(f"No existe {ARCHIVO_GOLD}")

    # Lee todo el script SQL como texto
    sql = ARCHIVO_GOLD.read_text(encoding="utf-8")

    # Ejecuta el script completo dentro de una transacción
    with engine.begin() as conexion:
        ejecutar_script(conexion, sql)

    print("Gold construida: dim_indicador, dim_fecha, kpi_diarios, macro_mensual, v_alertas_dolar")


# ============================================================
# 4. PROCESO PRINCIPAL
# ============================================================

def main():
    print("=" * 60)
    print("INICIO CARGA SILVER → POSTGRESQL → GOLD")
    print("=" * 60)

    # Crea la conexión
    engine = crear_engine()

    # Carga plata y luego construye gold con SQL
    cargar_silver(engine)
    construir_gold(engine)

    print("\nPROCESO FINALIZADO")


# Solo ejecuta main() si corres este archivo directamente
if __name__ == "__main__":
    main()