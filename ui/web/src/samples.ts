// Sample pictures for the demo "Try it" panel: an order ticket QR code, a product UPC label and a delivery note,
// drawn in the browser so they can be scanned without a camera. Demo data only.
import { BrowserQRCodeSvgWriter } from "@zxing/browser";

function canvas(w: number, h: number) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const x = c.getContext("2d")!;
  x.fillStyle = "#fff";
  x.fillRect(0, 0, w, h);
  x.fillStyle = "#000";
  return { c, x };
}

export async function sampleQr(text: string): Promise<string> {
  const svg = new BrowserQRCodeSvgWriter().write(text, 360, 360);
  svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const url = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(svg));
  const img = new Image();
  await new Promise((ok, bad) => {
    img.onload = ok;
    img.onerror = bad;
    img.src = url;
  });
  const { c, x } = canvas(400, 400);
  x.drawImage(img, 20, 20, 360, 360);
  return c.toDataURL("image/png");
}

// EAN-13 (a UPC-A is an EAN-13 with a leading 0)
const L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"];
const R = L.map((p) => [...p].map((b) => (b === "0" ? "1" : "0")).join(""));
const G = R.map((p) => [...p].reverse().join(""));
const PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"];

export function sampleUpc(upcA: string): string {
  const d = [..."0" + upcA].map(Number);
  let bits = "101";
  for (let i = 1; i <= 6; i++) bits += PARITY[d[0]][i - 1] === "L" ? L[d[i]] : G[d[i]];
  bits += "01010";
  for (let i = 7; i <= 12; i++) bits += R[d[i]];
  bits += "101";
  const m = 4, q = 12 * m;
  const { c, x } = canvas(bits.length * m + 2 * q, 240);
  [...bits].forEach((b, i) => b === "1" && x.fillRect(q + i * m, 20, m, 170));
  x.font = "28px monospace";
  x.fillText(upcA, q + 40, 225);
  return c.toDataURL("image/png");
}

export function sampleNote(lines: string[]): string {
  const { c, x } = canvas(900, 80 + lines.length * 64);
  lines.forEach((l, i) => {
    x.font = i === 0 ? "bold 52px Arial" : "42px Arial";
    x.fillText(l, 40, 80 + i * 64);
  });
  return c.toDataURL("image/png");
}
