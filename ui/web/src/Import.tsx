// Import (HiWR button): bring in sales from the register (a CSV export from the POS) so inventory is worked out
// from what was sold. The file is read in this browser; the lines go to the server as a proposal, and nothing
// changes until a manager confirms the card that shows exactly what comes out of stock.
import { useRef, useState } from "react";
import { Button, Dialog, Heading, Modal, ModalOverlay, TextArea, TextField, Label } from "react-aria-components";
import { api } from "./api";
import { useProposals } from "./ConfirmCard";
import { useT } from "./i18n";

type Row = { item: string; qty: number; hour?: number };

const ITEM_COLS = ["item", "dish", "name", "product", "description", "sku", "upc", "menu item"];
const QTY_COLS = ["qty", "quantity", "units", "count", "sold"];
const HOUR_COLS = ["hour", "time"];

function splitCsv(line: string): string[] {
  const out: string[] = [];
  let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q && c === '"' && line[i + 1] === '"') {
      cur += '"';
      i++;
    } else if (c === '"') q = !q;
    else if (c === "," && !q) {
      out.push(cur);
      cur = "";
    } else cur += c;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

// "item,qty[,hour]" with or without a header row. Unknown columns are ignored.
export function parseSales(text: string): Row[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (!lines.length) return [];
  const head = splitCsv(lines[0]).map((h) => h.toLowerCase());
  const hasHead = head.some((h) => ITEM_COLS.includes(h) || QTY_COLS.includes(h));
  const iItem = hasHead ? head.findIndex((h) => ITEM_COLS.includes(h)) : 0;
  const iQty = hasHead ? head.findIndex((h) => QTY_COLS.includes(h)) : 1;
  const iHour = hasHead ? head.findIndex((h) => HOUR_COLS.includes(h)) : 2;
  const rows: Row[] = [];
  for (const l of hasHead ? lines.slice(1) : lines) {
    const c = splitCsv(l);
    const item = c[iItem < 0 ? 0 : iItem];
    const qty = iQty < 0 ? 1 : Number(c[iQty]);
    if (!item || !Number.isFinite(qty)) continue;
    const hr = iHour >= 0 && c[iHour] ? parseInt(c[iHour], 10) : NaN;
    rows.push({ item, qty, ...(Number.isFinite(hr) ? { hour: hr } : {}) });
  }
  return rows;
}

export function ImportDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const t = useT();
  const { propose } = useProposals();
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const rows = parseSales(text);

  const sample = async () => {
    const dishes = await api<string[]>("/api/cs/dishes").catch(() => []);
    if (!dishes.length) {
      setText("item,qty\n");
      return;
    }
    setName(t("Sample from the register"));
    setText("item,qty,hour\n" + dishes.map((d, i) => `${d},${3 + i},${18 + (i % 3)}`).join("\n"));
  };
  const send = () => {
    propose("import_sales", { rows });
    onClose();
    setText("");
    setName("");
  };

  return (
    <ModalOverlay className="overlay" isOpen={isOpen} onOpenChange={(o) => !o && onClose()} isDismissable>
      <Modal className="card scan-card">
        <Dialog aria-label={t("Import")}>
          <Heading slot="title" className="h2">
            📥 {t("Import sales")}
          </Heading>
          <p className="muted small">
            {t("Bring in sales from your register (a CSV export: item or SKU/UPC, quantity, and the hour if you have it). Inventory is worked out from what was sold. You'll see a confirm card before anything changes.")}
          </p>
          <input
            ref={file}
            type="file"
            accept=".csv,text/csv,text/plain"
            hidden
            onChange={async (e) => {
              const f = e.target.files?.[0];
              if (f) {
                setName(f.name);
                setText(await f.text());
              }
              e.target.value = "";
            }}
          />
          <div className="row-gap">
            <Button className="btn btn-primary" onPress={() => file.current?.click()}>
              {t("Choose a sales file")}
            </Button>
            <Button className="btn" onPress={sample}>
              {t("Try a sample")}
            </Button>
          </div>
          <TextField className="field" value={text} onChange={setText}>
            <Label>{name || t("Or paste the lines here")}</Label>
            <TextArea rows={6} placeholder={"item,qty,hour\nFish tacos,4,19\nHouse burger,6,19"} />
          </TextField>
          <p className="small" role="status">
            {rows.length ? `${rows.length} ${t("sales lines ready")} · ${rows.reduce((a, r) => a + r.qty, 0)} ${t("sold")}` : t("No sales lines yet")}
          </p>
          <div className="row-gap scan-foot">
            <Button className="btn btn-ghost" onPress={onClose}>
              {t("Close")}
            </Button>
            <Button className="btn btn-primary" isDisabled={!rows.length} onPress={send}>
              {t("Check and import")}
            </Button>
          </div>
          <p className="muted small">
            {t("Live links to your register or bank (like Plaid) come later and need your accounts.")}
          </p>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
