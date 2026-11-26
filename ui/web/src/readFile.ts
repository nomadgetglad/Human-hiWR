// Read a sales file on this device: CSV/text as is, a PDF's text (pdf.js), or a photo/PNG through the text
// reader Scan uses. Then turn report or receipt lines into "item,qty" rows. Nothing leaves the device.
import { readText } from "./Scan";

async function pdfText(file: File): Promise<string> {
  const pdfjs = await import("pdfjs-dist");
  const worker = (await import("pdfjs-dist/build/pdf.worker.min.mjs?url")).default;
  pdfjs.GlobalWorkerOptions.workerSrc = worker;
  const doc = await pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) }).promise;
  const lines: string[] = [];
  for (let p = 1; p <= Math.min(doc.numPages, 20); p++) {
    const page = await doc.getPage(p);
    const content = await page.getTextContent();
    // group text pieces into lines by their vertical position
    const rows = new Map<number, { x: number; s: string }[]>();
    for (const it of content.items as any[]) {
      if (!it.str?.trim()) continue;
      const y = Math.round(it.transform[5]);
      rows.set(y, [...(rows.get(y) ?? []), { x: it.transform[4], s: it.str }]);
    }
    [...rows.entries()].sort((a, b) => b[0] - a[0]).forEach(([, parts]) => lines.push(parts.sort((a, b) => a.x - b.x).map((x) => x.s).join(" ")));
  }
  return lines.join("\n");
}

export async function fileToText(file: File): Promise<{ text: string; how: string }> {
  const name = file.name.toLowerCase();
  if (file.type === "application/pdf" || name.endsWith(".pdf")) return { text: await pdfText(file), how: "pdf" };
  if (file.type.startsWith("image/") || /\.(png|jpe?g|webp)$/.test(name)) {
    const url = URL.createObjectURL(file);
    try {
      const r = await readText(url);
      return { text: r?.text ?? "", how: "image" };
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  return { text: await file.text(), how: "csv" };
}

const PRICE = /\$?\s?\d{1,6}[.,]\d{2}\b/g;

// Turns lines like "2 x Fish tacos", "Fish tacos 2", "Fish tacos ..... 2   $30.00" into rows.
// Returns the rows as CSV text plus the lines that couldn't be read.
export function linesToCsv(text: string): { csv: string; rows: number; unread: string[] } {
  const out: string[] = [];
  const unread: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.replace(PRICE, " ").replace(/[.]{2,}|_{2,}/g, " ").replace(/\s+/g, " ").trim();
    if (!line || !/[a-z]/i.test(line)) continue;
    if (/^(item|dish|product|description|qty|quantity|total|subtotal|tax|tip|cash|card|change|thank)/i.test(line)) continue;
    let m = line.match(/^(\d{1,4})\s*[x×]?\s+([^\d].*)$/i);
    if (m) {
      out.push(`${m[2].replace(/,/g, " ").trim()},${m[1]}`);
      continue;
    }
    m = line.match(/^(.*?[a-z].*?)\s+[x×]?\s*(\d{1,4})$/i);
    if (m) {
      out.push(`${m[1].replace(/,/g, " ").trim()},${m[2]}`);
      continue;
    }
    unread.push(raw.trim());
  }
  return { csv: out.length ? "item,qty\n" + out.join("\n") : "", rows: out.length, unread };
}
