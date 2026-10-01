# Módulo de pronóstico — escala de tiempo

Proyecto: *Fortalecimiento de la resiliencia de los sistemas agroalimentarios
frente al Fenómeno de El Niño* — Alianza Bioversity-CIAT / Programa Mundial de
Alimentos.

| | |
|---|---|
| Estado | Diseñado. Sin implementar. |
| Alcance de este documento | Escala de **tiempo** (días). La escala de **clima** (estacional) se documenta aparte. |
| Última actualización | 1 de octubre de 2026 |

---

## 1. Por qué tiempo y clima van separados

No es una decisión de organización, es física.

El pronóstico del tiempo es un problema de **condiciones iniciales**: el modelo
parte del estado actual de la atmósfera y lo integra hacia adelante. El caos
limita eso a unas dos semanas.

El pronóstico estacional es un problema de **condiciones de frontera**: no
predice días, predice el desplazamiento de la distribución de probabilidad por
efecto de la temperatura del mar.

Mezclarlos en una sola vista induce a leer un valor estacional como si fuera un
pronóstico diario. Por eso son dos pestañas con lenguaje distinto: *tiempo*
habla de días concretos, *clima* de probabilidades de terciles.

Esta separación ya está planteada en la sección 1.4 del Reporte 1.

---

## 2. Fuentes

Ocho modelos, elegidos por **linajes independientes**. Incluir los 45 que expone
Open-Meteo sería ruido: muchos son variantes regionales sin cobertura de
Colombia, o derivados del mismo modelo padre.

| Modelo | Origen | Tipo | Resolución | Horizonte | Acceso |
|---|---|---|---|---|---|
| ECMWF IFS HRES | Europa | Físico | 9 km | 10 d | Open-Meteo |
| NCEP GFS | EE. UU. | Físico | 11–25 km | 16 d | Open-Meteo |
| DWD ICON | Alemania | Físico | 11 km | 7 d | Open-Meteo |
| GEM | Canadá | Físico | 15 km | 10 d | Open-Meteo |
| ECMWF AIFS | Europa | IA | 25 km | 15 d | Open-Meteo |
| Google WeatherNext 2 | DeepMind | IA, ensamble | 25 km | 15 d | Open-Meteo |
| WRF-IDEAM | Colombia | Físico regional | 10 km | 7 d | Portal IDEAM |
| CHIRPS-GEFS | CHC/UCSB | Híbrido, solo lluvia | 0,05° | 16 d | Servidor CHC |

Cuatro físicos globales, dos de IA, uno nacional y uno especializado en lluvia.
Eso permite tres comparaciones con sentido: entre centros, entre física e IA, y
entre global y regional.

### 2.1. Notas por fuente

**Google WeatherNext 2 es un ensamble.** Su media no es comparable sin más con un
modelo determinista: es más suave y subestima los extremos, justo lo que importa
en una alerta. Se muestra marcado visualmente como tal.

**Los modelos de IA no resuelven el problema de representatividad.** Se entrenan
sobre ERA5, así que heredan su representación de malla. Son mejores en métricas
de habilidad, pero una celda de 25 km sobre Planadas sigue promediando valle y
montaña. De hecho tienden a producir campos más suaves, lo que puede empeorar
los extremos.

**WRF-IDEAM entrega agregados diarios listos.** El archivo
`geoTIFF<DDMMAAAA>00Z.zip`, de unos 9,6 MB, contiene `TMAX_C`, `TMIN_C`,
`TMED_C`, `ACUM24HPREC_MM`, `VIENTOMAX_MPS`, `VIENTOMED_MPS` y `ET0_mm`, con 7
días por variable y la convención `<VAR>_<corrida>_fcst_<objetivo>.tif`.
Resolución 10 km, EPSG:4326, publicado hacia las 09:30 hora local (unas 14:30
UTC), con ~6 semanas de retención.

> El ZIP diario **no trae humedad relativa**. Esa variable solo está en
> `geoTIFFhumedadhorario`, de 32 MB y resolución horaria. Decisión pendiente:
> pagar esos 32 MB diarios o aceptar que la humedad venga solo de Open-Meteo.

**CHIRPS-GEFS es el único ya corregido por sesgo**, por emparejamiento de
cuantiles contra la climatología de CHIRPS. Para lluvia, que es la variable con
peor sesgo en los modelos, tenerla calibrada de origen vale mucho.

---

## 3. Variables

Cuatro, **las mismas del módulo de monitoreo**: Tmax, Tmin, precipitación y
humedad relativa.

Que coincidan no es casualidad: permite dibujar una **serie continua** donde el
tramo pasado es observado y el futuro es pronóstico, con una línea marcando
"hoy". Materializa la idea de predicción sin costuras de la sección 1.4 del
Reporte 1, y hace evidente de inmediato si un modelo arranca desenganchado de la
realidad observada.

**El viento no es una variable del módulo.** Se muestra únicamente a través del
mapa de Windy embebido.

---

## 4. Normalización: comparar peras con peras

Los modelos difieren en resolución temporal, hora de inicialización, horizonte y
variables nativas. Sin tres normalizaciones, la comparación engaña.

**El día debe ser el mismo día.** "La máxima del martes" tiene que significar lo
mismo para todos. Open-Meteo agrega a diario con `timezone=America/Bogota`. Para
el IDEAM, que entrega en UTC, la agregación se hace con el mismo criterio.

**Tmax y Tmin se derivan, no se asumen.** Varios modelos no las publican y hay
que calcularlas del ciclo horario. Mezclar variable nativa en unos y derivada en
otros rompe la comparación.

**Cada modelo con su hora de corrida real.** Un pronóstico de la corrida 00Z y
otro de la 12Z no tienen la misma anticipación aunque apunten al mismo día. El
archivo guarda `emision`, `objetivo` y `horizonte`, y toda comparación se hace
**a igual horizonte**.

---

## 5. El problema del sesgo

### 5.1. El riesgo

Los modelos tienen sesgo sistemático a nivel de punto, sobre todo en Tmax y en
terreno complejo, porque la elevación de la celda difiere de la del sitio real.
Un error de 5 a 7 °C es perfectamente posible.

Para este sistema eso es grave: el indicador central cuenta **días por encima de
un umbral**. Si Tmax viene sistemáticamente baja, se subcuentan los días de
calor, que es justo lo que debe activar las medidas anticipatorias.

### 5.2. Por qué la anomalía no basta por sí sola

La resta cancela el error sistemático **solo si la climatología viene del mismo
modelo que el pronóstico**. Con climatología de otra fuente:

```
anomalía = (real + sesgo_modelo) − climatología_ERA5
         = (real − climatología_ERA5) + sesgo_modelo
```

El sesgo no desaparece: pasa entero a la anomalía. Hay que quitarlo antes.

### 5.3. Nunca se mide contra la realidad, y no hace falta

**No tenemos estaciones meteorológicas.** Tampoco las necesitamos para esto.

Lo que llamamos "sesgo" es en rigor un **desfase entre dos productos**:

```
desfase_modelo = promedio( modelo − ERA5 )
```

Esa operación solo necesita las dos series, que tenemos completas. La realidad no
aparece. Escribiendo cada producto como realidad más error:

```
modelo = realidad + e_modelo
ERA5   = realidad + e_era5

desfase = modelo − ERA5 = e_modelo − e_era5
```

La realidad se cancela. Lo que queda es la diferencia entre errores, que es
exactamente lo que se necesita para poner el modelo en la escala de ERA5.

> **Importante:** el desfase se calcula sobre la **resta día por día**, no sobre
> el promedio de los pronósticos. Si se promediaran los pronósticos de un modelo
> en abril-octubre de 2026 se obtendría una climatología corta contaminada por El
> Niño. Al promediar la resta, el calor está en ambas columnas y se va: queda
> solo el desfase.

### 5.4. ERA5 como regla de medir, no como verdad

ERA5 también está sesgado y **no es dato observado**. No importa, porque la
pregunta que hacemos es una diferencia.

Una regla mal fabricada a la que le faltan dos centímetros da 98 cm para una mesa
de 100: el valor absoluto está mal. Pero si se miden mesa y silla con **la misma
regla** y se pregunta cuánto más alta es una que la otra, el error se va en la
resta.

ERA5 es esa regla. Se corrige el modelo para que deje de usar su propia regla
distinta, y después se resta.

**Lo que ERA5 aporta y ningún modelo puede:** qué es normal. Eso requiere
décadas, y del archivo de pronósticos solo hay seis meses, que además son de El
Niño fuerte. La climatología ERA5 1991-2020 cubre niños, niñas y años normales.

### 5.5. La fórmula completa

```
anomalía = pronóstico − desfase_modelo − climatología_ERA5
```

Ejemplo numérico, El Paso, 2 de octubre:

| Paso | Valor |
|---|---|
| GFS pronostica | 30,5 °C |
| Desfase de GFS en El Paso, octubre | −3,2 °C |
| → pronóstico en escala ERA5 | 33,7 °C |
| Climatología ERA5 del 2 de octubre | 31,0 °C |
| **Anomalía** | **+2,7 °C** |

Sin el primer paso, la anomalía habría dado −0,5 °C: el tablero diría "día
normal" cuando el modelo anuncia casi tres grados por encima de lo normal.

### 5.6. Qué queda sin resolver

| Lo que muestra el tablero | ¿Contaminado por el sesgo de ERA5? |
|---|---|
| Anomalía | **No** |
| Valor absoluto | **Sí**, hereda el error de ERA5 |

Por eso la anomalía va grande y el absoluto en gris pequeño. No es una decisión
estética: un número es confiable y el otro no.

El valor absoluto solo se arregla validando contra estaciones del IDEAM, que es
un pendiente abierto y compartido con el módulo de monitoreo.

### 5.7. El supuesto, dicho sin rodeos

Todo esto depende de que **ERA5 se equivoque de forma parecida siempre en ese
punto**. Si subestima 2,3 °C tanto en el promedio de 30 años como el día
concreto, el error se cancela limpio. Si subestimara 2,3 °C en promedio pero 4 °C
en días muy calurosos, quedaría un residuo y la anomalía saldría corta justo en
los extremos.

Como el sesgo viene sobre todo de la geometría de la celda y la altitud, que no
cambian, el supuesto es razonable. Por eso la climatología se calcula **por día
del año** y no como un promedio anual único.

Nótese que es un supuesto **más débil** que el alternativo: no corregir nada
equivale a suponer que los productos no tienen sesgo, lo cual sabemos falso.

### 5.8. Una ventaja colateral

Al corregir el desfase, los modelos se vuelven **comparables entre sí**. Sin eso,
la banda de dispersión mezclaría dos cosas: desacuerdo real sobre el clima y
simple diferencia de escalas. La banda saldría inflada por una razón que no tiene
nada que ver con incertidumbre meteorológica.

Con todos en la misma escala, la banda mide solo desacuerdo, y la confianza del
día significa algo.

Corolario comprobable: después de corregir, los modelos **deberían converger** a
la misma anomalía. Si no convergen, discrepan sobre el clima, no sobre su escala.

---

## 6. Tratamiento por variable

No todas se corrigen igual. El sesgo de temperatura es aditivo; el de
precipitación es multiplicativo y sobre todo de frecuencia.

| Variable | En la serie | En las tarjetas | Por qué |
|---|---|---|---|
| Tmax, Tmin | Anomalía con desfase restado | Anomalía | Sesgo aditivo y estable |
| Precipitación | Milímetros por modelo | Probabilidad de lluvia + acumulado | La anomalía comunica poco: lo normal de un día suele ser cero |
| Humedad relativa | Valor con línea de umbral | Probabilidad de superar el umbral | Acotada 0–100 y acoplada a la temperatura |

### 6.1. Precipitación

La anomalía de lluvia dice poco: "+3 mm sobre lo normal" no significa gran cosa
cuando lo normal para un día concreto es cero. Y el sesgo de los modelos en
lluvia no es tanto de cantidad como de **frecuencia**: llueve poquito casi todos
los días.

En la serie van los milímetros, porque es lo que el usuario espera. En las
tarjetas va la **probabilidad de superar el umbral de día con lluvia**, que es
mucho menos sensible al sesgo que un valor. Para la cantidad, CHIRPS-GEFS aporta
la estimación ya calibrada.

**Nunca se promedian los modelos para lluvia.** Promediar produce una llovizna
constante que ningún modelo predijo y que nunca ocurre. Lo que se muestra es la
mediana y el acuerdo.

### 6.2. Humedad relativa

Se trata como la lluvia, no como la temperatura. Está acotada entre 0 y 100, así
que cerca de los extremos una corrección aditiva se sale de rango. Y está
acoplada al error de temperatura: si el modelo da Tmax baja, da humedad alta, de
modo que no es un error independiente.

Una anomalía de humedad tampoco se interpreta fácil. El umbral sí: para café,
superar el 75 % es riesgo de roya; para gallinas, la humedad alta es lo que
convierte 30 °C en emergencia.

### 6.3. ITH pronosticado

Con Tmax y humedad pronosticadas se puede calcular el índice de temperatura y
humedad para los municipios pecuarios. En el monitoreo se verificó que El Paso
tuvo tres días en categoría de emergencia que el indicador de temperatura solo no
mostraba. Anticipar eso con tres días es exactamente el tipo de alerta que activa
una medida.

---

## 7. Agregación y confianza

Metodología adaptada de [meteocompare](https://github.com/Flowm/meteocompare).

### 7.1. Predictibilidad

```
spreadScore    = clamp(1 − desviación / dispersión_típica, 0, 1)
modelFactor    = min(1, n / 3)
predictibilidad = clamp(spreadScore × modelFactor, 0, 1)
```

La dispersión se normaliza contra la típica para ese horizonte. El factor penaliza
cuando pocos modelos aportan: un solo modelo nunca alcanza confianza alta, aunque
"coincida consigo mismo".

Esto es superior a contar cuántos modelos coinciden en el signo: es continuo y
reconoce que la incertidumbre crece con el horizonte.

### 7.2. La confianza del día es el mínimo, no el promedio

**El día vale lo que vale su variable menos confiable.**

Promediar la confianza de temperatura y lluvia hace que todo quede en medio: una
variable certera nunca levanta el día y una incierta nunca lo hunde. Con el
mínimo, un día con temperatura clara pero lluvia en disputa sale marcado como
poco confiable, que es lo correcto.

### 7.3. Horizonte y número de modelos

A partir del día 7, ICON y WRF-IDEAM se quedan sin horizonte. El factor por
número de modelos hace que la confianza no pueda ser alta con tres aportando. La
tarjeta se marca y dice por qué, en lugar de ocultar el día o fingir que vale lo
mismo.

### 7.4. Verificación

Es lo que distingue esto de un visor de pronósticos. La pregunta de fondo no es
qué dicen los modelos, sino **cuál de ellos ha acertado aquí**.

Solo se responde archivando cada emisión y comparándola después contra lo
observado. Después de unos meses se podrá decir algo como "para lluvia a 3 días
en Planadas, ICON acierta más que GFS", y eso es lo que convierte un visor en una
herramienta de decisión. Es también lo que permite calibrar los umbrales que
activan las medidas anticipatorias.

La confianza se comunica como **frecuencia observada**, no como porcentaje
abstracto: "8 de cada 10 pronósticos con esta confianza acertaron dentro de 2 °C
en El Paso".

---

## 8. Visualización

Estructura inspirada en meteocompare, con la anomalía al frente.

**Cabecera.** Municipio, su sistema productivo y los umbrales aplicables. Es
contexto de toda la página. Al cambiar de El Paso a Planadas no solo cambian los
datos: el sistema pasa de gallinas ponedoras a café y el umbral de calor de 30 a
28 °C.

**Panorama diario.** Una tarjeta por día, **desde mañana**. Icono del tiempo,
anomalía grande y en color, los dos valores absolutos en gris pequeño,
probabilidad de lluvia con su acumulado, humedad, y la insignia de confianza.

El **borde superior** lo pinta la anomalía; la **insignia** lleva la confianza.
Separarlos permite leer las dos cosas: qué tan anómalo es el día y qué tan
creíble es esa anomalía.

Los iconos se reutilizan del generador de infografías del equipo, para que portal
e infografías se vean como parte de la misma cosa.

**Serie continua.** Con sus propios controles: pestañas de variable (Tmax, Tmin,
lluvia, humedad) y selector de horizonte (7 o 16 días). Observado a la izquierda,
línea de "hoy", y hacia adelante el **agregado ponderado con su banda de ±1σ**.
Los modelos individuales quedan en gris tenue, con un control para resaltarlos.

Siete líneas de colores a 16 días es ilegible, y además induce a buscar "cuál
tiene razón", que es la pregunta equivocada antes de tener verificación.

**Mapa de Windy.** Iframe de `embed.windy.com` centrado en el municipio,
plegable y cerrado por defecto, con carga diferida. Sin llave ni cuenta.

> Dos ajustes frente al uso de meteocompare: la capa por defecto no debe ser
> radar, porque **en Colombia la cobertura de radar es escasa**; conviene lluvia
> modelada o satélite. Y al ser dependencia externa que no funciona sin conexión,
> cerrado por defecto es lo correcto para usuarios en territorio.

**Verificación.** Bloque reservado, hoy vacío.

### 8.1. Nota al pie

Se explica qué puede y qué no puede hacer el número, en vez de redactar un
descargo. Una nota escrita para cubrirse se nota, y la gente deja de leerlas.

> Los modelos estiman la lluvia sobre celdas de 10 a 25 km, así que un valor
> representa el promedio de un área grande, no un punto. Suelen acertar mejor
> **si** va a llover que **cuánto**, y tienden a repartir lluvia en días que
> terminan secos. Para decidir, pesa más el acuerdo entre modelos y la
> probabilidad que el número de milímetros.

---

## 9. Almacenamiento

```
pronostico/
├── tiempo/AAAA/AAAA-MM-DD.parquet     INMUTABLE · una emisión por archivo
└── clima/AAAA/AAAA-MM.parquet         INMUTABLE · estacional, después

referencia/
├── climatologia.parquet               ERA5 1991-2020 · se calcula UNA VEZ
└── sesgos.parquet                     GENERADO · se recalcula el día 1

derivado/
└── verificacion/
    ├── tiempo/AAAA.parquet
    └── resumen.parquet
```

`referencia/` existe porque **no es ni observación ni pronóstico**: es la vara de
medir. Si cambia, cambian todas las anomalías publicadas, y eso merece su propio
historial.

### 9.1. `pronostico/tiempo/AAAA/AAAA-MM-DD.parquet`

| Columna | Ejemplo |
|---|---|
| `emision` | 2026-10-01T00:00Z |
| `objetivo` | 2026-10-05 |
| `horizonte` | 4 |
| `municipio` | el_paso |
| `variable` | tmax |
| `modelo` | ecmwf_ifs |
| `valor` | 33,7 |

Un archivo por fecha de emisión, **nunca reescrito**: si una escritura falla, se
pierde un día en lugar de un mes de historia irrecuperable.

Volumen: ~3.600 filas diarias (7 municipios × 8 modelos × 16 días × 4 variables),
entre 40 y 60 KB por día, unos 18 MB al año.

### 9.2. `referencia/climatologia.parquet`

Se calcula **una sola vez**. 30 años de ERA5, una llamada a la API histórica de
Open-Meteo por municipio, produce un valor por municipio, variable y día del año.
No cambia salvo que se decida cambiar el período de referencia.

### 9.3. `referencia/sesgos.parquet`

| Columna | Para qué |
|---|---|
| `municipio`, `modelo`, `variable` | la clave |
| `mes` | el desfase cambia con la estación |
| `horizonte_banda` | 1-3, 4-7, 8-16 días |
| `desfase` | el offset estimado |
| `pares` | cuántas comparaciones lo respaldan |
| `calculado` | fecha del cálculo |

`pares` permite decidir si confiar: con pocos, el tablero muestra la anomalía sin
corregir y lo declara.

Guardar el desfase **por mes** conserva el historial de cómo se mueve, que es lo
que detecta un cambio de versión de modelo sin que nadie lo anuncie (ECMWF pasó
al ciclo 50R1 en mayo de 2026).

### 9.4. Qué NO se guarda

**Los agregados.** El promedio ponderado, la dispersión y la probabilidad se
recalculan desde las emisiones en cada publicación. Son derivados, y guardarlos
obligaría a regenerarlos cada vez que cambie un peso.

**El IDEAM no tiene carpeta propia.** Sus pronósticos entran a la misma tabla con
`modelo: wrf_ideam`. La fuente es un detalle de captura, no de estructura.

---

## 10. Cadencia

| Qué | Cuándo | Costo |
|---|---|---|
| Captura de pronósticos | Diaria, ~16:00 UTC | Minutos |
| Recálculo de desfases | Día 1 de cada mes | Minutos |
| Climatología ERA5 | Una vez | Una llamada por municipio |
| Relleno inicial de desfases | Una vez | ~15 min |

El cron de las 16:00 UTC se separa del monitoreo (05:00 UTC) porque el IDEAM
publica hacia las 14:30 UTC.

### 10.1. Por qué recalcular el desfase

**Estacionalidad.** El error en Tmax en temporada seca no es el mismo que en
lluviosa, porque el error de nubosidad cambia. Obliga a estimarlo por mes.

**Cambios de versión.** Una actualización de modelo desplaza el desfase, y una
estimación vieja queda desfasada sin aviso.

Mejor que fijar una frecuencia a ciegas: que el sistema **mida la estabilidad de
su propio desfase**. Si al recalcular se mueve poco, puede pasar a trimestral; si
salta, hay que mirar qué cambió.

### 10.2. Costo real del cálculo

No se parece al problema de MSWX en Drive:

| | MSWX histórico | Desfase vía Open-Meteo |
|---|---|---|
| Qué se baja | Archivos globales | Series puntuales por API |
| Para 6 meses | ~1.100 archivos de 25 MB | Unas pocas llamadas JSON |
| Volumen | ~27 GB | Cientos de KB |
| Cuota | Limitada y compartida | 10.000 llamadas diarias |

Para el desfase **por horizonte** hace falta la API de corridas individuales, una
llamada por fecha de inicialización: ~1.400 peticiones, bien bajo el límite
diario, unos 15 minutos **una sola vez**.

---

## 11. Decisiones tomadas y descartadas

| Decisión | Razón |
|---|---|
| **Anomalía al frente, absoluto en gris** | El absoluto hereda el sesgo de ERA5; la anomalía no |
| **Climatología ERA5 1991-2020** | Única fuente de "lo normal" con ciclo anual completo |
| **Desfase sobre la resta diaria, no sobre el promedio de pronósticos** | El promedio de abril-octubre 2026 traería El Niño adentro |
| ~~Referencia de los últimos 15-30 días~~ | **Borraría la señal de El Niño**: la anomalía daría cero durante el JJA más cálido desde 1950. Y en transiciones como septiembre-octubre en Sucre arrastraría el desfase estacional |
| ~~Climatología corta del propio modelo~~ | Solo 6 meses de archivo, contaminados por el evento |
| ~~Promediar modelos para lluvia~~ | Produce una llovizna que ningún modelo predijo |
| ~~Anomalía para humedad~~ | Acotada 0–100 y acoplada a la temperatura; el umbral sí comunica |
| ~~ET0 como variable~~ | Se descartó para mantener las mismas cuatro del monitoreo |
| ~~Viento como variable~~ | Solo se muestra vía el mapa de Windy |
| ~~Escala subestacional (2–6 semanas)~~ | Menor predictibilidad y escaso desarrollo operativo en el IDEAM |
| ~~Siete líneas de colores en la serie~~ | Ilegible a 16 días; el agregado con banda comunica mejor |
| ~~Confianza como promedio de variables~~ | Regresa todo a "medio"; el mínimo discrimina |

---

## 12. Pendientes

### Decisiones

- [ ] Humedad relativa del IDEAM: ¿pagar 32 MB diarios del ZIP horario, o
      aceptar que venga solo de Open-Meteo?
- [ ] Dónde cortan las categorías de confianza (alta, media, baja)
- [ ] Dispersión típica por variable y horizonte para normalizar la
      predictibilidad
- [ ] Pesos por modelo: ¿iguales, o con bonificación regional al WRF-IDEAM?

### Implementación

- [ ] Adaptadores: Open-Meteo, IDEAM, CHIRPS-GEFS
- [ ] Relleno inicial del archivo de pronósticos (abril-octubre 2026)
- [ ] Cálculo de la climatología ERA5
- [ ] Cálculo de desfases y workflow mensual
- [ ] Página `pronostico.html`

### Compartidos con el monitoreo

- [ ] **Validar contra estaciones del IDEAM.** Es lo único que arregla el valor
      absoluto, y afecta a los dos módulos.
- [ ] Validar los umbrales con los técnicos en territorio. Sin eso, las alertas
      del pronóstico heredan el mismo problema que las del monitoreo.
