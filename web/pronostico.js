/* Tablero de pronóstico.
   Lee publico/pronostico.json, que produce scripts/pronostico.py. */

const VARIABLES = [
  { id: "tmax", etiqueta: "Temperatura máxima", unidad: "°C" },
  { id: "tmin", etiqueta: "Temperatura mínima", unidad: "°C" },
  { id: "precipitacion", etiqueta: "Lluvia", unidad: "mm" },
  { id: "humedad_relativa", etiqueta: "Humedad relativa", unidad: "%" },
];

/* Frase de cada icono. Los iconos los elige el pipeline (exportar_pronostico.icono). */
const CONDICION = {
  sun: "Soleado", hot: "Muy caluroso", part: "Parcialmente nublado", cloudy: "Nublado",
  overcast: "Cubierto", drizzle: "Llovizna", rain: "Lluvioso", heavy: "Lluvias fuertes",
  storm: "Tormentas", showers: "Chubascos", rainlow: "Posibles lluvias",
  thermo_hot: "Caluroso", thermo_cool: "Fresco", humid: "Húmedo",
};
/* Fondo de la parte de arriba de cada tarjeta, según el cielo. */
const CIELO = {
  sun: "#FFE7B8, #FFF6E4", thermo_hot: "#FFD6B8, #FFF1E6", hot: "#FFD0B0, #FFF0E4",
  part: "#DCEBF7, #F4F8FB", cloudy: "#DDE3E8, #F3F5F7", overcast: "#D2D9DF, #F0F2F4",
  rainlow: "#CFE0EE, #EEF4F9", rain: "#BBD3E8, #E9F1F8", heavy: "#A9C4DE, #E4EDF6",
  storm: "#B5BFD6, #EAEDF5", drizzle: "#D3E3EF, #F1F6FA", showers: "#D3E3EF, #F1F6FA",
  thermo_cool: "#DDE8EE, #F3F7F9", humid: "#D6EAF0, #F1F7F9",
};
const ICONO_DATO = {
  t: '<svg viewBox="0 0 24 24" fill="none" stroke="#C0582A" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M10 14.5V5a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0z"/><path d="M12 9v7"/></svg>',
  l: '<svg viewBox="0 0 24 24" fill="none" stroke="#1D5A8C" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M7 15a4 4 0 0 1 .5-8 5 5 0 0 1 9.6 1.5A3.5 3.5 0 0 1 17 15z"/><path d="M9 18l-1 2M13 18l-1 2M17 18l-1 2"/></svg>',
  h: '<svg viewBox="0 0 24 24" fill="none" stroke="#2F7E9E" stroke-width="2.2" stroke-linejoin="round" aria-hidden="true"><path d="M12 3s-6 7-6 11a6 6 0 0 0 12 0c0-4-6-11-6-11z"/></svg>',
};

let DATOS = null;
const estado = { municipio: null, variable: "tmax", dias: 7, modelo: null };

const $ = (id) => document.getElementById(id);
const redondo = (v, suf = "") => (v === null || v === undefined ? "—" : Math.round(v) + suf);
const escapar = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function fecha(iso, opciones) {
  const [a, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(a, m - 1, d)).toLocaleDateString("es-CO", { timeZone: "UTC", ...opciones });
}
const mayuscula = (s) => s.charAt(0).toUpperCase() + s.slice(1);
/* Fecha de hoy en Colombia (AAAA-MM-DD). */
const hoyColombia = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Bogota" });

function puntos(confianza) {
  const n = { alta: 3, media: 2, baja: 1 }[confianza] || 0;
  return `<span class="puntos" aria-hidden="true">${[1, 2, 3].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</span>`;
}

function mostrarVacio(motivo) {
  $("contenido").hidden = true;
  const v = $("vacio");
  v.hidden = false;
  v.innerHTML =
    '<div style="max-width:52rem;padding:1.5rem 1.75rem;border-left:4px solid var(--color-max);' +
    'background:var(--color-panel);border-radius:var(--radius)">' +
    '<h2 style="font-family:var(--font-display);margin:0 0 .5rem;font-size:1.2rem">Sin datos de pronóstico</h2>' +
    "<p style=\"margin:0 0 .75rem\">No se pudo cargar <code>pronostico.json</code>. " +
    "Es probable que la última corrida del módulo de pronóstico no haya terminado bien.</p>" +
    '<p style="margin:0;font-size:.88rem;color:var(--color-ink-soft)">Detalle: ' + escapar(motivo) + "</p></div>";
}

/* ---------- Tarjetas de los próximos días ---------- */

function tarjeta(d) {
  const nombre = d.horizonte === 1 ? "Mañana" : mayuscula(fecha(d.objetivo, { weekday: "long" }));
  const cielo = CIELO[d.icono] || CIELO.part;
  const prob = d.probabilidad_lluvia === null || d.probabilidad_lluvia === undefined ? "—" : Math.round(d.probabilidad_lluvia * 100) + " %";
  const pocos = d.modelos && d.modelos <= 3 ? ` · solo ${d.modelos} modelos` : "";
  return `<article class="dia${d.horizonte === 1 ? " manana" : ""}">
    <div class="cielo" style="--cielo:linear-gradient(180deg, ${cielo})">
      <div class="dn">${nombre}<small>${fecha(d.objetivo, { day: "numeric", month: "long" })}</small></div>
      ${icon(d.icono)}
      <div class="cond">${CONDICION[d.icono] || ""}</div>
    </div>
    <div class="cuerpo">
      <div class="fila"><div class="lab">${ICONO_DATO.t}Temperatura</div>
        <div class="par"><div class="mn"><small>Mínima</small><b>${redondo(d.tmin, "°")}</b></div><div class="mx"><small>Máxima</small><b>${redondo(d.tmax, "°")}</b></div></div></div>
      <div class="fila"><div class="lab">${ICONO_DATO.l}Lluvia</div>
        <div class="par ll"><div><small>Probabilidad</small><b>${prob}</b></div><div><small>Cantidad</small><b>${redondo(d.precipitacion, " mm")}</b></div></div></div>
      <div class="fila"><div class="lab">${ICONO_DATO.h}Humedad relativa</div>
        <div class="hr">${redondo(d.humedad_relativa, " %")}</div></div>
    </div>
    <div class="pie" title="${d.variable_limitante ? "Lo limita: " + escapar(d.variable_limitante) : ""}${pocos}">
      ${puntos(d.confianza)} ${d.confianza ? "Confianza " + escapar(d.confianza) : "Confianza sin dato"}</div>
  </article>`;
}

function pintarDias(mun) {
  const dias = mun.dias.slice(0, estado.dias);
  $("tit-dias").textContent = `Los próximos ${estado.dias} días en ${mun.nombre}`;
  $("dias").innerHTML = dias.map(tarjeta).join("");
  $("dias").scrollLeft = 0;
  flechas();
}

/* Las flechas aparecen solo si hay tarjetas fuera de la vista hacia ese lado. */
function flechas() {
  const c = $("dias");
  $("ant").hidden = c.scrollLeft < 4;
  $("sig").hidden = c.scrollLeft + c.clientWidth > c.scrollWidth - 4;
}

/* ---------- Gráfico: cada modelo en gris y su promedio ---------- */

function pintarGrafico(mun) {
  const cfg = VARIABLES.find((v) => v.id === estado.variable);
  const serie = mun.series[estado.variable];
  $("sub-graf").textContent = `${mun.nombre}, ${mun.departamento} · pronóstico de cada día`;
  const cont = $("grafico");
  if (!serie || !serie.objetivo || !serie.objetivo.length) {
    cont.innerHTML = '<p style="color:var(--color-ink-soft);font-size:.9rem;margin:1rem 0">Ningún modelo aporta esta variable.</p>';
    $("leyenda").innerHTML = "";
    return;
  }

  /* Desde hoy (si la serie lo trae) hasta el último día elegido. */
  const hoy = hoyColombia();
  const ultimo = mun.dias[Math.min(estado.dias, mun.dias.length) - 1];
  const fin = ultimo ? serie.objetivo.indexOf(ultimo.objetivo) : serie.objetivo.length - 1;
  let ini = serie.objetivo.findIndex((f) => f >= hoy);
  if (ini < 0 || ini > fin) ini = 0;
  const idx = [];
  for (let i = ini; i <= (fin >= 0 ? fin : serie.objetivo.length - 1); i++) idx.push(i);
  const fechas = idx.map((i) => serie.objetivo[i]);
  const promedio = idx.map((i) => serie.agregado[i]);
  const modelos = Object.entries(serie.modelos || {}).map(([k, v]) => [k, idx.map((i) => v[i])]);
  const N = fechas.length;
  const nombre = (k) => DATOS.modelos[k]?.nombre || k;
  /* Como en el pipeline: la lluvia es la mediana de los modelos; lo demás,
     su promedio ponderado (WRF-IDEAM pesa más). */
  const central = estado.variable === "precipitacion" ? "mediana" : "promedio ponderado";

  /* Modelo: "Todos" o uno solo. */
  if (estado.modelo && !serie.modelos[estado.modelo]) estado.modelo = null;
  $("modelo-sel").innerHTML = [["", "Todos"], ...modelos.map(([k]) => [k, nombre(k)])]
    .map(([k, n]) => `<option value="${k}">${escapar(n)}</option>`).join("");
  $("modelo-sel").value = estado.modelo || "";
  $("variable-sel").value = estado.variable;
  [...$("variables").children].forEach((x) => x.setAttribute("aria-pressed", x.dataset.var === estado.variable));
  const elegida = estado.modelo ? modelos.find(([k]) => k === estado.modelo)[1] : null;
  const activa = elegida || promedio;

  const todos = [...(elegida || [...promedio, ...modelos.flatMap(([, v]) => v)])].filter((v) => v !== null && v !== undefined);
  if (!todos.length) { cont.innerHTML = ""; $("leyenda").innerHTML = ""; return; }
  let lo = Math.floor(Math.min(...todos)) - 1, hi = Math.ceil(Math.max(...todos)) + 1;
  if (estado.variable === "precipitacion") lo = 0;
  if (estado.variable === "humedad_relativa") hi = Math.min(100, hi);
  if (hi <= lo) hi = lo + 1;

  const celular = cont.clientWidth < 560;
  const W = Math.max(320, cont.clientWidth || 900), H = celular ? 280 : 330;
  const mL = celular ? 40 : 54, mR = celular ? 14 : 40, mT = 18, mB = 34;
  const X = (i) => mL + (N > 1 ? (i * (W - mL - mR)) / (N - 1) : (W - mL - mR) / 2);
  const Y = (v) => mT + ((hi - v) * (H - mT - mB)) / (hi - lo);
  const rango = hi - lo;
  const paso = rango > 40 ? 10 : rango > 16 ? 5 : rango > 8 ? 2 : 1;
  let g = "";
  for (let v = Math.ceil(lo / paso) * paso; v <= hi; v += paso) {
    g += `<line x1="${mL}" x2="${W - mR}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--rejilla)"/>` +
         `<text x="${mL - 8}" y="${Y(v) + 4}" text-anchor="end" font-size="12" fill="var(--color-ink-soft)">${v}</text>`;
  }
  const linea = (vals, color, ancho) => {
    let s = "", tramo = [];
    const cerrar = () => { if (tramo.length > 1) s += `<polyline points="${tramo.join(" ")}" fill="none" stroke="${color}" stroke-width="${ancho}" stroke-linejoin="round" stroke-linecap="round"/>`; tramo = []; };
    vals.forEach((v, i) => { if (v === null || v === undefined) cerrar(); else tramo.push(`${X(i).toFixed(1)},${Y(v).toFixed(1)}`); });
    cerrar();
    return s;
  };
  const color = "var(--prom)";
  if (!elegida) modelos.forEach(([, v]) => { g += linea(v, "var(--modelo)", 1.6); });
  g += linea(activa, color, 3.2);
  g += activa.map((v, i) => (v === null || v === undefined ? "" :
    `<circle cx="${X(i)}" cy="${Y(v)}" r="${celular ? 3.5 : 4.5}" fill="${color}" stroke="var(--color-panel)" stroke-width="1.5"/>`)).join("");
  const iHoy = fechas.indexOf(hoy);
  if (iHoy >= 0) {
    g += `<line x1="${X(iHoy)}" x2="${X(iHoy)}" y1="${mT - 6}" y2="${H - mB}" stroke="var(--color-max)" stroke-width="1.5" stroke-dasharray="4 4"/>` +
         `<text x="${X(iHoy) + 6}" y="${mT + 6}" font-size="11" font-weight="700" fill="var(--color-max)">Hoy</text>`;
  }
  const cada = Math.ceil(N / (celular ? 4 : 9));
  fechas.forEach((f, i) => {
    if (i % cada === 0) g += `<text x="${X(i)}" y="${H - 12}" text-anchor="${X(i) > W - mR - 30 ? "end" : "middle"}" font-size="12" fill="var(--color-ink-soft)">${fecha(f, { day: "numeric", month: "short" }).replace(".", "")}</text>`;
  });
  g += `<text transform="translate(14 ${(H - mB + mT) / 2}) rotate(-90)" text-anchor="middle" font-size="12" fill="var(--color-ink-soft)">${cfg.etiqueta} (${cfg.unidad})</text>`;

  g += `<line id="guia" y1="${mT}" y2="${H - mB}" stroke="var(--color-ink-soft)" stroke-dasharray="2 3" visibility="hidden"/>` +
       `<circle id="marca" r="7" fill="none" stroke="${color}" stroke-width="2.5" visibility="hidden"/>`;

  cont.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${cfg.etiqueta} pronosticada por cada modelo y su promedio">${g}</svg>` +
    `<div class="tip" hidden></div>`;
  $("leyenda").innerHTML = elegida
    ? `<span><i class="prom"></i>${escapar(nombre(estado.modelo))}</span>`
    : `<span><i class="prom"></i>${mayuscula(central)} de los modelos</span>` +
      (celular ? `<span><i></i>Cada modelo</span>` : modelos.map(([k]) => `<span><i></i>${escapar(nombre(k))}</span>`).join(""));

  /* Al pasar el mouse (o tocar) se muestra el valor del día más cercano en la línea activa. */
  const svg = cont.querySelector("svg"), tip = cont.querySelector(".tip");
  const guia = svg.querySelector("#guia"), marca = svg.querySelector("#marca");
  const ocultar = () => { tip.hidden = true; guia.setAttribute("visibility", "hidden"); marca.setAttribute("visibility", "hidden"); };
  svg.onpointermove = (e) => {
    const r = svg.getBoundingClientRect(), esc = W / r.width;
    const i = Math.round((((e.clientX - r.left) * esc - mL) * (N - 1)) / (W - mL - mR));
    const v = activa[i];
    if (i < 0 || i >= N || v === null || v === undefined) return ocultar();
    guia.setAttribute("x1", X(i)); guia.setAttribute("x2", X(i)); guia.setAttribute("visibility", "visible");
    marca.setAttribute("cx", X(i)); marca.setAttribute("cy", Y(v)); marca.setAttribute("visibility", "visible");
    tip.innerHTML = `${escapar(elegida ? nombre(estado.modelo) : mayuscula(central) + " de los modelos")} · ${fecha(fechas[i], { weekday: "short", day: "numeric", month: "short" })}<br>` +
      `<b>${(+v).toFixed(1).replace(".", ",")} ${cfg.unidad}</b>`;
    tip.hidden = false;
    const px = X(i) / esc, py = Y(v) / esc;
    tip.style.left = Math.max(0, Math.min(px - tip.offsetWidth / 2, r.width - tip.offsetWidth)) + "px";
    tip.style.top = Math.max(0, py - tip.offsetHeight - 14) + "px";
  };
  svg.onpointerdown = svg.onpointermove;
  svg.onpointerleave = ocultar;

  const temperatura = estado.variable === "tmax" || estado.variable === "tmin";
  $("nota").innerHTML =
    "Pronóstico diario de varios modelos (Open-Meteo, CC BY 4.0), en el punto del municipio. " +
    (elegida ? "Se muestra solo el modelo elegido. " : `Cada línea gris es un modelo; la línea oscura es su ${central}. `) +
    (estado.variable === "precipitacion"
      ? "Los modelos estiman la lluvia sobre áreas de 10 a 25 km: aciertan mejor <strong>si</strong> va a llover que <strong>cuánto</strong>."
      : temperatura && DATOS.referencia && !DATOS.referencia.desfase_aplicado
        ? "Los valores todavía no se corrigen por el sesgo de cada modelo, así que pueden quedar algo por debajo de lo que marca un termómetro."
        : "");
}

/* ---------- Armado ---------- */

function pintar() {
  const mun = DATOS.municipios[estado.municipio];
  if (!mun) return;
  pintarDias(mun);
  pintarGrafico(mun);
  const panel = $("panel-mapa");
  if (!panel.hidden) cargarMapa(mun);
}

function cargarMapa(mun) {
  const panel = $("panel-mapa");
  panel.innerHTML = "";
  const f = document.createElement("iframe");
  /* Capa de lluvia modelada, no radar: la red de radares en Colombia es
     escasa y la capa de radar saldría vacía en estos municipios. */
  f.loading = "lazy";
  f.title = "Mapa de lluvia y viento";
  f.src = "https://embed.windy.com/embed.html?type=map&location=coordinates" +
    "&metricRain=mm&metricTemp=°C&metricWind=km/h&zoom=8&overlay=rain" +
    "&product=ecmwf&level=surface&lat=" + (mun.lat ?? 0).toFixed(3) + "&lon=" + (mun.lon ?? 0).toFixed(3);
  panel.appendChild(f);
}

function construirControles() {
  const sel = $("municipio");
  sel.innerHTML = Object.entries(DATOS.municipios)
    .map(([id, m]) => `<option value="${id}">${escapar(m.nombre)} (${escapar(m.departamento)})</option>`).join("");
  sel.value = estado.municipio;
  sel.onchange = () => { estado.municipio = sel.value; pintar(); };

  /* El selector de días de la barra y el del gráfico son el mismo: cambiar uno cambia el otro. */
  document.querySelectorAll("[data-horizonte]").forEach((grupo) => {
    grupo.onclick = (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      estado.dias = Number(b.dataset.dias);
      document.querySelectorAll("[data-dias]").forEach((x) => x.setAttribute("aria-pressed", x.dataset.dias === b.dataset.dias));
      pintar();
    };
  });

  const paso = () => $("dias").firstElementChild.offsetWidth + 14;
  $("ant").onclick = () => $("dias").scrollBy({ left: -paso() * 3 });
  $("sig").onclick = () => $("dias").scrollBy({ left: paso() * 3 });
  $("dias").onscroll = flechas;

  $("variables").innerHTML = VARIABLES.map((v) =>
    `<button type="button" class="pill" data-var="${v.id}" aria-pressed="${v.id === estado.variable}">${v.etiqueta}</button>`).join("");
  $("variables").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    estado.variable = b.dataset.var;
    pintarGrafico(DATOS.municipios[estado.municipio]);
  };
  $("variable-sel").innerHTML = VARIABLES.map((v) => `<option value="${v.id}">${v.etiqueta}</option>`).join("");
  $("variable-sel").onchange = (e) => { estado.variable = e.target.value; pintarGrafico(DATOS.municipios[estado.municipio]); };
  $("modelo-sel").onchange = (e) => { estado.modelo = e.target.value || null; pintarGrafico(DATOS.municipios[estado.municipio]); };

  $("btn-mapa").onclick = () => {
    const panel = $("panel-mapa"), abrir = panel.hidden;
    panel.hidden = !abrir;
    $("btn-mapa").setAttribute("aria-expanded", abrir);
    $("btn-mapa").textContent = abrir ? "Cerrar mapa" : "Abrir mapa";
    if (abrir) { cargarMapa(DATOS.municipios[estado.municipio]); panel.scrollIntoView({ behavior: "smooth", block: "nearest" }); }
    else panel.innerHTML = "";
  };

  /* Fecha de emisión: la corrida más reciente entre los modelos. */
  const emis = Object.values(DATOS.emisiones || {}).filter(Boolean).sort();
  const nModelos = Object.keys(DATOS.modelos || {}).length;
  const ultima = emis.length ? emis[emis.length - 1] : null;
  $("emitido").innerHTML = `<b>Pronóstico del ${ultima ? ["day", "month", "year"].map((p) => fecha(ultima.slice(0, 10), { [p]: p === "month" ? "short" : "numeric" }).replace(".", "")).join(" ") : "—"}</b>` +
    `combina ${nModelos} modelo${nModelos === 1 ? "" : "s"} de pronóstico`;

  const f = new Date(DATOS.generado_en);
  $("pie-fuentes").innerHTML = "Fuentes: " + Object.values(DATOS.modelos).map((m) => escapar(m.nombre)).join(", ") +
    (DATOS.referencia?.climatologia ? ". Referencia: " + escapar(DATOS.referencia.climatologia) : "") +
    ". Actualizado el " + f.toLocaleString("es-CO", { dateStyle: "long", timeStyle: "short", timeZone: "America/Bogota" }).replace(/\.?$/, ".");

  /* El gráfico se ajusta al ancho de la pantalla. */
  let espera;
  window.addEventListener("resize", () => { clearTimeout(espera); espera = setTimeout(() => { pintarGrafico(DATOS.municipios[estado.municipio]); flechas(); }, 150); });
}

(async function iniciar() {
  try {
    const r = await fetch("./pronostico.json", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    DATOS = await r.json();
    const ids = Object.keys(DATOS.municipios || {});
    if (!ids.length) throw new Error("el archivo llegó sin municipios");
    estado.municipio = ids[0];
    $("contenido").hidden = false;
    construirControles();
    pintar();
  } catch (e) {
    console.error(e);
    mostrarVacio(e.message);
  }
})();
