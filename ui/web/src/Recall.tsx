// Recall (HiWR button): recurring numbers and averages, computed straight from the records (instant, no AI).
// Search by item, person, vendor or client. Staff see only facts about themselves.
import { useEffect, useState } from "react";
import { Button, Dialog, Heading, Input, Label, Modal, ModalOverlay, SearchField } from "react-aria-components";
import { api } from "./api";
import { useT } from "./i18n";

type Fact = { title: string; value: string; text: string; tags: string[]; kind: string };

const ICON: Record<string, string> = { sales: "🍽", people: "👤", vendors: "🚚", inventory: "📦", lines: "📊", clients: "🤝", money: "$" };
const TRY = ["fish tacos", "Marco", "Sysco", "white fish", "late", "average"];

export function RecallDialog({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const t = useT();
  const [q, setQ] = useState("");
  const [facts, setFacts] = useState<Fact[]>([]);
  const [total, setTotal] = useState(0);
  useEffect(() => {
    if (!isOpen) return;
    const id = setTimeout(() => {
      api<{ facts: Fact[]; total: number }>(`/api/recall?q=${encodeURIComponent(q)}`)
        .then((r) => {
          setFacts(r.facts);
          setTotal(r.total);
        })
        .catch(() => setFacts([]));
    }, 150);
    return () => clearTimeout(id);
  }, [q, isOpen]);
  return (
    <ModalOverlay className="overlay" isOpen={isOpen} onOpenChange={(o) => !o && onClose()} isDismissable>
      <Modal className="card scan-card recall-card">
        <Dialog aria-label={t("Recall")}>
          <Heading slot="title" className="h2">
            🕘 {t("Recall")}
          </Heading>
          <p className="muted small">{t("Recurring numbers and averages, straight from your records.")}</p>
          <SearchField className="field" value={q} onChange={setQ} autoFocus aria-label={t("Search")}>
            <Label className="sr-only">{t("Search")}</Label>
            <Input placeholder={t("Search an item, a person, a vendor or a client")} />
          </SearchField>
          <div className="row-gap recall-try">
            {TRY.map((x) => (
              <Button key={x} className="btn btn-sm btn-ghost" onPress={() => setQ(x)}>
                {x}
              </Button>
            ))}
          </div>
          <div className="recall-list">
            {facts.length === 0 && <p className="muted">{total === 0 ? t("No history yet: Recall fills in as the business runs.") : t("Nothing found for that yet.")}</p>}
            {facts.map((f, i) => (
              <div key={i} className="recall-fact">
                <span className="recall-icon" aria-hidden>
                  {ICON[f.kind] ?? "•"}
                </span>
                <div>
                  <div className="row-between">
                    <b>{f.title}</b>
                    <span className="recall-value">{f.value}</span>
                  </div>
                  <p className="muted small">{f.text}</p>
                </div>
              </div>
            ))}
          </div>
          <div className="row-gap scan-foot">
            <Button className="btn btn-ghost" onPress={onClose}>
              {t("Close")}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}
