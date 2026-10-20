// Renderer de un subconjunto de Markdown (sin dependencias externas).
// Soporta: ## / ###, **bold**, *italic*, `code`, ``` fenced blocks,
// tablas |, listas - y 1., > blockquotes, ---, [links](url), párrafos.
//
// Seguridad: TODO el texto se escapa antes de procesar; los enlaces solo
// permiten http(s)/mailto.

import { escapeHtml } from "./util.js";

function inline(s) {
  let out = s;
  // code inline primero (protege su contenido de otros reemplazos)
  const codes = [];
  out = out.replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0000CODE${codes.length - 1}\u0000`;
  });
  // enlaces [texto](url)
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, text, url) => {
    const safe = /^(https?:|mailto:)/i.test(url) ? url : "#";
    return `<a href="${safe}" target="_blank" rel="noopener">${text}</a>`;
  });
  out = out
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>");
  // restaurar códigos
  out = out.replace(/\u0000CODE(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
  return out;
}

export function renderMarkdown(src) {
  const lines = escapeHtml(src).split("\n");
  const html = [];
  let i = 0;

  const isTableSep = (l) => /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(l) && l.includes("-");

  while (i < lines.length) {
    const line = lines[i];

    // bloque de código
    const fence = line.match(/^\s*```(\w*)/);
    if (fence) {
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
      i++; // closing fence
      html.push(`<pre data-lang="${fence[1] || "text"}"><code>${buf.join("\n")}</code></pre>`);
      continue;
    }

    // encabezados
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) {
      const lvl = Math.min(h[1].length + 1, 5); // ## -> h3
      html.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
      i++; continue;
    }

    // hr
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { html.push("<hr>"); i++; continue; }

    // blockquote
    if (/^\s*&gt;\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*&gt;\s?/, ""));
        i++;
      }
      html.push(`<blockquote>${renderMarkdown(buf.join("\n"))}</blockquote>`);
      continue;
    }

    // tabla
    if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const parseRow = (l) => l.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map(c => c.trim());
      const head = parseRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        rows.push(parseRow(lines[i])); i++;
      }
      let t = "<table><thead><tr>" + head.map(c => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
      for (const r of rows) t += "<tr>" + r.map(c => `<td>${inline(c)}</td>`).join("") + "</tr>";
      t += "</tbody></table>";
      html.push(t);
      continue;
    }

    // lista desordenada
    if (/^\s*[-*]\s+/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        buf.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ""))}</li>`); i++;
      }
      html.push(`<ul>${buf.join("")}</ul>`);
      continue;
    }

    // lista ordenada
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        buf.push(`<li>${inline(lines[i].replace(/^\s*\d+[.)]\s+/, ""))}</li>`); i++;
      }
      html.push(`<ol>${buf.join("")}</ol>`);
      continue;
    }

    // línea vacía
    if (line.trim() === "") { i++; continue; }

    // párrafo (agrupa líneas contiguas)
    const buf = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== "" &&
           !/^\s*(```|#{1,4}\s|[-*]\s|\d+[.)]\s|&gt;)/.test(lines[i]) &&
           !/^\s*(-{3,})\s*$/.test(lines[i])) {
      buf.push(lines[i]); i++;
    }
    html.push(`<p>${inline(buf.join("\n"))}</p>`);
  }

  return html.join("\n");
}

// Bloques de contenido de lección -> DOM
export function contentBlockHtml(block) {
  if (block.format === "log" || block.format === "command") {
    const label = block.language || (block.format === "command" ? "terminal" : "log");
    return `<div class="logblock" data-label="${escapeHtml(label)}">${escapeHtml(block.body)}</div>`;
  }
  if (block.format === "code") {
    return `<pre data-lang="${escapeHtml(block.language || "code")}"><code>${escapeHtml(block.body)}</code></pre>`;
  }
  return `<div class="md">${renderMarkdown(block.body)}</div>`;
}
