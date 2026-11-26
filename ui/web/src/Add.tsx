// HiWR -> Add: for the team. Add a person (the Team screen's Add staff), or add a job role to the pick list,
// optionally with training steps. Managers and the account holder only; every change goes through a confirm card.
import { useEffect, useState } from "react";
import { Button, Dialog, Heading, Input, Label, Modal, ModalOverlay, TextArea, TextField } from "react-aria-components";
import type { Me } from "./api";
import { useProposals } from "./ConfirmCard";
import { useT } from "./i18n";

export function AddDialog({ isOpen, onClose, me, go }: { isOpen: boolean; onClose: () => void; me: Me; go: (s: string) => void }) {
  const t = useT();
  const { propose } = useProposals();
  const [role, setRole] = useState("");
  const [steps, setSteps] = useState("");
  const [mode, setMode] = useState<"menu" | "email" | "role">("menu");
  const [first, setFirst] = useState("");
  const [email, setEmail] = useState("");
  const [copied, setCopied] = useState("");
  const can = me.level >= 2;
  const link = location.origin + "/";
  const inviteText = (name: string) =>
    `Hi ${name || "there"},\n\nYou're invited to join our team on Human, the app we use for shifts, tasks and the kitchen.\n\n` +
    `1. Open ${link}\n2. Pick your name and choose "First time".\n3. Type the one-time setup code I'll give you, then scan the QR code with an authenticator app.\n\nSee you there,\n${me.name}`;
  const close = () => {
    setMode("menu");
    setCopied("");
    onClose();
  };
  return (
    <ModalOverlay className="overlay" isOpen={isOpen} onOpenChange={(o) => !o && close()} isDismissable>
      <Modal className="card scan-card">
        <Dialog aria-label={t("Add")}>
          <Heading slot="title" className="h2">
            ➕ {t("Add to the team")}
          </Heading>
          {!can ? (
            <p className="muted">{t("Adding people and job roles is for managers and the account holder.")}</p>
          ) : mode === "menu" ? (
            <ul className="quick-list" role="menu" aria-label={t("Quick commands")}>
              <li>
                <Button className="note" onPress={() => setMode("email")}>
                  <span className="note-icon" aria-hidden>✉️</span>
                  <span>
                    <b>{t("Invite by email")}</b>
                    <span className="muted small" style={{ display: "block" }}>{t("Opens a ready-made email in your mail app; you send it")}</span>
                  </span>
                </Button>
              </li>
              <li>
                <Button
                  className="note"
                  onPress={async () => {
                    await navigator.clipboard?.writeText(inviteText(""));
                    setCopied(t("Invite copied. Paste it in a text or chat. Give each person their setup code from the Team screen."));
                  }}
                >
                  <span className="note-icon" aria-hidden>🔗</span>
                  <span>
                    <b>{t("Share invite link")}</b>
                    <span className="muted small" style={{ display: "block" }}>{t("Copies the link and the steps to paste anywhere")}</span>
                  </span>
                </Button>
              </li>
              <li>
                <Button className="note" onPress={() => { close(); go("team"); }}>
                  <span className="note-icon" aria-hidden>👤</span>
                  <span>
                    <b>{t("Add staff")}</b>
                    <span className="muted small" style={{ display: "block" }}>{t("First name, job role and level, on the Team screen")}</span>
                  </span>
                </Button>
              </li>
              <li>
                <Button className="note" onPress={() => { close(); go("roles"); }}>
                  <span className="note-icon" aria-hidden>🏷</span>
                  <span>
                    <b>{t("Add or set up a job role")}</b>
                    <span className="muted small" style={{ display: "block" }}>{t("Level, who it reports to, training, who certifies")}</span>
                  </span>
                </Button>
              </li>
              {copied && (
                <li>
                  <p className="callout callout-ok small" role="status">{copied}</p>
                </li>
              )}
            </ul>
          ) : mode === "email" ? (
            <div className="panel">
              <h3 className="h3">✉️ {t("Invite by email")}</h3>
              <TextField className="field" value={first} onChange={setFirst}>
                <Label>{t("First name")}</Label>
                <Input autoComplete="off" />
              </TextField>
              <TextField className="field" value={email} onChange={setEmail} type="email">
                <Label>{t("Their email")}</Label>
                <Input autoComplete="off" />
              </TextField>
              <p className="muted small">{t("Human doesn't send anything: this opens a draft in your own mail app. Add them on the Team screen and give them their setup code.")}</p>
              <div className="row-gap">
                <Button className="btn" onPress={() => setMode("menu")}>{t("Back")}</Button>
                <Button
                  className="btn btn-primary"
                  isDisabled={!email.includes("@")}
                  onPress={() => {
                    location.href = `mailto:${encodeURIComponent(email.trim())}?subject=${encodeURIComponent("Join our team on Human")}&body=${encodeURIComponent(inviteText(first.trim()))}`;
                  }}
                >
                  {t("Open the email draft")}
                </Button>
              </div>
            </div>
          ) : (
            <>
              <div className="panel" hidden>
                <h3 className="h3">👤 {t("Add a person")}</h3>
                <p className="muted small">{t("First name, job role and level, on the Team screen.")}</p>
                <Button
                  className="btn btn-primary"
                  onPress={() => {
                    onClose();
                    go("team");
                  }}
                >
                  {t("Add staff")}
                </Button>
              </div>
              <div className="panel">
                <h3 className="h3">🏷 {t("Add a job role")}</h3>
                <p className="muted small">{t("It joins the job-role list. Add training steps and people in this role are trainees until each step is signed off.")}</p>
                <TextField className="field" value={role} onChange={setRole}>
                  <Label>{t("Job role")}</Label>
                  <Input placeholder="Barista" />
                </TextField>
                <TextField className="field" value={steps} onChange={setSteps}>
                  <Label>{t("Training steps, one per line (optional)")}</Label>
                  <TextArea rows={4} placeholder={"Espresso dial-in\nMilk steaming\nCleaning the machine"} />
                </TextField>
                <Button
                  className="btn btn-primary"
                  isDisabled={!role.trim()}
                  onPress={() => {
                    propose("add_role", { role, steps: steps.split(/\r?\n/).filter((s) => s.trim()) });
                    onClose();
                    setRole("");
                    setSteps("");
                  }}
                >
                  {t("Check and add")}
                </Button>
                <Button className="btn" onPress={() => setMode("menu")}>
                  {t("Back")}
                </Button>
              </div>
            </>
          )}
          <div className="row-gap scan-foot">
            <Button className="btn btn-ghost" onPress={close}>
              {t("Close")}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

// Subtitles that fade between examples of what a business covers. Holds still if the viewer prefers less motion.
export function Rotator({ items, ms = 2600 }: { items: string[]; ms?: number }) {
  const t = useT();
  const [i, setI] = useState(0);
  useEffect(() => {
    if (items.length < 2 || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const id = setInterval(() => setI((x) => (x + 1) % items.length), ms);
    return () => clearInterval(id);
  }, [items.length, ms]);
  return (
    <span className="rotator" aria-label={items.map((x) => t(x)).join(", ")}>
      <span key={i} className="rotator-item" aria-hidden>
        {t(items[i] ?? "")}
      </span>
    </span>
  );
}
