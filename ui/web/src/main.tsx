import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Button, ListBox, ListBoxItem, Popover, Select, SelectValue, Label, ToggleButton, ToggleButtonGroup } from "react-aria-components";
import { api, setMember, type AuthStatus, type Me, type Member } from "./api";
import { ProposalProvider } from "./ConfirmCard";
import { LangContext, useT, type Lang } from "./i18n";
import { SignIn } from "./SignIn";
import { TopBar } from "./TopBar";
import { Scene } from "./Scene";
import { Portfolio } from "./Portfolio";
import { Tour, tourKey } from "./Tour";
import { Accounting, Assistant, BusinessProfile, MeshScreen, CustomerService, Home, Inventory, Prep, Staff, Tasks, Team } from "./screens";
import { useData } from "./useData";
import "./styles.css";

function App() {
  const [who, setWho] = useState(() => new URLSearchParams(location.search).get("as") ?? "chef_marco");
  const [me, setMe] = useState<Me | null>(null);
  const [lang, setLang] = useState<Lang>("en");
  const [screen, setScreen] = useState(() => new URLSearchParams(location.search).get("screen") ?? "home");
  const [toast, setToast] = useState("");
  const [touring, setTouring] = useState(false);
  // First time this person opens the app on this device: start the guided tour on Home
  useEffect(() => {
    if (me && !localStorage.getItem(tourKey(me))) {
      setScreen("home");
      setTouring(true);
    }
  }, [me?.id]);
  const [authState, setAuthState] = useState<AuthStatus | null>(null);
  const [signedIn, setSignedIn] = useState(0); // bumps after a sign-in or sign-out

  useEffect(() => {
    api<AuthStatus>("/api/auth/status").then(setAuthState).catch(() => setAuthState({ auth_required: false, signed_in: null, sample: true }));
  }, [signedIn]);

  useEffect(() => {
    if (!authState) return;
    if (authState.auth_required) {
      // your own account: who you are comes from your sign-in, not a switcher
      setMember("");
      if (!authState.signed_in) return setMe(null);
      api<Me>("/api/me").then((m) => {
        setMe(m);
        setLang(m.language);
      });
      return;
    }
    setMember(who);
    api<Me>("/api/me")
      .then((m) => {
        setMe(m);
        setLang((new URLSearchParams(location.search).get("lang") as Lang) ?? m.language);
      })
      // not on this roster (e.g. a new, empty account): sign in as the first person on it
      .catch(() => api<Member[]>("/api/members").then((ms) => ms.length && ms[0].id !== who && setWho(ms[0].id)));
  }, [who, authState]);
  useEffect(() => {
    if (toast) {
      const id = setTimeout(() => setToast(""), 5000);
      return () => clearTimeout(id);
    }
  }, [toast]);

  if (authState?.auth_required && !authState.signed_in)
    return (
      <LangContext.Provider value={lang}>
        <SignIn onDone={() => setSignedIn((n) => n + 1)} />
      </LangContext.Provider>
    );
  if (!me) return null;
  const signOut = async () => {
    await api("/api/auth/signout", {});
    setScreen("home");
    setSignedIn((n) => n + 1);
  };
  // Only sections the server put on the menu (it hides ones this member can't open or that have nothing in them).
  const allowed = new Set(["home", "mesh", ...(me.sample ? ["scene"] : []), ...(me.level >= 4 ? ["settings"] : []), ...me.menu.flatMap((m) => [m.id, ...(m.children ?? [])])]);
  const invTabs = me.menu.find((m) => m.id === "inventory")?.children ?? [];
  const go = (s: string) => setScreen(allowed.has(s) ? s : "home");
  const view = allowed.has(screen) ? screen : "home";

  return (
    <LangContext.Provider value={lang}>
      <ProposalProvider onError={setToast}>
        <div className="app" key={me.id}>
          <TopBar
            me={me}
            go={go}
            right={
              <SessionControls
                who={who}
                setWho={(w) => { setWho(w); setScreen("home"); }}
                lang={lang}
                setLang={setLang}
                me={me}
                signOut={authState?.auth_required ? signOut : undefined}
              />
            }
          />
          <Nav me={me} screen={view} go={go} tour={() => { setScreen("home"); setTouring(true); }} />
          <main className="main" id="main">
            {view === "home" && <Home me={me} go={go} />}
            {view === "team" && <Team />}
            {view === "staff" && <Staff />}
            {view === "prep" && <Prep />}
            {["inventory", "warehouse", "stew", "lab"].includes(view) && <Inventory tab={view === "inventory" ? undefined : view} tabs={invTabs} />}
            {view === "cs" && <CustomerService />}
            {view === "accounting" && <Accounting />}
            {view === "tasks" && <Tasks />}
            {view === "assistant" && <Assistant />}
            {view === "settings" && <BusinessProfile />}
            {view === "mesh" && <MeshScreen />}
            {view === "scene" && <Scene go={go} />}
            {view === "portfolio" && <Portfolio />}
          </main>
          {touring && view === "home" && <Tour me={me} onDone={() => setTouring(false)} />}
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

function Nav({ me, screen, go, tour }: { me: Me; screen: string; go: (s: string) => void; tour: () => void }) {
  const t = useT();
  const items = [{ id: "home", label: "Home" }, ...me.menu];
  const active = ["warehouse", "stew", "lab"].includes(screen) ? "inventory" : screen;
  return (
    <nav className="nav" aria-label="Main menu">
      {items.map((m) => (
        <Button key={m.id} className={`nav-item ${active === m.id ? "nav-on" : ""}`} onPress={() => go(m.id)} aria-current={active === m.id ? "page" : undefined}>
          {t(m.label)}
        </Button>
      ))}
      <Button className="nav-item nav-tour" onPress={tour}>
        🧭 {t("Take the tour")}
      </Button>
    </nav>
  );
}

function SessionControls({ who, setWho, lang, setLang, me, signOut }: { who: string; setWho: (w: string) => void; lang: Lang; setLang: (l: Lang) => void; me: Me; signOut?: () => void }) {
  const t = useT();
  const members = useData<Member[]>(signOut ? null : "/api/members") ?? []; // reloads after someone is added
  return (
    <div className="session">
      {signOut ? (
        <div className="who who-val">
          <b>{me.name}</b>
          <span className="muted small">
            {me.title} · L{me.level} {me.level_name}
          </span>
          <Button className="btn btn-ghost btn-sm" onPress={signOut}>
            {t("Sign out")}
          </Button>
        </div>
      ) : (
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
      )}
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
