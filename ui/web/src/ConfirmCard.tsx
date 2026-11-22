// Every change goes through this card: the server describes what will happen, then the member picks
// Confirm, Fix (edit and check again) or Cancel. After a confirmed change comes the authority check-in.
import { createContext, useContext, useState, type ReactNode } from "react";
import { Button, Dialog, Heading, Input, Label, Modal, ModalOverlay, TextField } from "react-aria-components";
import { api, type Proposal } from "./api";
import { useT } from "./i18n";

type Ctx = { propose: (kind: string, params: Record<string, any>) => void; version: number; refresh: () => void };
const ProposeContext = createContext<Ctx>({ propose: () => {}, version: 0, refresh: () => {} });
export const useProposals = () => useContext(ProposeContext);

export function ProposalProvider({ children, onError }: { children: ReactNode; onError: (m: string) => void }) {
  const [card, setCard] = useState<Proposal | null>(null);
  const [version, setVersion] = useState(0);
  const propose = async (kind: string, params: Record<string, any>) => {
    try {
      setCard(await api<Proposal>("/api/proposals", { kind, params }));
    } catch (e: any) {
      onError(e.message);
    }
  };
  return (
    <ProposeContext.Provider value={{ propose, version, refresh: () => setVersion((v) => v + 1) }}>
      {children}
      {card && (
        <CardModal
          key={card.id}
          card={card}
          onReplace={setCard}
          onClose={(changed) => {
            setCard(null);
            if (changed) setVersion((v) => v + 1);
          }}
        />
      )}
    </ProposeContext.Provider>
  );
}

function CardModal({ card, onReplace, onClose }: { card: Proposal; onReplace: (p: Proposal) => void; onClose: (changed: boolean) => void }) {
  const t = useT();
  const [fixing, setFixing] = useState(false);
  const [draft, setDraft] = useState<Record<string, any>>(card.params);
  const [state, setState] = useState(card);
  const [error, setError] = useState("");
  const [checkin, setCheckin] = useState<{ works?: boolean; intended?: boolean; status?: string }>({});

  const confirm = async () => {
    try {
      setState(await api<Proposal>(`/api/proposals/${card.id}/confirm`, {}));
    } catch (e: any) {
      setError(e.message);
    }
  };
  const cancel = async () => {
    await api(`/api/proposals/${card.id}/cancel`, {});
    onClose(false);
  };
  const checkAgain = async () => {
    try {
      const next = await api<Proposal>("/api/proposals", { kind: card.kind, params: draft });
      await api(`/api/proposals/${card.id}/cancel`, {});
      onReplace(next);
    } catch (e: any) {
      setError(e.message);
    }
  };
  const answer = async (field: "works" | "intended", v: boolean) => {
    const next = { ...checkin, [field]: v };
    setCheckin(next);
    if (next.works !== undefined && next.intended !== undefined) {
      const r = await api<{ status: string }>(`/api/proposals/${card.id}/checkin`, { works: next.works, intended: next.intended });
      setCheckin({ ...next, status: r.status });
    }
  };

  const done = state.state === "confirmed";
  return (
    <ModalOverlay className="overlay" isOpen isDismissable={false} isKeyboardDismissDisabled>
      <Modal className={`card ${card.money ? "card-money" : ""}`}>
        <Dialog aria-label={card.title}>
          <Heading slot="title" className="card-title">
            {card.title}
          </Heading>
          {card.money && !done && <p className="badge badge-warn">PENDING - requires double approval</p>}
          {!done && (
            <>
              <p className="eyebrow">{t("What will happen")}</p>
              <ul className="card-lines">
                {card.lines.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            </>
          )}
          {fixing && !done && (
            <div className="fix">
              {card.fix.map((f) => (
                <TextField
                  key={f.field}
                  className="field"
                  value={String(draft[f.field] ?? "")}
                  onChange={(v) => setDraft({ ...draft, [f.field]: f.type === "number" ? Number(v.replace(/\D/g, "")) : v })}
                >
                  <Label>{f.label}</Label>
                  <Input inputMode={f.type === "number" ? "numeric" : "text"} />
                </TextField>
              ))}
            </div>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {!done ? (
            <div className="card-actions">
              {fixing ? (
                <Button className="btn btn-primary" onPress={checkAgain}>
                  {t("Check again")}
                </Button>
              ) : (
                <Button className="btn btn-primary" onPress={confirm} autoFocus>
                  {t("Confirm")}
                </Button>
              )}
              {card.fix.length > 0 && !fixing && (
                <Button className="btn" onPress={() => setFixing(true)}>
                  {t("Fix")}
                </Button>
              )}
              <Button className="btn btn-ghost" onPress={cancel}>
                {t("Cancel")}
              </Button>
            </div>
          ) : (
            <div className="checkin">
              <p className="result">✓ {state.result}</p>
              <CheckRow label={t("Did it work?")} value={checkin.works} onPick={(v) => answer("works", v)} />
              <CheckRow label={t("Did it do what you meant?")} value={checkin.intended} onPick={(v) => answer("intended", v)} />
              {checkin.status && <p className={`badge ${checkin.status === "OK" ? "badge-ok" : "badge-warn"}`}>Check-in: {checkin.status}</p>}
              <div className="card-actions">
                <Button className="btn btn-primary" onPress={() => onClose(true)}>
                  {t("Close")}
                </Button>
              </div>
            </div>
          )}
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

function CheckRow({ label, value, onPick }: { label: string; value?: boolean; onPick: (v: boolean) => void }) {
  const t = useT();
  return (
    <div className="check-row" role="group" aria-label={label}>
      <span>{label}</span>
      <Button className={`btn btn-sm ${value === true ? "btn-on" : ""}`} onPress={() => onPick(true)} aria-pressed={value === true}>
        {t("Yes")}
      </Button>
      <Button className={`btn btn-sm ${value === false ? "btn-on-bad" : ""}`} onPress={() => onPick(false)} aria-pressed={value === false}>
        {t("No")}
      </Button>
    </div>
  );
}
