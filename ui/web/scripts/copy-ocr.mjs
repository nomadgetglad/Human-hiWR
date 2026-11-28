// Copies the text reader (Tesseract) into public/ocr so scanning works offline: no photo or text leaves the PC.
import { cpSync, mkdirSync, readdirSync } from "node:fs";
const out = "public/ocr";
mkdirSync(out, { recursive: true });
cpSync("node_modules/tesseract.js/dist/worker.min.js", `${out}/worker.min.js`);
for (const f of readdirSync("node_modules/tesseract.js-core")) {
  if (/lstm\.(wasm\.js|js|wasm)$/.test(f)) cpSync(`node_modules/tesseract.js-core/${f}`, `${out}/${f}`);
}
cpSync("node_modules/@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz", `${out}/eng.traineddata.gz`);
console.log("ocr files copied to", out);
