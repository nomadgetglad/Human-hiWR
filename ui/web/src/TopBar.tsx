// The bar on top of every screen: one key number set per level (revenue for the account holder,
// labor for managers, tasks for employees) and the notifications bell. The server decides what each level gets.
import { Button, Dialog, DialogTrigger, Heading, Popover } from "react-aria-components";
import { useEffect, useState } from "react";
import { api, usd, type Me } from "./api";
import { useT } from "./i18n";
import { ScanDialog } from "./Scan";
import { ImportDialog } from "./Import";
import { RecallDialog } from "./Recall";
import { useData } from "./useData";

type Stat = { label: string; value: number; format: "usd" | "pct" | "int"; detail: string };
type Note = { kind: string; text: string; go: string; id?: number };
type Bar = { role_view: "owner" | "manager" | "employee"; stats: Stat[]; notifications: Note[] };

const ICON: Record<string, string> = { task: "☐", prep: "🔥", staff: "👥", hr: "📝", "86": "⚠", ticket: "🎫", money: "$" };

// Businesses the account holder can open. "Human" is the placeholder name for the trial demos: before a plan
// is bought, a business tries the demo for its type (stock house, stew or lab). "Your business" is your own
// saved account.
export type Business = { key: string; demo: string | null; name: string; detail: string; icon: string };
export const BUSINESSES: Business[] = [
  { key: "own", demo: null, name: "Your business", detail: "Your own team and data, saved on this PC", icon: "🏠" },
  { key: "stock", demo: "stock", name: "Stock house demo", detail: "Warehouse and shelves: pull list, dates, stock moves", icon: "📦" },
  { key: "stew", demo: "stew", name: "Stew demo", detail: "Restaurant: staff, fryer station, recipes, approvals", icon: "🍲" },
  { key: "lab", demo: "lab", name: "Lab demo", detail: "Long jobs: a house build with phases, inspections, subs", icon: "🏗" },
  { key: "portfolio", demo: "portfolio", name: "Portfolio demo", detail: "Pipe assembly, repair and service, a café, lots, appointments", icon: "📊" },
];

export function currentBusiness(me: Me): Business {
  if (!me.sample) return BUSINESSES[0];
  return BUSINESSES.find((b) => b.demo && b.demo === me.demo) ?? { key: "all", demo: null, name: "Human trial demo", detail: "", icon: "🧪" };
}

// Switch the whole app to another business, then reload into it. Account holder only (the server checks).
export async function switchBusiness(me: Me, b: Business, screen?: string) {
  await api(b.demo ? `/api/demo/reset?demo=${b.demo}` : "/api/demo/reset?sample=false", {});
  location.href = `${location.pathname}?as=${encodeURIComponent(me.id)}${screen ? `&screen=${screen}` : ""}`;
}

function BusinessSwitcher({ me, go }: { me: Me; go: (s: string) => void }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const current = currentBusiness(me);
  const open = async (b: Business, close: () => void) => {
    if (b.key === current.key) return close();
    setBusy(true);
    try {
      await switchBusiness(me, b);
    } finally {
      setBusy(false);
    }
  };
  return (
    <DialogTrigger>
      <Button className="brand" aria-label={`${t("Switch business")}: ${t(current.name)}`}>
        <img src="/icon.svg" alt="" width={36} height={36} />
        <span className="brand-text">
          <span>Human</span>
          <span className="brand-sub">{t(current.name)} ▾</span>
        </span>
      </Button>
      <Popover className="popover" placement="bottom start">
        <Dialog className="notes">
          {({ close }) => (
            <>
              <Heading slot="title" className="notes-title">
                {t("Switch business")}
              </Heading>
              <ul>
                {BUSINESSES.map((b, i) => (
                  <li key={b.key}>
                    {i === 1 && <p className="muted small switch-sub">{t("Try a demo before you buy a plan")}</p>}
                    <Button className={`note ${b.key === current.key ? "note-current" : ""}`} isDisabled={busy} onPress={() => open(b, close)}>
                      <span className="note-icon" aria-hidden>
                        {b.key === current.key ? "✓" : b.icon}
                      </span>
                      <span>
                        <b>{t(b.name)}</b>
                        <span className="muted small" style={{ display: "block" }}>
                          {t(b.detail)}
                        </span>
                      </span>
                    </Button>
                  </li>
                ))}
                <li>
                  <Button
                    className="note"
                    isDisabled={busy}
                    onPress={async () => {
                      if (me.demo === "stew") {
                        close();
                        go("scene");
                        return;
                      }
                      setBusy(true);
                      try {
                        await switchBusiness(me, BUSINESSES.find((x) => x.demo === "stew")!, "scene");
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    <span className="note-icon" aria-hidden>
                      🎬
                    </span>
                    <span>
                      <b>{t("Demo scene for Dad")}</b>
                      <span className="muted small" style={{ display: "block" }}>
                        {t("How prior invoices and payments settle the numbers")}
                      </span>
                    </span>
                  </Button>
                </li>
                <li>
                  <Button
                    className="note"
                    onPress={() => {
                      close();
                      go("settings");
                    }}
                  >
                    <span className="note-icon" aria-hidden>
                      ⚙
                    </span>
                    <span>{t("Business profile")}</span>
                  </Button>
                </li>
              </ul>
            </>
          )}
        </Dialog>
      </Popover>
    </DialogTrigger>
  );
}

// The HiWR button: opens a half circle (hemisphere) of quick actions. The actions are placeholders for now:
// what each one does is still to be decided, so a tap only says so.
const HIWR_ACTIONS = [
  { id: "scan", label: "Scan", icon: "📷" },
  { id: "import", label: "Import", icon: "📥" },
  { id: "recall", label: "Recall", icon: "🕘" },
  { id: "mesh", label: "Mesh", icon: "🕸" },          // staff organizer
  { id: "add", label: "Add", icon: "➕" },
  { id: "settings", label: "Settings", icon: "⚙" },   // last: opens the Business profile (account holder)
];

function HiwrButton({ me, go }: { me: Me; go: (s: string) => void }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [importing, setImporting] = useState(false);
  const [recalling, setRecalling] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  const n = HIWR_ACTIONS.length;
  return (
    <div className="hiwr">
      <Button className={`hiwr-btn ${open ? "hiwr-on" : ""}`} aria-expanded={open} aria-label="HiWR" onPress={() => { setOpen(!open); setPicked(null); }}>
        HiWR
      </Button>
      {open && (
        <>
          <div className="hiwr-scrim" onClick={() => setOpen(false)} aria-hidden />
          <div className="hiwr-fan" role="menu" aria-label="HiWR">
            {HIWR_ACTIONS.map((a, i) => {
              // spread evenly over the lower half circle, left to right
              const angle = Math.PI - (Math.PI * (i + 0.5)) / n;
              const x = Math.cos(angle) * 120, y = Math.sin(angle) * 120;
              return (
                <Button
                  key={a.id}
                  className="hiwr-opt"
                  style={{ transform: `translate(${x}px, ${y}px)`, transitionDelay: `${i * 25}ms` }}
                  onPress={() => {
                    if (a.id === "scan") {
                      setOpen(false);
                      setScanning(true);
                    } else if (a.id === "recall") {
                      setOpen(false);
                      setRecalling(true);
                    } else if (a.id === "import" && me.level >= 2) {
                      setOpen(false);
                      setImporting(true);
                    } else if (a.id === "mesh") {
                      // Mesh = the staff organizer: team chat, plus who makes what from order history
                      setOpen(false);
                      go("mesh");
                    } else if (a.id === "settings" && me.level >= 4) {
                      setOpen(false);
                      go("settings");
                    } else setPicked(a.id);
                  }}
                  aria-label={t(a.label)}
                >
                  <span aria-hidden>{a.icon}</span>
                  <span className="hiwr-label">{t(a.label)}</span>
                </Button>
              );
            })}
            {picked && (
              <p className="hiwr-note" role="status">
                {t(HIWR_ACTIONS.find((a) => a.id === picked)!.label)}:{" "}
                {picked === "settings" ? t("only the account holder opens settings") : picked === "mesh" ? t("nothing on your schedule yet") : picked === "import" ? t("importing sales needs Manager level") : t("not built yet")}
              </p>
            )}
          </div>
        </>
      )}
      <ScanDialog isOpen={scanning} onClose={() => setScanning(false)} />
      <ImportDialog isOpen={importing} onClose={() => setImporting(false)} />
      <RecallDialog isOpen={recalling} onClose={() => setRecalling(false)} />
    </div>
  );
}

export function TopBar({ me, go, right }: { me: Me; go: (s: string) => void; right: React.ReactNode }) {
  const t = useT();
  const bar = useData<Bar>("/api/topbar", [me.id]);
  const fmt = (s: Stat) => (s.format === "usd" ? usd(s.value) : s.format === "pct" ? `${s.value}%` : String(s.value));
  const notes = bar?.notifications ?? [];
  return (
    <header className={`topbar topbar-${bar?.role_view ?? "employee"}`}>
      {/* The logo is the business switcher for the account holder ("Human" is the trial demo's placeholder
          name); for everyone else it goes home. */}
      {me.level >= 4 ? (
        <BusinessSwitcher me={me} go={go} />
      ) : (
        <Button className="brand" onPress={() => go("home")} aria-label={t("Home")}>
          <img src="/icon.svg" alt="" width={36} height={36} />
          <span>Human</span>
        </Button>
      )}
      <HiwrButton me={me} go={go} />
      <div className="stats" aria-live="polite">
        {bar?.stats.map((s) => (
          <div className="stat" key={s.label}>
            <span className="stat-label">{t(s.label)}</span>
            <span className="stat-value">{fmt(s)}</span>
            <span className="stat-detail">{s.detail}</span>
          </div>
        ))}
      </div>
      <DialogTrigger>
        <Button className="bell" aria-label={`${t("Notifications")}: ${notes.length}`}>
          <span aria-hidden>🔔</span>
          {notes.length > 0 && <span className="bell-count">{notes.length}</span>}
        </Button>
        <Popover className="popover" placement="bottom end">
          <Dialog className="notes">
            {({ close }) => (
              <>
                <Heading slot="title" className="notes-title">
                  {t("Notifications")}
                </Heading>
                {notes.length === 0 && <p>{t("Nothing needs you right now")}</p>}
                <ul>
                  {notes.map((n, i) => (
                    <li key={i}>
                      <Button
                        className={`note note-${n.kind}`}
                        onPress={() => {
                          close();
                          go(n.go);
                        }}
                      >
                        <span className="note-icon" aria-hidden>
                          {ICON[n.kind] ?? "•"}
                        </span>
                        <span>{n.text}</span>
                      </Button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Dialog>
        </Popover>
      </DialogTrigger>
      {right}
    </header>
  );
}
