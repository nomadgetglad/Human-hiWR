import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Button, ListBox, ListBoxItem, Popover, Select, SelectValue, Label, ToggleButton, ToggleButtonGroup } from "react-aria-components";
import { api, setMember, type Me, type Member } from "./api";
import { ProposalProvider } from "./ConfirmCard";
import { LangContext, useT, type Lang } from "./i18n";
import { TopBar } from "./TopBar";
import { Accounting, CustomerService, Home, Inventory, Prep, Staff, Tasks } from "./screens";
import "./styles.css";

const INV_TABS = ["warehouse", "stew", "lab"];
const PAGE_ICON: Record<string, string> = { home: "🏠", staff: "👥", inventory: "📦", prep: "🔥", cs: "💬", accounting: "📒", tasks: "☐" };

function App() {
  const [members, setMembers] = useState<Member[]>([]);
  const [who, setWho] = useState(() => new URLSearchParams(location.search).get("as") ?? "chef_marco");
  const [me, setMe] = useState<Me | null>(null);
  const [lang, setLang] = useState<Lang>("en");
  const [screen, setScreen] = useState(() => new URLSearchParams(location.search).get("screen") ?? "home");
  const [toast, setToast] = useState("");

  useEffect(() => {
    api<Member[]>("/api/members").then(setMembers);
  }, []);
  useEffect(() => {
    setMember(who);
    api<Me>("/api/me").then((m) => {
      setMe(m);
      setLang((new URLSearchParams(location.search).get("lang") as Lang) ?? m.language);
    });
  }, [who]);
  useEffect(() => {
    if (toast) {
      const id = setTimeout(() => setToast(""), 5000);
      return () => clearTimeout(id);
    }
  }, [toast]);

  if (!me) return null;
  // One full-screen page per section, in menu order. Swipe left/right (or tap the bottom bar) to move between them.
  const pages = [{ id: "home", label: "Home" }, ...me.menu, ...(me.level < 2 && !me.menu.some((m) => m.id === "tasks") ? [{ id: "tasks", label: "My tasks" }] : [])];
  const pageOf = (s: string) => (INV_TABS.includes(s) ? "inventory" : s);
  const current = pages.some((p) => p.id === pageOf(screen)) ? pageOf(screen) : "home";
  const index = pages.findIndex((p) => p.id === current);
  const invTab = INV_TABS.includes(screen) ? screen : undefined;
  const go = (s: string) => setScreen(pages.some((p) => p.id === pageOf(s)) ? s : "home");

  const render = (id: string) => {
    switch (id) {
      case "home":
        return <Home me={me} go={go} />;
      case "staff":
        return <Staff />;
      case "prep":
        return <Prep />;
      case "inventory":
        return <Inventory key={invTab ?? "inv"} tab={invTab} />;
      case "cs":
        return <CustomerService />;
      case "accounting":
        return <Accounting />;
      case "tasks":
        return <Tasks />;
    }
    return null;
  };

  return (
    <LangContext.Provider value={lang}>
      <ProposalProvider onError={setToast}>
        <div className="app" key={me.id}>
          <TopBar
            me={me}
            go={go}
            right={<SessionControls members={members} who={who} setWho={(w) => { setWho(w); setScreen("home"); }} lang={lang} setLang={setLang} me={me} />}
          />
          <Pager count={pages.length} index={index} onIndex={(i) => i !== index && setScreen(pages[i].id)}>
            {pages.map((p, i) => (
              <section key={p.id} className="page" id={i === index ? "main" : undefined} inert={i !== index ? true : undefined}>
                <div className="page-inner">{render(p.id)}</div>
              </section>
            ))}
          </Pager>
          <Nav pages={pages} current={current} go={go} />
          {toast && (
            <div className="toast" role="alert">
              {toast}
            </div>
          )}
        </div>
      </ProposalProvider>
    </LangContext.Provider>
  );
}

// Horizontal pager built on native scroll snapping, so the swipe feels like the phone's own and needs no library.
function Pager({ count, index, onIndex, children }: { count: number; index: number; onIndex: (i: number) => void; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const settle = useRef<number>(0);
  const first = useRef(true);
  const indexRef = useRef(index);
  indexRef.current = index;

  // Bottom bar, notifications or a link changed the page: slide to it
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const target = index * el.clientWidth;
    if (Math.abs(el.scrollLeft - target) > 2) el.scrollTo({ left: target, behavior: first.current ? "auto" : "smooth" });
    first.current = false;
  }, [index, count]);

  // Keep the current page lined up when the phone rotates or the window resizes
  useEffect(() => {
    const fit = () => ref.current && ref.current.scrollTo({ left: indexRef.current * ref.current.clientWidth });
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  // Desktop: left/right arrow keys turn the page when nothing that takes typing has focus
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === "ArrowRight" && indexRef.current < count - 1) onIndex(indexRef.current + 1);
      if (e.key === "ArrowLeft" && indexRef.current > 0) onIndex(indexRef.current - 1);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [count, onIndex]);

  const onScroll = () => {
    clearTimeout(settle.current);
    settle.current = window.setTimeout(() => {
      const el = ref.current;
      if (!el) return;
      const i = Math.max(0, Math.min(count - 1, Math.round(el.scrollLeft / el.clientWidth)));
      if (i !== indexRef.current) onIndex(i);
    }, 90);
  };

  return (
    <div className="pager" ref={ref} onScroll={onScroll}>
      {children}
    </div>
  );
}

function Nav({ pages, current, go }: { pages: { id: string; label: string }[]; current: string; go: (s: string) => void }) {
  const t = useT();
  return (
    <nav className="nav" aria-label="Main menu">
      {pages.map((m) => (
        <Button key={m.id} className={`nav-item ${current === m.id ? "nav-on" : ""}`} onPress={() => go(m.id)} aria-current={current === m.id ? "page" : undefined}>
          <span className="nav-icon" aria-hidden>
            {PAGE_ICON[m.id] ?? "•"}
          </span>
          <span className="nav-label">{t(m.label)}</span>
        </Button>
      ))}
    </nav>
  );
}

function SessionControls({ members, who, setWho, lang, setLang, me }: { members: Member[]; who: string; setWho: (w: string) => void; lang: Lang; setLang: (l: Lang) => void; me: Me }) {
  const t = useT();
  return (
    <div className="session">
      <Select className="who" selectedKey={who} onSelectionChange={(k) => setWho(String(k))} aria-label={t("Signed in as")}>
        <Label className="sr-only">{t("Signed in as")}</Label>
        <Button className="select-btn select-who">
          <SelectValue>
            {() => (
              <span className="who-val">
                <b>{me.name}</b>
                <span className="muted small">
                  {me.title} · L{me.level} {me.level_name}
                </span>
              </span>
            )}
          </SelectValue>
          <span aria-hidden>▾</span>
        </Button>
        <Popover className="popover">
          <ListBox className="listbox">
            {members.map((m) => (
              <ListBoxItem key={m.id} id={m.id} className="option" textValue={m.name}>
                {m.name} · {m.title}
              </ListBoxItem>
            ))}
          </ListBox>
        </Popover>
      </Select>
      <ToggleButtonGroup
        className="lang"
        aria-label={t("Language")}
        selectionMode="single"
        disallowEmptySelection
        selectedKeys={[lang]}
        onSelectionChange={(k) => setLang([...k][0] as Lang)}
      >
        <ToggleButton id="en" className="lang-btn">
          EN
        </ToggleButton>
        <ToggleButton id="es" className="lang-btn">
          ES
        </ToggleButton>
      </ToggleButtonGroup>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
// Service workers need https or localhost; on a plain LAN address the app still works, just without the offline shell
if ("serviceWorker" in navigator && !import.meta.env.DEV) navigator.serviceWorker.register("/sw.js");
