# Arquitectura del sistema de monitoreo y pronóstico agroclimático

Proyecto: *Fortalecimiento de la resiliencia de los sistemas agroalimentarios
frente al Fenómeno de El Niño* — Alianza Bioversity-CIAT / Programa Mundial de
Alimentos.

Repositorio: `pmaclimafap/PMA_nino`

Última actualización: septiembre de 2026

---

## 1. Qué es esto

Un portal web que muestra tres módulos:

| Módulo | Contenido | Estado |
|---|---|---|
| **Monitoreo** | Lluvia, temperatura y humedad observadas, con indicadores derivados | En construcción |
| **Pronóstico** | Escala diaria (7–16 días) y estacional (1–3 meses) | Diseñado |
| **Medidas de adaptación** | Medidas anticipatorias codiseñadas por asociación | Fuera del alcance inicial |

Cobertura: 7 municipios en 4 departamentos.

| Municipio | Departamento | Sistemas productivos de las asociaciones |
|---|---|---|
| Los Palmitos | Sucre | Porcicultura |
| San José de Toluviejo | Sucre | Yuca, procesamiento agroalimentario |
| Icononzo | Tolima | Café |
| Planadas | Tolima | Café, aguacate |
| Fonseca | La Guajira | Café, ganadería, maíz |
| Manaure Balcón del Cesar | Cesar | Porcicultura, cacao, frutales, maderables |
| El Paso | Cesar | Gallinas ponedoras, ganadería |

El sistema debe correr desatendido y ser transferible al PMA sin compartir
contraseñas personales.

---

## 2. Principio de diseño

> **Reducir antes de almacenar, nunca almacenar antes de reducir.**

El archivo diario global de MSWEP es una matriz de 1800 × 3600 (≈6,5 millones
de celdas, ~25 MB en memoria). De todo eso se necesitan **7 puntos**.

Medido sobre un archivo real:

| Etapa | Celdas | Tamaño |
|---|---|---|
| Global | 6.480.000 | 24,7 MB |
| Recorte a los 4 departamentos | 6.960 | 27,2 KB |
| Extracción de 7 municipios | 7 | < 1 KB |

Factor de reducción: **931×**, en 0,01 s. Después del recorte, el problema de
almacenamiento deja de existir.

---

## 3. Acceso a los datos

### 3.1. La cadena de autenticación

Las carpetas de MSWEP y MSWX que GloH2O comparte están configuradas como
**públicas por enlace**. Verificado abriéndolas en una ventana sin sesión de
Google.

Eso permite la solución más simple posible:

```
Cuenta de servicio de Google Cloud  →  Drive API  →  carpetas por ID
```

Una cuenta de servicio es una identidad autenticada de Google, así que puede
leer cualquier carpeta pública por enlace sin permiso explícito.

**Credencial:** un archivo JSON, guardado como el secreto
`GCP_SERVICE_ACCOUNT_KEY` del repositorio. Se transfiere al PMA reemplazando
ese secreto.

### 3.2. Se consulta por ID, no por ruta

Las carpetas se direccionan por su identificador de Drive, no navegando por
nombres ni dependiendo de "Compartido conmigo":

| Producto | ID de la carpeta raíz |
|---|---|
| MSWEP (V2.8) | `1Kok05OPVESTpyyan7NafR-2WwuSJ4TO9` |
| MSWX | `1R1KRmldXmLj_09kzE2wKSrbXUzU3pQN9` |

Y cada archivo se busca por nombre dentro de su carpeta:

```
q = "name='2026271.nc' and '<folder_id>' in parents and trashed=false"
```

Una llamada por archivo candidato. No se paginan carpetas con decenas de miles
de archivos.

### 3.3. Convención de nombres

`AAAADDD.nc` — año y día juliano. `2026271.nc` es el 28 de septiembre de 2026.
Ordenar alfabéticamente equivale a ordenar cronológicamente.

### 3.4. Estructura

```
MSWEP_V280/
├── NRT/          ← tiempo casi real (desde 2025)
├── Past/         ← histórico con corrección por pluviómetros
└── Past_nogauge/ ← histórico sin corrección

MSWX_V100/
└── NRT/
    ├── Temp/     ← temperatura media
    ├── Tmin/
    ├── Tmax/
    └── RelHum/   ← humedad relativa
```

Cada archivo `.nc` contiene **una sola variable**. MSWEP es solo precipitación;
las demás vienen de MSWX. Son dos productos con solicitudes de acceso
independientes.

> Las rutas exactas se confirman con el workflow `00-explorar-drive`.

### 3.5. La ventana de revisión de 10 días

Instrucción explícita de GloH2O, en su correo de concesión de acceso y en la
documentación de MSWEP V3.16: los archivos de MSWEP-NRT con **menos de 10 días**
se actualizan progresivamente a medida que llegan mejores fuentes, y se
recomienda volver a descargarlos.

Secuencia de maduración:

| Antigüedad | Fuentes | Madurez |
|---|---|---|
| 0–1 día | GSMaP NRT, IMERG Early, GDAS | `preliminar` |
| 1–5 días | GSMaP Standard, IMERG Late | `intermedio` |
| > 5 días | ERA5 reemplaza a GDAS | `consolidado` |

**Consecuencia de diseño:** el pipeline no puede saltarse un archivo porque ya
exista en disco. Debe comparar la **fecha de modificación en origen** contra la
registrada. Cada dato lleva su marca de madurez, y el tablero indica cuándo un
valor todavía es provisional.

---

## 4. Flujo diario

```
1. Listar metadata          sin descargar nada
2. Comparar contra registro  ¿nuevo, o modificado en los últimos 10 días?
3. Descargar solo eso        típicamente 2–3 archivos
4. Recortar de inmediato     4 departamentos → 7 municipios
5. Calcular indicadores      en el pipeline, nunca en el navegador
6. Persistir el producto     solo lo reducido
7. Publicar                  el sitio se regenera
8. Descartar                 el runner muere con los .nc dentro
```

El dato crudo global vive **minutos** en el runner y nunca se almacena.

**No existe un mirror en Drive propio.** Se descartó porque no elimina ninguna
credencial, no cabe en una cuenta gratuita (~100 MB/día) y, sobre todo, se
desactualiza en silencio frente a las revisiones del NRT.

---

## 5. Almacenamiento

Tres lugares, tres funciones distintas.

| Capa | Dónde | Comportamiento | Crece |
|---|---|---|---|
| Datos crudos | En ningún lado | Se descartan | — |
| Serie observada | Hugging Face | Reescribible (últimos 10 días) | Sí, lento |
| Pronósticos emitidos | Hugging Face | **Solo añadir, inmutable** | Sí, constante |
| Ventana de 2 meses + pronóstico vigente | Artefacto del sitio | Se reemplaza cada día | No |
| Instantánea con DOI | Repositorio institucional (Dataverse / CGSpace) | Depósito periódico | Por depósito |

### 5.1. Por qué el pronóstico es la pieza crítica

El dato observado se puede volver a descargar de la fuente. **El pronóstico
emitido hoy no.** Cuando sale la siguiente corrida, la anterior desaparece;
nadie archiva lo que se pronosticó el 29 de septiembre para el 5 de octubre.

Sin ese archivo no se puede:

- Calibrar los umbrales de activación de los planes de respuesta anticipatoria
- Rendir cuentas de qué se anunció y cuándo
- Medir qué modelo acierta más en cada territorio

### 5.2. Por qué dos consumidores, dos capas

| Consumidor | Frecuencia | Necesita |
|---|---|---|
| El pipeline | 1 vez/día, autenticado | Escritura, acumulación |
| El navegador de cada usuario | Muchas, anónimo | CORS, sin llaves, sin límites de tasa |

Hugging Face sirve para el primero. Para el segundo tiene dos problemas: las
descargas redirigen a un host que **falla el preflight de CORS** para
peticiones con Range (adiós a leer Parquet por partes desde el navegador), y
aplica límites de tasa por IP a usuarios anónimos.

Por eso los datos que lee el portal viajan **junto al sitio**: mismo origen,
sin CORS, sin llaves, con CDN y compatibles con Service Worker.

### 5.3. Volumen

| Elemento | Tamaño |
|---|---|
| Ventana de 60 días, 7 municipios, 4 variables | ~15 KB |
| Grillas por pixel | ~80 KB |
| Pronóstico diario, varios modelos | ~40 KB |
| **Carga inicial del portal** | **~115 KB** (~30 KB comprimidos) |
| Partición mensual archivada | ~20 KB |
| Archivo de pronósticos, un año | ~15 MB |

No se borra nada. El límite de dos meses es una decisión de **carga**, no de
retención.

---

## 6. Modelo de datos

### 6.1. Observado — reescribible

Clave: `fecha` + `municipio` + `variable` + `fuente`.

| Campo | Ejemplo |
|---|---|
| `fecha` | 2026-09-28 |
| `municipio` | los_palmitos |
| `variable` | precipitacion |
| `valor` | 12.4 |
| `fuente` | mswep_v280_nrt |
| `madurez` | preliminar / intermedio / consolidado |
| `actualizado` | 2026-09-29T11:04Z |

`fuente` va **en la clave**, para que MSWEP y CHIRPS puedan coexistir en la
misma fecha y compararse antes de decidir cuál queda como primaria.

### 6.2. Pronóstico — inmutable, solo añadir

Clave: `emision` + `objetivo` + `municipio` + `variable` + `modelo`.

| Campo | Ejemplo |
|---|---|
| `emision` | 2026-09-29T06:00Z |
| `objetivo` | 2026-10-05 |
| `municipio` | planadas |
| `variable` | precipitacion |
| `modelo` | ecmwf_ifs |
| `valor` | 8.2 |
| `horizonte` | 6 |

Un registro emitido **nunca** se modifica ni se borra. Esa inmutabilidad es lo
que hace posible la verificación.

### 6.3. Estado — el cerebro del pipeline

- `ingesta.parquet`: por archivo de origen, su fecha de modificación, cuándo se
  procesó y si salió bien. Es lo que permite descargar 2 archivos en vez de 60.
- `umbrales.json`: P80 de Tmax y Tmin por municipio y umbral de HR por sistema
  productivo. Se calcula una vez y se congela, por reproducibilidad.

### 6.4. Formatos

Parquet para acumular y analizar (tipado fuerte, compresión por columna).
JSON para lo que lee el navegador, porque el problema de CORS impide leer
Parquet por partes desde el cliente.

---

## 7. Indicadores

Todos se calculan **en el pipeline**. El frontend solo dibuja.

| Indicador | Definición | Origen del umbral |
|---|---|---|
| Día seco | Precipitación < 1 mm | Convención |
| Día con lluvia | Precipitación ≥ 1 mm | Convención |
| Racha seca | Días secos consecutivos | Pizarra de diseño |
| Acumulado | Suma en la ventana | Pizarra de diseño |
| Día cálido | Tmax > P80 local | Sección 1.2 del Reporte 1 (AgERA5) |
| Ola de calor | ≥ 3 días consecutivos Tmax > P80 | Sección 1.2 del Reporte 1 |
| Noche cálida | Tmin > P80 de Tmin | Derivado |
| Día de humedad alta | HR > umbral del sistema productivo | Provisional |

Ventanas de referencia: ayer, últimos 7, 14 y 30 días.

### 7.1. Umbrales provisionales heredados

Del tablero prototipo, marcados en el código como valores de ejemplo
pendientes de análisis agronómico:

| Cultivo | Tmax (°C) | HR (%) |
|---|---|---|
| Algodón | 36 | 78 |
| Palma de aceite | 36 | 88 |
| Arroz | 35 | 85 |
| Yuca | 34 | 83 |
| Maíz | 33 | 80 |
| Ajonjolí | 33 | 78 |
| Ganadería | 33 | 80 |
| Cultivos de pancoger | 33 | 82 |
| Ñame | 32 | 82 |
| Soya | 32 | 80 |
| Cebolla de bulbo | 30 | 80 |
| Fríjol | 30 | 80 |
| Café | 28 | 75 |

**Brecha identificada:** la lista viene de las fichas departamentales del
Ministerio de Agricultura, no de los sistemas productivos reales de las
asociaciones. Faltan **porcicultura, gallinas ponedoras, cacao, frutales,
maderables y aguacate**, que son precisamente aquellos sobre los que giran las
medidas anticipatorias codiseñadas.

**Mejora pendiente:** para sistemas pecuarios, el estrés calórico no se mide
bien con temperatura y humedad por separado. 33 °C con 60 % de humedad y 33 °C
con 85 % son situaciones muy distintas para un animal. Corresponde un índice
combinado, calculable con los datos que ya se tienen.

---

## 8. Fuentes

### 8.1. Monitoreo

| Variable | Fuente primaria | Respaldo |
|---|---|---|
| Precipitación | MSWEP NRT | CHIRPS (HTTP plano, 0,05°) |
| Tmax, Tmin, Temp | MSWX NRT | AgERA5 / Open-Meteo |
| Humedad relativa | MSWX NRT | AgERA5 / Open-Meteo |

### 8.2. Pronóstico

| Escala | Modelos | Acceso |
|---|---|---|
| Diaria (7 d) | WRF-IDEAM | Portal IDEAM (scraping) |
| Diaria (16 d) | GFS, GEFS, IFS/ENS, AIFS, ICON, GEM/GEPS | Open-Meteo, sin llave |
| Estacional | NMME | IRI Data Library (OPeNDAP) |
| Estacional | SEAS5 | Open-Meteo |
| Estacional | C3S | Copernicus CDS (llave gratuita) |
| Estacional | Predicción climática IDEAM | Portal IDEAM |

La escala subestacional (2–6 semanas) **queda fuera de la v1**, en línea con el
diseño original y con lo que el propio Reporte 1 señala: es la escala de menor
predictibilidad y la menos desarrollada operativamente en el IDEAM.

MSWX también ofrece ensambles de pronóstico (30 miembros a 10 días desde GEFS,
51 miembros a 7 meses desde SEAS5), armonizados con lo observado. Es la única
fuente gratuita que da una cadena observación-pronóstico sin costura.

### 8.3. Contrato de fuentes

Cada adaptador, sea Drive o HTTP, devuelve lo mismo:

```
fecha | municipio | variable | valor | fuente
```

Todo lo que está aguas abajo es idéntico. MSWEP puede entrar, salir o ser
reemplazado sin tocar indicadores, almacenamiento ni tablero.

---

## 9. Calendario de ejecución

Colombia es UTC−5 sin horario de verano.

| Workflow | Cron UTC | COT | Qué hace |
|---|---|---|---|
| `00-explorar-drive` | manual | — | Diagnóstico de acceso y mapeo de IDs |
| `01-diario` | `0 11 * * *` | 06:00 | Monitoreo + pronóstico diario |
| `02-ideam` | `30 11 * * *` | 06:30 | WRF-IDEAM, aislado para que su caída no tumbe nada |
| `03-estacional` | `0 12 15 * *` | 07:00 día 15 | NMME, SEAS5, IDEAM |
| `05-cierre-mensual` | `0 6 1 * *` | 01:00 día 1 | Cierra partición, poda ventanas |
| `90-deploy-pages` | al hacer push | — | Publica |

Horarios escalonados a propósito: dos workflows escribiendo a la vez generan
conflictos.

---

## 10. Reglas de operación

**Idempotencia.** Cada corrida reconstruye la ventana completa desde cero, sin
*append*. Si un día falla, al siguiente se autocorrige. Sin esto, un fallo deja
un hueco permanente.

**Degradación controlada.** Si una fuente cae, el pipeline no aborta: usa el
respaldo, marca en el dato qué fuente se usó realmente y sigue. El portal nunca
queda en blanco.

**Heartbeat obligatorio.** En repositorios públicos, los workflows programados
se desactivan cuando no hay actividad durante 60 días. Un pipeline que solo
hace commit *cuando hay datos nuevos* puede volverse un no-op y apagarse en
silencio. Por eso el registro de última ejecución se escribe **siempre**.

**Nunca datos simulados sin avisar.** El tablero prototipo cae silenciosamente
en datos sintéticos si no encuentra su archivo de datos, mostrando un
distintivo de "Actualizado". Si no hay datos, el portal debe decirlo.

**Escritura atómica.** Se escribe a un temporal y se reemplaza. Si el proceso
muere a mitad, no queda un archivo corrupto que mañana se dé por bueno.

---

## 11. Credenciales

| Secreto | Para qué | Cómo se renueva |
|---|---|---|
| `GCP_SERVICE_ACCOUNT_KEY` | Leer MSWEP y MSWX en Drive | Nueva clave JSON en Google Cloud |
| `HF_TOKEN` | Escribir en el dataset | Nuevo token en Hugging Face |
| `CDS_API_KEY` | AgERA5 (una sola vez) | Registro gratuito en Copernicus |

Ninguna depende de una cuenta personal ni expira sola.

---

## 12. Registro de decisiones

Se documentan los caminos descartados, porque sin esto alguien los volverá a
intentar.

| Decisión | Por qué se descartó |
|---|---|
| **Mirror de MSWEP en Drive propio** | No elimina ninguna credencial; ~100 MB/día no caben en una cuenta gratuita; se desactualiza frente a las revisiones del NRT |
| **rclone** | Su `client_id` compartido será retirado durante 2026. La Drive API en Python funciona igual de bien |
| **OAuth de usuario con credenciales propias** | `drive.readonly` es un scope **restringido**: en modo Prueba el token muere a los 7 días; en Producción exige verificación y una evaluación CASA anual (USD 5.000–20.000) |
| **Consentimiento Interno / delegación de dominio** | Requiere Google Workspace. La cuenta del proyecto es Gmail y la Alianza usa Microsoft 365 |
| **Scope `drive.file`** | Solo ve archivos creados por la propia aplicación. Los de GloH2O los creó GloH2O |
| **Espejo del ICDC (U. Hamburgo)** | Acceso restringido y ~1 mes de rezago. Útil para histórico, no para NRT |
| **Espejo del NCI (Australia)** | Solo 1979–2020, requiere cuenta institucional australiana |
| **Google Earth Engine** | MSWEP no está publicado ahí |
| **API/FTP de GloH2O** | Reservados a usuarios comerciales |
| **Git como archivo de largo plazo** | El historial crece sin poda posible y GitHub no tiene mandato de preservación |
| **Cloudflare R2, Backblaze B2** | Exigen tarjeta de crédito |
| **Supabase** | Pausa proyectos inactivos |
| **Escala subestacional en la v1** | Baja predictibilidad y escaso desarrollo operativo |

---

## 13. Pendientes

### Bloqueantes

- [ ] Correr `00-explorar-drive` y confirmar los IDs de las carpetas terminales
- [ ] Calcular el P80 de Tmax y Tmin por municipio desde AgERA5
- [ ] Definir el umbral de HR para porcicultura, gallinas ponedoras, cacao,
      frutales, maderables y aguacate
- [ ] Decidir cuántos modelos de pronóstico archivar desde el día uno

### No bloqueantes

- [ ] Evaluar MSWEP V3.16 frente a V2.8 con datos propios
- [ ] Índice combinado de temperatura y humedad para sistemas pecuarios
- [ ] Resolver cuál repositorio es el canónico: el Reporte 1 cita
      `alliance-datascience/PMA-nino-Colombia`, el prototipo vive en
      `aamaya03/PMA_Dashboard` y el entregable es `pmaclimafap/PMA_nino`
- [ ] Gestionar el depósito en Dataverse / CGSpace de la Alianza
- [ ] Documentar la licencia CC BY-NC 4.0 de MSWEP y MSWX, y confirmar por
      escrito con GloH2O que los productos derivados pueden publicarse
- [ ] Módulos de monitoreo comunitario y medidas de adaptación
