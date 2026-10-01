/* Tablero de pronóstico.
   Lee publico/pronostico.json, que produce scripts/pronostico.py.
   Si no hay datos, LO DICE: nunca simula. */

const VARIABLES = [
  { id: "tmax", etiqueta: "Tmax", unidad: "°C", anomalia: true },
  { id: "tmin", etiqueta: "Tmin", unidad: "°C", anomalia: true },
  { id: "precipitacion", etiqueta: "Lluvia", unidad: "mm", anomalia: false },
  { id: "humedad_relativa", etiqueta: "Humedad", unidad: "%", anomalia: false },
];

const COLORES_MODELO = ["#1D5A8C", "#D97F2B", "#2E7D4F", "#B23A3A",
                        "#6b5ea8", "#4FA8C9", "#8a6d3b", "#5f6a6a"];

let DATOS = null;
let estado = { municipio: null, variable: "tmax", dias: 7 };

const $ = (id) => document.getElementById(id);
const num = (v, d = 1) => (v === null || v === undefined ? "—" : v.toFixed(d));
const firmado = (v, d = 1) =>
  v === null || v === undefined ? "—" : (v > 0 ? "+" : "") + v.toFixed(d);

function fechaCorta(iso) {
  const [a, m, d] = iso.split("-").map(Number);
  const f = new Date(Date.UTC(a, m - 1, d));
  return f.toLocaleDateString("es-CO", {
    weekday: "short", day: "numeric", timeZone: "UTC",
  });
}

/* Sin datos se dice, no se inventan. El tablero de monitoreo tenía un
   respaldo silencioso que generaba datos de ejemplo y seguía mostrando el
   sello de actualizado; aquí no existe esa posibilidad. */
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
    '<p style="margin:0;font-size:.88rem;color:var(--color-ink-soft)">Detalle: ' +
    motivo + "</p></div>";
}

/* ---------- Tarjetas ---------- */

function colorAnomalia(a) {
  if (a === null || a === undefined) return "var(--color-line)";
  if (a >= 3) return "#c43e30";
  if (a >= 1.5) return "#e8702a";
  if (a <= -3) return "#1D5A8C";
  if (a <= -1.5) return "#4FA8C9";
  return "var(--color-line)";
}

function pintarTarjetas(mun) {
  const cont = $("tarjetas");
  cont.innerHTML = "";
  const dias = mun.dias.slice(0, estado.dias);

  for (const d of dias) {
    const art = document.createElement("article");
    art.className = "tarjeta";
    art.style.setProperty("--acento", colorAnomalia(d.tmax_anomalia));

    /* Un día al que ya no llegan suficientes modelos se marca y se explica,
       en vez de ocultarlo o fingir que vale lo mismo. */
    const pocos = d.modelos <= 3;
    const lluvia = d.probabilidad_lluvia === null ? "—"
      : Math.round(d.probabilidad_lluvia * 100) + " %";

    art.innerHTML =
      `<div class="fecha">${fechaCorta(d.objetivo)}</div>` +
      icon(d.icono) +
      `<div class="anom" style="color:${colorAnomalia(d.tmax_anomalia)}">` +
      `${firmado(d.tmax_anomalia)}°</div>` +
      `<div class="abs">${num(d.tmax)} / ${num(d.tmin)}</div>` +
      `<div class="lluvia">${lluvia} · ${num(d.precipitacion)} mm</div>` +
      `<div class="hr">HR ${num(d.humedad_relativa, 0)} %</div>` +
      `<div class="conf conf--${d.confianza}" title="${
        d.variable_limitante
          ? "Lo limita: " + d.variable_limitante
          : ""}${pocos ? " · solo " + d.modelos + " modelos" : ""}">` +
      `${d.confianza}</div>`;
    cont.appendChild(art);
  }

  const total = mun.dias.length;
  if (estado.dias > total) {
    const faltan = document.createElement("div");
    faltan.className = "tarjeta tarjeta--corta";
    faltan.textContent = "Ningún modelo llega más lejos";
    cont.appendChild(faltan);
  }
}

/* ---------- Gráfico ---------- */

function pintarGrafico(mun) {
  const serie = mun.series[estado.variable];
  const cfg = VARIABLES.find((v) => v.id === estado.variable);
  const cont = $("grafico");

  if (!serie || !serie.objetivo.length) {
    cont.innerHTML =
      '<p style="color:var(--color-ink-soft);font-size:.9rem;margin:1rem 0">' +
      "Ningún modelo aporta esta variable.</p>";
    $("leyenda").innerHTML = "";
    return;
  }

  const n = Math.min(estado.dias, serie.objetivo.length);
  const fechas = serie.objetivo.slice(0, n);
  /* En temperatura se grafica la anomalía: es lo que no arrastra el sesgo
     del modelo. En lluvia y humedad, el valor, porque su anomalía comunica
     poco (lo normal de un día suele ser cero) o no se interpreta bien. */
  const usarAnom = cfg.anomalia && serie.anomalia;
  const central = (usarAnom ? serie.anomalia : serie.agregado).slice(0, n);
  const sup = usarAnom ? null : serie.banda_sup.slice(0, n);
  const inf = usarAnom ? null : serie.banda_inf.slice(0, n);
  const desv = serie.agregado
    .slice(0, n)
    .map((v, i) => (v === null ? 0 : (serie.banda_sup[i] ?? v) - v));

  const bandaSup = usarAnom
    ? central.map((v, i) => (v === null ? null : v + desv[i]))
    : sup;
  const bandaInf = usarAnom
    ? central.map((v, i) => (v === null ? null : v - desv[i]))
    : inf;

  const modelos = Object.entries(serie.modelos).map(([k, vals]) => {
    const recortado = vals.slice(0, n);
    if (!usarAnom) return [k, recortado];
    /* Para comparar modelos en anomalía, cada uno se desplaza por la misma
       climatología que el agregado: la diferencia entre ellos se conserva. */
    const ref = serie.agregado.slice(0, n).map((v, i) =>
      v === null || central[i] === null ? null : v - central[i]);
    return [k, recortado.map((v, i) =>
      v === null || ref[i] === null ? null : +(v - ref[i]).toFixed(2))];
  });

  const todos = [...central, ...bandaSup, ...bandaInf,
                 ...modelos.flatMap(([, v]) => v)].filter((v) => v !== null);
  let lo = Math.min(...todos), hi = Math.max(...todos);
  if (usarAnom) { const m = Math.max(Math.abs(lo), Math.abs(hi), 1); lo = -m; hi = m; }
  if (!usarAnom) { lo = Math.min(0, lo); }
  if (hi === lo) hi = lo + 1;

  const W = 680, H = 230, mL = 42, mR = 10, mT = 14, mB = 26;
  const x = (i) => mL + (i * (W - mL - mR)) / Math.max(1, n - 1);
  const y = (v) => mT + ((hi - v) * (H - mT - mB)) / (hi - lo);

  const linea = (vals, color, ancho, opacidad) => {
    const tramos = [];
    let actual = [];
    vals.forEach((v, i) => {
      if (v === null) { if (actual.length > 1) tramos.push(actual); actual = []; }
      else actual.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    });
    if (actual.length > 1) tramos.push(actual);
    return tramos.map((t) =>
      `<polyline points="${t.join(" ")}" fill="none" stroke="${color}" ` +
      `stroke-width="${ancho}" opacity="${opacidad}" stroke-linejoin="round"/>`
    ).join("");
  };

  const banda = () => {
    const arriba = [], abajo = [];
    bandaSup.forEach((v, i) => {
      if (v !== null && bandaInf[i] !== null) {
        arriba.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
        abajo.unshift(`${x(i).toFixed(1)},${y(bandaInf[i]).toFixed(1)}`);
      }
    });
    if (arriba.length < 2) return "";
    return `<polygon points="${arriba.concat(abajo).join(" ")}" ` +
           `fill="var(--color-rain)" opacity="0.14"/>`;
  };

  const ticks = [lo, (lo + hi) / 2, hi].map((v) =>
    `<line x1="${mL}" y1="${y(v)}" x2="${W - mR}" y2="${y(v)}" ` +
    `stroke="var(--color-line)" stroke-dasharray="3 3"/>` +
    `<text x="${mL - 6}" y="${y(v) + 3.5}" text-anchor="end" font-size="10" ` +
    `fill="var(--color-ink-soft)">${v.toFixed(v % 1 ? 1 : 0)}</text>`
  ).join("");

  const cero = usarAnom
    ? `<line x1="${mL}" y1="${y(0)}" x2="${W - mR}" y2="${y(0)}" ` +
      `stroke="var(--color-ink-soft)" stroke-width="1"/>`
    : "";

  const etiquetas = fechas.map((f, i) =>
    i % Math.ceil(n / 7) === 0
      ? `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" font-size="10" ` +
        `fill="var(--color-ink-soft)">${fechaCorta(f)}</text>`
      : ""
  ).join("");

  cont.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img" ` +
    `aria-label="Pronóstico de ${cfg.etiqueta} por modelo">` +
    ticks + banda() + cero +
    modelos.map(([k, v], i) =>
      linea(v, COLORES_MODELO[i % COLORES_MODELO.length], 1, 0.35)).join("") +
    linea(central, "var(--color-rain)", 2.5, 1) +
    etiquetas + "</svg>";

  $("leyenda").innerHTML =
    `<span><span style="display:inline-block;width:16px;height:3px;` +
    `background:var(--color-rain);vertical-align:3px"></span> agregado ` +
    `ponderado${usarAnom ? " (anomalía)" : ` (${cfg.unidad})`}</span>` +
    `<span><span style="display:inline-block;width:16px;height:9px;` +
    `background:var(--color-rain);opacity:.25;vertical-align:-1px"></span> ` +
    `±1σ entre modelos</span>` +
    modelos.map(([k], i) =>
      `<span style="opacity:.75"><span style="display:inline-block;width:14px;` +
      `height:2px;background:${COLORES_MODELO[i % COLORES_MODELO.length]};` +
      `vertical-align:3px"></span> ${DATOS.modelos[k]?.nombre || k}</span>`
    ).join("");

  const sinCorregir = !DATOS.referencia.desfase_aplicado && cfg.anomalia;
  $("nota").innerHTML =
    (estado.variable === "precipitacion"
      ? "Los modelos estiman la lluvia sobre celdas de 10 a 25 km, así que un " +
        "valor representa el promedio de un área grande, no un punto. Suelen " +
        "acertar mejor <strong>si</strong> va a llover que <strong>cuánto</strong>, " +
        "y tienden a repartir lluvia en días que terminan secos. Para decidir, " +
        "pesa más el acuerdo entre modelos y la probabilidad que el número de milímetros."
      : "La anomalía compara contra lo normal de la época según " +
        DATOS.referencia.climatologia + ". El valor absoluto hereda el sesgo de " +
        "esa referencia y suele quedar por debajo de lo que marca un termómetro; " +
        "la anomalía no, porque es una diferencia.") +
    (sinCorregir
      ? ' <strong>Atención:</strong> todavía no hay desfases estimados, así que ' +
        "la anomalía se muestra sin corregir el sesgo de cada modelo."
      : "");
}

/* ---------- Armado ---------- */

function pintar() {
  const mun = DATOS.municipios[estado.municipio];
  if (!mun) return;

  $("contexto").innerHTML =
    `${mun.sistema_principal.replace(/_/g, " ")}` +
    (mun.umbral_tmax ? ` · umbral Tmax ${mun.umbral_tmax} °C` : "") +
    (mun.umbral_hr ? `, HR ${mun.umbral_hr} %` : "");

  const emis = Object.entries(DATOS.emisiones || {});
  $("meta").innerHTML =
    `${emis.length} modelo${emis.length === 1 ? "" : "s"}<br>` +
    `corrida ${emis.length ? emis[0][1] : "—"}`;

  pintarTarjetas(mun);
  pintarGrafico(mun);

  const w = $("windy");
  if (!w.dataset.cargado) {
    w.dataset.cargado = "1";
    const g = DATOS.municipios[estado.municipio];
    const f = document.createElement("iframe");
    /* Capa de lluvia modelada, no radar: la red de radares en Colombia es
       escasa y la capa de radar saldría vacía en estos municipios. */
    f.loading = "lazy";
    f.title = "Mapa de lluvia y viento";
    f.src = "https://embed.windy.com/embed.html?type=map&location=coordinates" +
      "&metricRain=mm&metricTemp=°C&metricWind=km/h&zoom=8&overlay=rain" +
      "&product=ecmwf&level=surface&lat=" + (g.lat ?? 0).toFixed(3) +
      "&lon=" + (g.lon ?? 0).toFixed(3);
    w.appendChild(f);
  }
}

function construirControles() {
  const sel = $("municipio");
  sel.innerHTML = Object.entries(DATOS.municipios)
    .map(([id, m]) => `<option value="${id}">${m.nombre}, ${m.departamento}</option>`)
    .join("");
  sel.value = estado.municipio;
  sel.onchange = () => { estado.municipio = sel.value;
    $("windy").innerHTML = ""; $("windy").dataset.cargado = ""; pintar(); };

  $("pestanas").innerHTML = VARIABLES.map((v) =>
    `<button type="button" data-var="${v.id}" aria-pressed="${
      v.id === estado.variable}">${v.etiqueta}</button>`).join("");
  $("pestanas").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    estado.variable = b.dataset.var;
    [...$("pestanas").children].forEach((x) =>
      x.setAttribute("aria-pressed", x === b));
    pintarGrafico(DATOS.municipios[estado.municipio]);
  };

  $("horizonte").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    estado.dias = Number(b.dataset.dias);
    [...$("horizonte").children].forEach((x) =>
      x.setAttribute("aria-pressed", x === b));
    pintar();
  };

  const f = new Date(DATOS.generado_en);
  $("pie-fuentes").innerHTML =
    "Fuentes: " +
    Object.values(DATOS.modelos).map((m) => m.nombre).join(", ") +
    ". Referencia: " + DATOS.referencia.climatologia +
    ". Actualizado el " +
    f.toLocaleString("es-CO", { dateStyle: "long", timeStyle: "short" }) + ".";
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
