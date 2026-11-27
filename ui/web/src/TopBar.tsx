// The bar on top of every screen: one key number set per level (revenue for the account holder,
// labor for managers, tasks for employees) and the notifications bell. The server decides what each level gets.
import { Button, Dialog, DialogTrigger, Heading, Popover } from "react-aria-components";
import { usd, type Me } from "./api";
import { useT } from "./i18n";
import { useData } from "./useData";

type Stat = { label: string; value: number; format: "usd" | "pct" | "int"; detail: string };
type Note = { kind: string; text: string; go: string; id?: number };
type Bar = { role_view: "owner" | "manager" | "employee"; stats: Stat[]; notifications: Note[] };

const ICON: Record<string, string> = { task: "☐", prep: "🔥", staff: "👥", hr: "📝", "86": "⚠", ticket: "🎫", money: "$" };

export function TopBar({ me, go, right }: { me: Me; go: (s: string) => void; right: React.ReactNode }) {
  const t = useT();
  const bar = useData<Bar>("/api/topbar", [me.id]);
  const fmt = (s: Stat) => (s.format === "usd" ? usd(s.value) : s.format === "pct" ? `${s.value}%` : String(s.value));
  const notes = bar?.notifications ?? [];
  return (
    <header className={`topbar topbar-${bar?.role_view ?? "employee"}`}>
      <Button className="brand" onPress={() => go("home")} aria-label={t("Home")}>
        <img src="/icon.svg" alt="" width={36} height={36} />
        <span className="brand-name">Human</span>
      </Button>
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
      <DialogTrigger>
        <Button className="me-btn" aria-label={`${t("Signed in as")} ${me.name}`}>
          {me.name.slice(0, 1)}
        </Button>
        <Popover className="popover" placement="bottom end">
          <Dialog className="notes me-sheet" aria-label={t("Signed in as")}>
            {right}
          </Dialog>
        </Popover>
      </DialogTrigger>
    </header>
  );
}
