-- ============================================================
-- GOLD: tablas listas para Power BI
-- Se reconstruye completa en cada corrida (DROP + CREATE TABLE AS)
-- Fuente: silver.economic_indicators
-- ============================================================

-- Primero borramos lo anterior. CASCADE borra también lo que dependa (la vista de alertas)
DROP VIEW  IF EXISTS gold.v_alertas_dolar;
DROP TABLE IF EXISTS gold.kpi_diarios    CASCADE;
DROP TABLE IF EXISTS gold.macro_mensual  CASCADE;
DROP TABLE IF EXISTS gold.dim_fecha      CASCADE;
DROP TABLE IF EXISTS gold.dim_indicador  CASCADE;


-- ============================================================
-- 1. DIM_INDICADOR: una fila por indicador
-- ============================================================
CREATE TABLE gold.dim_indicador AS
SELECT
    indicador,
    MAX(nombre_indicador) AS nombre_indicador,
    MAX(unidad_medida)    AS unidad_medida,
    -- Dólar y UF se publican a diario; el resto una vez al mes
    CASE WHEN indicador IN ('dolar', 'uf') THEN 'Diaria' ELSE 'Mensual' END AS frecuencia,
    MIN(fecha)            AS primera_fecha,
    MAX(fecha)            AS ultima_fecha
FROM silver.economic_indicators
GROUP BY indicador;

ALTER TABLE gold.dim_indicador ADD PRIMARY KEY (indicador);


-- ============================================================
-- 2. DIM_FECHA: calendario (una fila por día) para Power BI
-- ============================================================
CREATE TABLE gold.dim_fecha AS
SELECT
    d::date                                   AS fecha,
    EXTRACT(YEAR    FROM d)::int              AS anio,
    EXTRACT(QUARTER FROM d)::int              AS trimestre,
    EXTRACT(MONTH   FROM d)::int              AS mes,
    -- Nombre del mes en español: tomamos la posición del mes dentro de un arreglo
    (ARRAY['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio',
           'Agosto','Septiembre','Octubre','Noviembre','Diciembre'])[EXTRACT(MONTH FROM d)::int] AS nombre_mes,
    TO_CHAR(d, 'YYYY-MM')                     AS anio_mes,
    DATE_TRUNC('month', d)::date              AS inicio_mes,
    -- ISODOW: 1 = lunes ... 7 = domingo
    EXTRACT(ISODOW FROM d)::int               AS dia_semana,
    (EXTRACT(ISODOW FROM d) IN (6, 7))        AS es_fin_de_semana
FROM generate_series(
        (SELECT MIN(fecha) FROM silver.economic_indicators)::timestamp,
        CURRENT_DATE::timestamp,
        INTERVAL '1 day'
     ) AS d;

ALTER TABLE gold.dim_fecha ADD PRIMARY KEY (fecha);


-- ============================================================
-- 3. KPI_DIARIOS: KPIs de dólar y UF (series diarias)
-- ============================================================
CREATE TABLE gold.kpi_diarios AS
WITH base AS (
    -- Solo series diarias y sin fechas futuras (la UF se publica por adelantado)
    SELECT indicador, fecha, valor
    FROM silver.economic_indicators
    WHERE indicador IN ('dolar', 'uf')
      AND fecha <= CURRENT_DATE
),
retornos AS (
    -- Variación respecto de la observación anterior (en fracción, ej: 0.01 equivale a 1 por ciento)
    SELECT
        indicador,
        fecha,
        valor,
        LAG(valor) OVER w AS valor_anterior,
        valor / NULLIF(LAG(valor) OVER w, 0) - 1 AS retorno_diario
    FROM base
    -- Ventana con nombre: se calcula por indicador, en orden de fecha
    WINDOW w AS (PARTITION BY indicador ORDER BY fecha)
)
SELECT
    r.indicador,
    r.fecha,
    r.valor,
    r.valor_anterior,

    -- Variación porcentual vs la observación anterior
    ROUND((r.retorno_diario * 100)::numeric, 4) AS var_dia_pct,

    -- Variación vs hace 30 días y vs hace 12 meses (porcentaje).
    -- Usa el último valor disponible en o antes de esa fecha (el dólar no tiene fines de semana)
    ROUND(((r.valor / NULLIF(v30.valor, 0) - 1) * 100)::numeric, 4)  AS var_30d_pct,
    ROUND(((r.valor / NULLIF(v365.valor, 0) - 1) * 100)::numeric, 4) AS var_12m_pct,

    -- Medias móviles sobre las últimas 7 y 30 observaciones
    ROUND(AVG(r.valor) OVER (PARTITION BY r.indicador ORDER BY r.fecha
          ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)::numeric, 4)  AS media_movil_7,
    ROUND(AVG(r.valor) OVER (PARTITION BY r.indicador ORDER BY r.fecha
          ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)::numeric, 4) AS media_movil_30,

    -- Volatilidad: desviación estándar de los retornos de las últimas 30 observaciones,
    -- anualizada con raíz de 252 (días hábiles de mercado), en porcentaje
    ROUND((STDDEV_SAMP(r.retorno_diario) OVER (PARTITION BY r.indicador ORDER BY r.fecha
          ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) * SQRT(252) * 100)::numeric, 4) AS volatilidad_30_anual_pct,

    -- Máximo y mínimo de los últimos 52 semanas (364 días)
    MAX(r.valor) OVER (PARTITION BY r.indicador ORDER BY r.fecha
          RANGE BETWEEN INTERVAL '364 days' PRECEDING AND CURRENT ROW) AS max_52s,
    MIN(r.valor) OVER (PARTITION BY r.indicador ORDER BY r.fecha
          RANGE BETWEEN INTERVAL '364 days' PRECEDING AND CURRENT ROW) AS min_52s

FROM retornos r
-- LATERAL: para cada fila, busca el valor más reciente en o antes de (fecha - 30 días)
LEFT JOIN LATERAL (
    SELECT s.valor
    FROM base s
    WHERE s.indicador = r.indicador
      AND s.fecha <= r.fecha - 30
    ORDER BY s.fecha DESC
    LIMIT 1
) v30 ON TRUE
-- Lo mismo para hace 365 días
LEFT JOIN LATERAL (
    SELECT s.valor
    FROM base s
    WHERE s.indicador = r.indicador
      AND s.fecha <= r.fecha - 365
    ORDER BY s.fecha DESC
    LIMIT 1
) v365 ON TRUE;

ALTER TABLE gold.kpi_diarios ADD PRIMARY KEY (indicador, fecha);


-- ============================================================
-- 4. MACRO_MENSUAL: inflación, TPM, tasa real, dólar y UF por mes
-- ============================================================
CREATE TABLE gold.macro_mensual AS
WITH mensual AS (
    -- Una fila por mes con cada indicador en su propia columna (pivot con FILTER)
    SELECT
        DATE_TRUNC('month', fecha)::date AS mes,
        -- IPC: variación mensual en porcentaje, hay un solo dato por mes
        AVG(valor) FILTER (WHERE indicador = 'ipc')    AS ipc_mensual_pct,
        -- TPM, UTM, UF y dólar de cierre: último dato del mes
        (ARRAY_AGG(valor ORDER BY fecha DESC) FILTER (WHERE indicador = 'tpm'))[1]   AS tpm_pct,
        (ARRAY_AGG(valor ORDER BY fecha DESC) FILTER (WHERE indicador = 'utm'))[1]   AS utm,
        (ARRAY_AGG(valor ORDER BY fecha DESC) FILTER (WHERE indicador = 'uf'))[1]    AS uf_cierre,
        (ARRAY_AGG(valor ORDER BY fecha DESC) FILTER (WHERE indicador = 'dolar'))[1] AS dolar_cierre,
        AVG(valor) FILTER (WHERE indicador = 'dolar')  AS dolar_promedio
    FROM silver.economic_indicators
    WHERE fecha <= CURRENT_DATE
    GROUP BY DATE_TRUNC('month', fecha)
),
calculos AS (
    SELECT
        m.*,
        -- Inflación acumulada 12 meses: se componen los 12 IPC mensuales
        -- (producto de (1 + ipc/100)) calculado como exp(suma de logaritmos).
        -- Solo se calcula si hay 12 meses de IPC disponibles en la ventana
        CASE WHEN COUNT(ipc_mensual_pct) OVER w12 = 12
             THEN (EXP(SUM(LN(1 + ipc_mensual_pct / 100)) OVER w12) - 1) * 100
        END AS ipc_12m_pct,
        -- Variaciones del dólar y la UF
        (dolar_cierre / NULLIF(LAG(dolar_cierre) OVER w, 0) - 1) * 100       AS dolar_var_mensual_pct,
        (uf_cierre    / NULLIF(LAG(uf_cierre)    OVER w, 0) - 1) * 100       AS uf_var_mensual_pct,
        (uf_cierre    / NULLIF(LAG(uf_cierre, 12) OVER w, 0) - 1) * 100      AS uf_var_anual_pct
    FROM mensual m
    -- w: orden por mes; w12: ventana de este mes y los 11 anteriores
    WINDOW w   AS (ORDER BY mes),
           w12 AS (ORDER BY mes ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)
)
SELECT
    mes,
    ROUND(ipc_mensual_pct::numeric, 4)        AS ipc_mensual_pct,
    ROUND(ipc_12m_pct::numeric, 4)            AS ipc_12m_pct,
    ROUND(tpm_pct::numeric, 4)                AS tpm_pct,
    -- Tasa real (Fisher): ((1 + TPM) / (1 + inflación 12m) - 1), en porcentaje
    ROUND((((1 + tpm_pct / 100) / (1 + ipc_12m_pct / 100) - 1) * 100)::numeric, 4) AS tasa_real_pct,
    ROUND(dolar_promedio::numeric, 4)         AS dolar_promedio,
    ROUND(dolar_cierre::numeric, 4)           AS dolar_cierre,
    ROUND(dolar_var_mensual_pct::numeric, 4)  AS dolar_var_mensual_pct,
    ROUND(uf_cierre::numeric, 4)              AS uf_cierre,
    ROUND(uf_var_mensual_pct::numeric, 4)     AS uf_var_mensual_pct,
    ROUND(uf_var_anual_pct::numeric, 4)       AS uf_var_anual_pct,
    ROUND(utm::numeric, 4)                    AS utm
FROM calculos;

ALTER TABLE gold.macro_mensual ADD PRIMARY KEY (mes);


-- ============================================================
-- 5. V_ALERTAS_DOLAR: días con movimiento fuerte del dólar
--    Sirve de insumo para la automatización (correo/alerta)
-- ============================================================
CREATE VIEW gold.v_alertas_dolar AS
SELECT
    fecha,
    valor,
    valor_anterior,
    var_dia_pct,
    -- Umbral de 1 por ciento diario; ajústalo a gusto
    CASE WHEN var_dia_pct >= 0 THEN 'Alza' ELSE 'Baja' END AS direccion
FROM gold.kpi_diarios
WHERE indicador = 'dolar'
  AND ABS(var_dia_pct) >= 1;