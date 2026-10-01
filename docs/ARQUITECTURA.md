# Arquitectura del sistema de monitoreo y pronóstico agroclimático

Proyecto: *Fortalecimiento de la resiliencia de los sistemas agroalimentarios
frente al Fenómeno de El Niño* — Alianza Bioversity-CIAT / Programa Mundial de
Alimentos.

| | |
|---|---|
| Código | https://github.com/pmaclimafap/PMA_nino |
| Datos | https://huggingface.co/datasets/pmaclimafap/monitoreo-nino-colombia |
| Tablero | https://pmaclimafap.github.io/PMA_nino/ |
| Última actualización | 1 de octubre de 2026 |

> Este documento describe el sistema **tal como opera**, no como se planeó. Las
> secciones marcadas con «verificado» incluyen mediciones de corridas reales.

---

## 1. Estado actual

**El módulo de monitoreo está en producción y corre desatendido.** Verificado:
la corrida programada del 1 de octubre se ejecutó sola, procesó el día nuevo y
actualizó el tablero sin intervención.

| Módulo | Estado |
|---|---|
| Monitoreo satelital | **En producción** |
| Índice ENSO (ONI) | **En producción** |
| Tablero web | **En producción** |
| Pronóstico diario y estacional | Diseñado, sin implementar |
| Monitoreo comunitario | Fuera del alcance actual |
| Medidas de adaptación | Fuera del alcance actual |

Cobertura: 7 municipios en 4 departamentos.

| id | Municipio | Departamento | Sistemas productivos |
|---|---|---|---|
| `los_palmitos` | Los Palmitos | Sucre | Porcicultura |
| `san_jose_de_toluviejo` | San José de Toluviejo | Sucre | Yuca, procesamiento |
| `icononzo` | Icononzo | Tolima | Café |
| `planadas` | Planadas | Tolima | Café, aguacate |
| `fonseca` | Fonseca | La Guajira | Café, ganadería, maíz |
| `manaure` | Manaure Balcón del Cesar | Cesar | Porcicultura, cacao, frutales |
| `el_paso` | El Paso | Cesar | Gallinas ponedoras, ganadería |

---

## 2. Principio de diseño

> **Reducir antes de almacenar, nunca almacenar antes de reducir.**

**Verificado** sobre un archivo real de MSWEP:

| Etapa | Celdas | Tamaño | Tiempo |
|---|---|---|---|
| Global (1800 × 3600) | 6.480.000 | 24,7 MB | — |
| Recorte a 4 departamentos | 8.400 | 32,8 KB | 0,004 s |
| Extracción de 7 puntos | 7 | < 1 KB | 0,006 s |

Reducción de ~930 veces. Después del recorte, el almacenamiento deja de ser un
problema de ingeniería.

---

## 3. Acceso a los datos

### 3.1. Cadena de autenticación — verificado

Las carpetas que GloH2O comparte están configuradas como **públicas por
enlace** (comprobado abriéndolas sin sesión de Google). Eso habilita la
solución más simple posible:

```
Cuenta de servicio de Google Cloud → Drive API → carpetas por ID
```

Una cuenta de servicio es una identidad autenticada de Google, así que puede
leer cualquier carpeta pública por enlace sin permiso explícito. **No requiere
que GloH2O comparta nada con ella.**

Credencial: un JSON, en el secreto `GCP_SERVICE_ACCOUNT_KEY`. Se transfiere al
PMA reemplazando ese secreto.

### 3.2. Se consulta por ID, no por ruta

| Producto | ID de la carpeta raíz |
|---|---|
| MSWEP (V2.8) | `1Kok05OPVESTpyyan7NafR-2WwuSJ4TO9` |
| MSWX (V1.0) | `1R1KRmldXmLj_09kzE2wKSrbXUzU3pQN9` |

Cada archivo se busca por nombre dentro de su carpeta:

```
q = "name='2026271.nc' and '<folder_id>' in parents and trashed=false"
```

Una llamada por candidato. **No se paginan** carpetas con decenas de miles de
archivos. Direccionar por ID también evita depender de que la carpeta aparezca
en «Compartido conmigo», que fue el motivo de que `rclone lsd` saliera vacío.

Los IDs de las carpetas terminales están en `pipeline/config/fuentes.yml`, y se
redescubren con el workflow `00-explorar-drive`.

### 3.3. Convención de nombres

`AAAADDD.nc` — año y día juliano. `2026271.nc` es el 28 de septiembre de 2026.
Ordenar alfabéticamente equivale a ordenar cronológicamente, lo que permite
pedir los más recientes sin recorrer la carpeta.

### 3.4. Estructura — verificado

```
MSWEP_V280/{NRT, Past, Past_nogauge}/{3hourly, Daily, Monthly}

MSWX_V100/
├── NRT/{P, Temp, Tmin, Tmax, RelHum, SpecHum, Pres, Wind, SWd, LWd}/
│         └── {3hourly, Daily, Monthly}
├── Past/   igual
├── Mid/<Variable>/<AAAAMMDD_HH>/<miembro 01-30>/{Daily, 3hourly}
└── Long/
```

Detalles encontrados en la exploración:

- **Tmax y Tmin NO tienen carpeta `3hourly`**, solo `Daily` y `Monthly`. Un
  código que asuma la misma estructura para las cinco variables falla ahí.
- Cada `.nc` contiene **una sola variable**. MSWEP es solo precipitación.
- MSWX trae su propia precipitación (`NRT/P/`), útil como control cruzado
  independiente de MSWEP.
- Las ramas de pronóstico (`Mid`, `Long`) tienen una carpeta por fecha de
  inicialización y otra por miembro: decenas de miles. **No se recorren.**
  Inicializaciones observadas: desde `20000101_00`, o sea que hay
  retropronósticos desde el año 2000, lo que permite evaluar habilidad
  predictiva sin esperar años de operación.
- `MSWX_V100/Long` apareció sin subcarpetas, lo que no cuadra con la
  documentación. Pendiente de verificar.

### 3.5. La ventana de revisión de 10 días — CONFIRMADA en operación

GloH2O indica que los archivos de MSWEP-NRT con menos de 10 días se actualizan
progresivamente:

| Antigüedad | Fuentes | Madurez |
|---|---|---|
| 0–1 día | GSMaP NRT, IMERG Early, GDAS | `preliminar` |
| 1–5 días | GSMaP Standard, IMERG Late | `intermedio` |
| > 5 días | ERA5 reemplaza a GDAS | `consolidado` |

> **Corrección respecto a la versión anterior de este documento.** Tras
> observar 5 días de fechas de modificación, se concluyó que la rama NRT de la
> V2.8 publicaba y congelaba. **Era una muestra demasiado corta y la conclusión
> era errónea.** El 1 de octubre el pipeline detectó que `2026268.nc`
> (25 de septiembre, precipitación) había cambiado su fecha de modificación en
> origen. GloH2O **sí revisa** los archivos NRT de la V2.8, y la ventana de 10
> días hace trabajo real, no es un seguro teórico.

**Consecuencia de diseño:** el pipeline no puede saltarse un archivo porque ya
exista. Compara la **fecha de modificación en origen** contra la registrada.

### 3.6. Cuota de descarga de Drive — límite operativo real

Google limita las descargas **por archivo** en carpetas compartidas, y la de
GloH2O la usan miles de personas, así que la cuota se consume de forma
colectiva. El error es:

```
403 downloadQuotaExceeded — "The download quota for this file has been exceeded."
```

Se restablece en unas 24 horas. **Reintentar dentro de la misma corrida es
inútil**, por lo que el pipeline lo distingue de otros fallos
(`gloh2o.CuotaAgotada`), lo registra y espera 20 horas antes de volver a
intentarlo.

**Se agota reprocesando, no operando.** En régimen normal se descargan 5
archivos diarios, muy lejos del límite. Se alcanzó el 1 de octubre tras bajar
los 300 archivos de la ventana dos veces en un día. **Tratar el reprocesamiento
completo como operación excepcional.**

---

## 4. Flujo diario

```
1. Listar metadata            sin descargar nada
2. Comparar contra ingesta    ¿nuevo, revisado, o de versión anterior?
3. Descargar solo eso         2 a 5 archivos en régimen normal
4. Recortar de inmediato      4 departamentos → 7 municipios + grilla
5. Guardar en Hugging Face    puntos, grilla, ingesta, ONI, bitácora
6. Reconstruir y publicar     ventana de 60 días + indicadores
7. Descartar                  el runner muere con los .nc dentro
```

El dato crudo global vive **minutos** en el runner y nunca se almacena. **No
existe un mirror en Drive propio**; ver el registro de decisiones.

### 4.1. Versionado del extractor

`estado/ingesta.parquet` guarda **con qué versión** del extractor se procesó
cada archivo. Un registro con versión anterior se reprocesa aunque ya exista.

```
VERSION_EXTRACCION = 2    # 1 = solo puntos; 2 = puntos + grilla
```

Sin esto, el registro solo sabría *que* procesó un archivo y no *qué* extrajo:
al añadir la grilla, los 300 archivos ya estaban marcados como hechos y la
grilla nunca se habría extraído. Para cualquier cambio futuro en lo que se
extrae, basta subir el número.

### 4.2. Rendimiento — verificado

| Escenario | Archivos | Resultado | Tiempo |
|---|---|---|---|
| Arranque en frío (60 días) | 300 | 2.100 filas | 7 min 29 s |
| Reproceso con grilla | 300 | 2.100 filas + 14.760 celdas | 7 min 29 s |
| Régimen normal | 2–5 | — | ~30 s |
| Sin novedades | 0 | republica igual | ~20 s |

En dos meses de las cinco variables **no faltó ningún dato**: 2.100 filas
exactas (60 × 5 × 7).

---

## 5. Almacenamiento

Tres semánticas distintas, y la carpeta las hace explícitas.

| Capa | Dónde | Comportamiento | ¿Se puede perder? |
|---|---|---|---|
| Datos crudos | En ningún lado | Se descartan | Sí, se redescargan |
| `observado/` | Hugging Face | Reescribible, un archivo por mes | Sí, de la fuente |
| `observado_grilla/` | Hugging Face | Reescribible, por mes | Sí |
| `pronostico/` | Hugging Face | **Inmutable, solo añadir** | **No: irrecuperable** |
| `derivado/` | Hugging Face | Recalculable | Sí, se regenera |
| `estado/` | Hugging Face | Estado del pipeline | Sí, obliga a reprocesar |
| Ventana publicada | Artefacto de Pages | Se reemplaza cada día | Sí |
| Instantánea con DOI | Repositorio institucional | Por depósito | Pendiente |

### 5.1. Estructura del dataset

```
pmaclimafap/monitoreo-nino-colombia/
├── README.md                              tarjeta, licencia, atribución
├── observado/AAAA-MM.parquet
├── observado_grilla/AAAA-MM.parquet
├── pronostico/
│   ├── diario/AAAA/AAAA-MM-DD.parquet     una emisión = un archivo
│   └── estacional/AAAA/AAAA-MM.parquet
├── derivado/verificacion/AAAA.parquet
└── estado/
    ├── ingesta.parquet                    qué se procesó y con qué versión
    ├── oni.parquet                        serie del índice ENSO
    └── bitacora.parquet                   una fila por corrida
```

### 5.2. Por qué el pronóstico es la pieza crítica

El dato observado se puede volver a descargar. **El pronóstico emitido hoy no.**
Cuando sale la siguiente corrida, la anterior desaparece de la fuente.

Sin ese archivo no se puede calibrar los umbrales de activación de los planes
de respuesta anticipatoria, ni rendir cuentas de qué se anunció y cuándo, ni
medir qué modelo acierta en cada territorio.

Por eso los pronósticos van **un archivo por fecha de emisión**, no por mes: si
una escritura falla, se pierde un día en lugar de un mes de historia
irrecuperable. Y `guardar_pronostico_diario` **se niega a reescribir** un
archivo existente salvo que una persona pase `forzar=True`.

### 5.3. Por qué dos consumidores, dos capas

| Consumidor | Frecuencia | Necesita |
|---|---|---|
| El pipeline | 1 vez/día, autenticado | Escritura, acumulación |
| El navegador | Muchas, anónimo | CORS, sin llaves, sin límites de tasa |

Hugging Face sirve para el primero. Para el segundo tiene dos problemas: las
descargas redirigen a un host que **falla el preflight de CORS** para
peticiones con Range (lo que impide leer Parquet por partes desde el
navegador), y aplica límites de tasa por IP a usuarios anónimos.

Por eso lo que lee el tablero viaja **junto al sitio**: mismo origen, sin CORS,
sin llaves, con CDN.

### 5.4. Volumen — verificado

| Elemento | Tamaño |
|---|---|
| Puntos, 60 días | 2.100 filas |
| Grilla, 60 días, 3 variables | 14.760 celdas (82 por día y variable) |
| `data.json` publicado | 163 KB |
| `ventana-AAAAMMDD.json` | 89 KB |
| Serie ONI completa (1950–hoy) | ~900 filas |

La grilla **no es opcional**: sin ella el tablero genera una simulada y muestra
variación espacial inventada dentro del municipio.

### 5.5. Clave del observado

`fecha` + `municipio` + `variable` + **`fuente`**

`fuente` va en la clave a propósito: permite que MSWEP y CHIRPS coexistan para
la misma fecha y municipio, y compararlos antes de elegir primaria. Sin ella,
una fuente pisaría a la otra. La fusión usa `keep="last"`, para que un dato
revisado reemplace al preliminar.

---

## 6. Indicadores

Todos se calculan **en el pipeline**. El frontend solo dibuja: así el indicador
es el mismo para quien mira el tablero, quien consulta el archivo y quien
audita el cálculo.

| Indicador | Definición |
|---|---|
| Día seco / con lluvia | < 1 mm / ≥ 1 mm |
| Racha seca máxima | Días secos consecutivos, el tramo más largo |
| **Racha seca actual** | Días secos consecutivos **al día de hoy** |
| Acumulado y categoría | Suma en la ventana, con escala según su duración |
| Día de calor | Tmax > umbral del sistema productivo |
| **Ola de calor** | 3 o más días consecutivos sobre ese umbral |
| Día de humedad alta | HR > umbral del sistema productivo |
| ITH | Índice combinado de temperatura y humedad, pecuarios |

Ventanas: ayer, últimos 7, 14 y 30 días. **La ventana se cuenta desde la última
fecha con datos, no desde hoy**: si el NRT tiene dos días de rezago, «última
semana» debe seguir siendo siete días de datos.

Hay racha máxima y racha actual porque al productor le importa la segunda: no
cuál fue el período seco más largo del mes, sino cuántos días lleva sin llover
hoy. Esa es la que dispara una decisión de riego.

### 6.1. Umbrales: decisión de método

**Los umbrales se definen con conocimiento local, no estadísticamente.**

Se consideró y **descartó** calcular un percentil 80 climatológico por punto
(la metodología de la sección 1.2 del Reporte 1). Un percentil dice si un día
fue inusual *para el lugar*; un umbral agronómico dice si *le hizo daño* al
cultivo o al animal. Para activar medidas anticipatorias sirve lo segundo.

> **Consecuencia:** la definición de «ola de calor» del tablero NO es
> comparable con la de los mapas de riesgo del Reporte 1, que usan el P80
> climatológico. Son dos preguntas distintas y no deben mezclarse en una misma
> figura ni en un mismo texto.

Los 21 umbrales de `pipeline/config/umbrales.yml` llevan un campo `origen`:

| origen | Significado |
|---|---|
| `heredado` | Del tablero prototipo, marcado allí como ejemplo |
| `propuesto` | Agregado aquí porque el sistema productivo faltaba |
| `local` | **Validado con técnicos o asociaciones** ← objetivo |

**Hoy los 21 están sin validar.** Mientras eso no cambie, los indicadores de
calor son ilustrativos, no accionables.

El prototipo no incluía los sistemas productivos reales de las asociaciones:
faltaban porcicultura, gallinas ponedoras, cacao, frutales, maderables y
aguacate, que son precisamente aquellos sobre los que giran las medidas
anticipatorias codiseñadas.

### 6.2. El ITH y por qué importa

Para sistemas pecuarios, evaluar temperatura y humedad por separado es
engañoso: 33 °C con 60 % de HR y 33 °C con 85 % son situaciones muy distintas
para un animal, porque la humedad impide disipar calor.

**Verificado** con datos reales del 25 al 29 de septiembre en El Paso (gallinas
ponedoras):

| Lectura | Resultado |
|---|---|
| Por temperatura sola | 3 de 5 días sobre umbral, **0 olas de calor** |
| Por ITH | ITH máximo 90,2 → **3 días en categoría de emergencia** |

Leída por temperatura, la semana parece tolerable. Con el índice combinado,
hubo tres días en emergencia. La diferencia la hace la humedad, por encima del
79 % los cinco días. Y conecta con un hecho del reporte: ASOINREPA perdió el
20 % de la producción de huevos por altas temperaturas en 2026.

La formulación y los cortes están marcados como `propuesto_sin_validar`:
pendiente de revisión con un zootecnista.

---

## 7. Control de calidad

MSWX corrige el sesgo de cada variable **por separado**, así que la coherencia
entre ellas no está garantizada. El pipeline **marca** las anomalías; no las
corrige ni las descarta en silencio.

### 7.1. Dos niveles, y la razón

| Nivel | Qué es | ¿Enciende la bandera? |
|---|---|---|
| **Grave** | Dato físicamente imposible o fuera de rango | Sí |
| **Sesgo** | Amplitud térmica más baja de lo esperable | No, se reporta como proporción |

La primera versión mezclaba ambos y encendía la alerta si *algún* día de la
ventana tenía amplitud baja. Sobre 60 días eso ocurre casi siempre: **6 de 7
municipios quedaban marcados**, y una bandera que se enciende en el 86 % de los
casos no informa nada. Tras separarlos: **2 de 7**.

### 7.2. Hallazgos verificados

**Incoherencia térmica.** Manaure, 26 de septiembre: Tmin 15,9 · Temp 16,9 ·
Tmax 16,2. La media por encima del máximo. Una en 35 combinaciones.

**Amplitud térmica comprimida.** Entre el 23 % y el 43 % de los días según el
municipio tienen amplitud bajo 2 °C, donde en zonas tropicales secas se
esperarían 8 a 12 °C. Coincide con el sesgo documentado de MSWX, que
**subestima Tmax y sobrestima Tmin**.

Los municipios más afectados son San José de Toluviejo (40 %) y Planadas
(38 %): los primeros a priorizar si se valida contra estaciones del IDEAM.

> **Riesgo para el proyecto.** El indicador central del tablero cuenta días por
> encima de un umbral de Tmax. Si Tmax viene sistemáticamente baja, **se
> subcuentan los días de calor**, justo el indicador que debe activar las
> medidas anticipatorias. Pendiente de validar contra estaciones del IDEAM.

---

## 8. Índice ENSO (ONI)

| | |
|---|---|
| Fuente | `https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt` |
| Formato | Texto tabulado, un trimestre solapado por fila desde 1950 |
| Cadencia | NOAA actualiza hacia el **día 5 de cada mes** |
| Madurez | **Los valores se revisan hasta 2 meses después de publicarse** |

El pipeline lee la tabla completa en cada corrida y toma la última fila. No
compara ni busca filas nuevas: al releer todo, una corrección a un trimestre
anterior entra sin lógica especial.

**Respaldo.** Si la NOAA no responde, cae a la serie archivada en
`estado/oni.parquet` y lo declara como «valor archivado». El ONI es **mensual**,
así que el valor del trimestre más reciente sigue siendo cierto aunque el
servidor tenga un fallo de treinta segundos. Blanquear el panel por eso sería
desproporcionado. Si no hay fuente ni archivo, muestra «sin dato»: un índice
equivocado es peor que ninguno cuando se comunica si viene o no una sequía.

**Revisiones.** Al archivar la serie, el pipeline detecta cuándo la NOAA cambia
un valor y lo reporta. NOAA sobrescribe su tabla y nadie conserva la versión
anterior; es la misma lógica que aplicamos al pronóstico.

### 8.1. Estado del evento — verificado

ONI de junio-julio-agosto de 2026: **+1,80 °C, El Niño fuerte.**

| Puesto | Trimestre | ONI |
|---|---|---|
| **1** | **JJA 2026** | **+1,80** |
| 2 | JJA 1997 | +1,48 |
| 3 | JJA 2015 | +1,44 |
| 4 | JJA 1987 | +1,30 |

Es el **junio-julio-agosto más cálido de los 77 del registro desde 1950**, por
encima de los dos eventos que el propio Reporte 1 usa como análogos. Respalda
con un número la afirmación de que este es uno de los episodios más intensos
desde 1950.

### 8.2. Índice oficial: el ONI ya no lo es

Según la declaración pública **26-05 del NWS**, el índice usado para el
monitoreo y predicción oficial del ENSO es el **RONI** (Relative Oceanic Niño
Index), que resta la anomalía media del cinturón tropical para aislar el calor
del Pacífico del calentamiento general del océano.

Se conserva el ONI porque tiene endpoint estable en texto plano con serie
continua desde 1950, mientras el RONI solo se publica como tabla HTML.
**Pendiente de añadir**; es algo que el PMA podría preguntar.

---

## 9. Tablero

Migrado del prototipo `aamaya03/PMA_Dashboard`, que ya estaba validado. En
lugar de reescribirlo, el pipeline **emite `data.json` en el esquema que ese
tablero espera**, con los 7 nombres de municipio y las 8 claves por municipio.

### 9.1. Los tres arreglos de la migración

**El respaldo silencioso, el más importante.** Si no encontraba `data.json`,
generaba datos simulados avisando **solo por consola** y seguía mostrando el
sello de «Actualizado». Una simulación era indistinguible de la realidad para
quien la mirara, y ese enlace se comparte con el PMA. Ahora muestra un aviso
explícito y oculta los controles. El generador quedó accesible con `?demo=1`,
y en ese modo el sello dice «DEMO — datos simulados».

**El ENSO rompía el arranque.** `pintarEnso` llamaba a `e.valor.toFixed(1)` sin
guarda; con valor nulo lanzaba excepción antes de dibujar nada.

**Asumía que el primer municipio tenía datos.** Leía
`datos.municipios[MUNICIPIOS[0]].precip.slice(-1)[0].date` directamente. Si
fallaba la fuente para Los Palmitos, reventaba el tablero entero.

### 9.2. Pendiente de mostrar

El pipeline ya calcula y el frontend ignora: **la madurez del dato, el ITH y
las banderas de calidad.** Conviene tocar el frontend una sola vez, cuando
exista el pronóstico, para no rehacer el layout dos veces.

---

## 10. Workflows

Colombia es UTC−5 sin horario de verano.

| Workflow | Disparo | Qué hace |
|---|---|---|
| `00-explorar-drive` | Manual | Diagnóstico de acceso y mapeo de IDs |
| `01-probar-extraccion` | Manual | Prueba el flujo sin escribir nada |
| `03-diario` | `0 5 * * *` + manual | Monitoreo completo |
| `90-publicar` | Al terminar `03` + manual | Despliega el sitio |

### 10.1. El cron sale de una medición

Los archivos de GloH2O se publican entre las **01:33 y las 01:38 UTC**, con
regularidad de reloj, en este orden: MSWEP, Temp, Tmin, Tmax, RelHum. La
corrida a las **05:00 UTC** deja 3,5 horas de margen y equivale a medianoche en
Colombia, así que el tablero amanece actualizado.

Los cron de GitHub no son puntuales: la cola es compartida y los retrasos de 5
a 30 minutos son normales. Con 3,5 horas de margen no importa.

### 10.2. Publicación desacoplada

El sitio se despliega desde un **artefacto de build**, sin hacer commit y sin
rama de datos: nada entra a git y el historial no crece con una corrida diaria.

Al lanzarse a mano busca el artefacto de la **última corrida exitosa** del
diario. Antes solo miraba la corrida que lo había disparado, así que publicar
un ajuste de CSS dejaba el sitio sin datos y obligaba a correr el pipeline
completo.

Requisito de una sola vez: en Settings → Pages, *Source* = **GitHub Actions**.

### 10.3. Política de códigos de salida

**Un archivo fallido no tumba la corrida.** Antes cualquier error devolvía
código 1, y eso bloqueaba la publicación (que exige `conclusion == success`):
un archivo de 300 impedía publicar 60 días de datos buenos. Ahora los errores
parciales salen como advertencias visibles y la corrida termina bien. Solo falla
de verdad si no hay nada que publicar.

El detalle de cada error va al resumen del job, no solo al `manifest.json`: si
no se ve ahí, nadie sabe qué archivo falló ni por qué.

---

## 11. Reglas de operación

**Idempotencia.** La ventana se reconstruye desde el almacenamiento, no desde
lo descargado hoy. Si una corrida falla, la siguiente produce lo mismo.
**Verificado:** una corrida con 0 descargas publicó los mismos 163 KB.

**Degradación controlada.** Si una fuente cae, el pipeline usa el respaldo,
marca qué fuente se usó realmente y sigue.

**Heartbeat obligatorio.** En repositorios públicos GitHub desactiva los
workflows programados tras **60 días sin actividad**. La bitácora se escribe
siempre, con o sin datos nuevos: eso cuenta como actividad y mantiene el cron
vivo. Es la protección contra que el sistema muera en silencio.

**Escritura atómica.** Se escribe a un temporal y se reemplaza. Si el proceso
muere a mitad, no queda un archivo corrupto que mañana se dé por bueno. Además
se verifica el tamaño contra el que reporta Drive: un archivo truncado se
rechaza en lugar de fallar después con un error confuso de NetCDF.

**Nunca datos simulados sin avisar.** Si no hay datos, el portal lo dice.

---

## 12. Credenciales

| Secreto | Para qué | Cómo se renueva |
|---|---|---|
| `GCP_SERVICE_ACCOUNT_KEY` | Leer MSWEP y MSWX en Drive | Nueva clave JSON en Google Cloud |
| `HF_TOKEN` | Escribir en el dataset | Nuevo token *fine-grained* en Hugging Face |

Ninguna expira sola ni depende de un token OAuth de usuario.

**Para la entrega:** la cuenta `pmaclimafap@gmail.com` es el punto único de
falla de todo el sistema. Los **códigos de recuperación de 2FA** de Gmail y de
Hugging Face deben formar parte del entregable: si el PMA intenta entrar desde
otro país y otro dispositivo, Google o HF pueden pedir verificación.

---

## 13. Licencia y publicación

Los datos derivan de **MSWEP y MSWX, bajo CC BY-NC 4.0**. La restricción se
hereda: **el dataset no puede usarse con fines comerciales**, y exige la
atribución a Beck et al. que está en el `README.md` del dataset.

Conviene decirlo explícitamente al compartirlo, no dar por sentado que se lea
el README.

> **Pendiente de revisar.** El perfil de la cuenta de Hugging Face se muestra
> como «Programa Mundial de Alimentos». Un dataset público bajo ese nombre se
> lee como publicación oficial de una agencia de Naciones Unidas, y eso
> normalmente pasa por aprobación institucional. La cuenta la administra el
> equipo técnico, no el PMA.

> **Privacidad, a futuro.** Cuando entre el monitoreo comunitario habrá
> coordenadas de fincas de firmantes de paz en territorios como Planadas o
> Conejo. Eso no debe quedar público: lo satelital público, lo comunitario
> privado o agregado.

---

## 14. Registro de decisiones

Se documentan los caminos descartados, porque sin esto alguien los volverá a
intentar.

| Decisión | Por qué se descartó |
|---|---|
| **Mirror de MSWEP en Drive propio** | No elimina ninguna credencial (algo tiene que leer el Drive de GloH2O); ~100 MB/día no caben en una cuenta gratuita; y se desactualiza en silencio frente a las revisiones del NRT |
| **rclone** | Su `client_id` compartido será retirado durante 2026. La Drive API en Python funciona igual de bien y sin esa dependencia |
| **OAuth de usuario con credenciales propias** | `drive.readonly` es un scope **restringido**: en modo Prueba el token muere a los 7 días; en Producción exige verificación y una evaluación CASA anual (USD 5.000–20.000) |
| **Consentimiento Interno / delegación de dominio** | Requiere Google Workspace. La cuenta del proyecto es Gmail y la Alianza usa Microsoft 365 |
| **Scope `drive.file`** | Solo ve archivos creados por la propia aplicación; los de GloH2O los creó GloH2O |
| **Espejo del ICDC (U. Hamburgo)** | Acceso restringido y ~1 mes de rezago. Útil para histórico, no para NRT |
| **Espejo del NCI (Australia)** | Solo 1979–2020 y requiere cuenta institucional australiana |
| **Google Earth Engine** | MSWEP no está publicado ahí |
| **API/FTP de GloH2O** | Reservados a usuarios comerciales |
| **P80 climatológico** | Se definen umbrales con conocimiento local. Y el histórico de MSWX vía Drive serían ~11.000 archivos globales por variable |
| **AgERA5 para el umbral** | Comparar un Tmax de MSWX contra un percentil de AgERA5 sumaría dos sesgos en lugar de cancelarlos |
| **Git como archivo de largo plazo** | El historial crece sin poda posible y GitHub no tiene mandato de preservación |
| **Cloudflare R2, Backblaze B2** | Exigen tarjeta de crédito |
| **Supabase** | Pausa proyectos inactivos |
| **Hugging Face como capa de servicio** | Falla el preflight de CORS en peticiones con Range y limita por IP a usuarios anónimos |
| **Escala subestacional en la v1** | Baja predictibilidad y escaso desarrollo operativo en el IDEAM |

---

## 15. Mantenimiento previsible

| Qué | Cuándo | Acción |
|---|---|---|
| `ubuntu-latest` migra a Ubuntu 26 | 19 de octubre de 2026 | Ninguna. Es Python puro instalado con pip; se prefiere flotar antes que anclar una imagen que GitHub retirará |
| Versiones de las acciones | Periódico | Revisar. En octubre de 2026 se actualizaron de v4/v5 a v7/v8 porque apuntaban a Node.js 20, descontinuado |
| Retención de artefactos | 14 días | Si expira, publicar a mano deja el sitio sin datos. Correr el diario lo resuelve |

---

## 16. Pendientes

### Bloqueantes para el valor del sistema

- [ ] **Validar los 21 umbrales con los técnicos en territorio.** Hoy ninguno
      tiene respaldo agronómico y son el corazón de lo que activa las medidas.
- [ ] **Validar MSWX contra estaciones del IDEAM**, por el sesgo de Tmax que
      subcuenta los días de calor.

### Desarrollo

- [ ] Módulo de pronóstico: diario (Open-Meteo, sin credenciales), IDEAM y
      estacional
- [ ] Mostrar madurez, ITH y banderas de calidad en el tablero
- [ ] Añadir el RONI, que es el índice oficial desde la declaración 26-05
- [ ] Validar el ITH con un zootecnista
- [ ] Indicador de encharcamiento para ASPROAGROPE, que reporta daño por exceso
      de humedad y no por sequía

### Gobernanza

- [ ] Resolver el nombre visible de la cuenta de Hugging Face con el PMA
- [ ] Depósito en Dataverse o CGSpace de la Alianza, con DOI
- [ ] Confirmar por escrito con GloH2O que los derivados pueden publicarse
- [ ] Definir el repositorio canónico: el Reporte 1 cita
      `alliance-datascience/PMA-nino-Colombia`, el prototipo vive en
      `aamaya03/PMA_Dashboard` y el entregable es `pmaclimafap/PMA_nino`
- [ ] `CREDENCIALES.md` con el inventario y los códigos de recuperación

### Verificar

- [ ] `MSWX_V100/Long` apareció sin subcarpetas, lo que no cuadra con la
      documentación
- [ ] Evaluar MSWEP V3.16 frente a V2.8 con datos propios
- [ ] Comparar MSWEP contra la precipitación de MSWX (`NRT/P/`), que es una
      estimación independiente ya disponible
