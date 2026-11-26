import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Button, ListBox, ListBoxItem, Popover, Select, SelectValue, Label, ToggleButton, ToggleButtonGroup } from "react-aria-components";
import { api, setMember, type Me, type Member } from "./api";
import { ProposalProvider } from "./ConfirmCard";
import { LangContext, useT, type Lang } from "./i18n";
import { TopBar } from "./TopBar";
import { Accounting, CustomerService, Home, Inventory, Prep, Staff, Tasks, Team } from "./screens";
import { useData } from "./useData";
import "./styles.css";

function App() {
  const [who, setWho] = useState(() => new URLSearchParams(location.search).get("as") ?? "chef_marco");
  const [me, setMe] = useState<Me | null>(null);
  const [lang, setLang] = useState<Lang>("en");
  const [screen, setScreen] = useState(() => new URLSearchParams(location.search).get("screen") ?? "home");
  const [toast, setToast] = useState("");

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
  const allowed = new Set([...me.menu.map((m) => m.id), "home", "tasks", "warehouse", "stew", "lab"]);
  const go = (s: string) => setScreen(allowed.has(s) ? s : "home");
  const view = allowed.has(screen) ? screen : "home";

  return (
    <LangContext.Provider value={lang}>
      <ProposalProvider onError={setToast}>
        <div className="app" key={me.id}>
          <TopBar
            me={me}
            go={go}
            right={<SessionControls who={who} setWho={(w) => { setWho(w); setScreen("home"); }} lang={lang} setLang={setLang} me={me} />}
          />
          <Nav me={me} screen={view} go={go} />
          <main className="main" id="main">
            {view === "home" && <Home me={me} go={go} />}
            {view === "team" && <Team />}
            {view === "staff" && <Staff />}
            {view === "prep" && <Prep />}
            {["inventory", "warehouse", "stew", "lab"].includes(view) && <Inventory tab={view === "inventory" ? undefined : view} />}
            {view === "cs" && <CustomerService />}
            {view === "accounting" && <Accounting />}
            {view === "tasks" && <Tasks />}
          </main>
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

function Nav({ me, screen, go }: { me: Me; screen: string; go: (s: string) => void }) {
  const t = useT();
  const items = [{ id: "home", label: "Home" }, ...me.menu, ...(me.level < 2 ? [{ id: "tasks", label: "My tasks" }] : [])];
  const active = ["warehouse", "stew", "lab"].includes(screen) ? "inventory" : screen;
  return (
    <nav className="nav" aria-label="Main menu">
      {items.map((m) => (
        <Button key={m.id} className={`nav-item ${active === m.id ? "nav-on" : ""}`} onPress={() => go(m.id)} aria-current={active === m.id ? "page" : undefined}>
          {t(m.label)}
        </Button>
      ))}
    </nav>
  );
}

function SessionControls({ who, setWho, lang, setLang, me }: { who: string; setWho: (w: string) => void; lang: Lang; setLang: (l: Lang) => void; me: Me }) {
  const t = useT();
  const members = useData<Member[]>("/api/members") ?? []; // reloads after someone is added
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
