// Guided tour: a few stops that point at the real parts of the screen, different for the account holder,
// managers and staff. It starts on its own the first time someone opens the app (per person, on this device),
// and "Take the tour" in the menu runs it again.
import { useEffect, useLayoutEffect, useState } from "react";
import { Button } from "react-aria-components";
import type { Me } from "./api";
import { useT } from "./i18n";

type Stop = { at?: string; title: string; text: string };

export function tourStops(me: Me): Stop[] {
  const owner = me.level >= 4, manager = me.level >= 2;
  const stops: Stop[] = [
    { title: "Welcome to Human", text: "A quick tour of the screen, about a minute. Use Next, or Skip any time." },
    owner
      ? { at: ".brand", title: "Your business", text: "Tap the logo to switch between your business and the trial demos (Stock house, Stew, Lab), or to open your Business profile." }
      : { at: ".brand", title: "Home", text: "Tap the logo any time to come back to Home." },
    { at: ".hiwr-btn", title: "HiWR quick actions", text: "Scan a QR code, a UPC label or printed text with the camera. Mesh organizes your staff: who's on, who's short and covering shifts. Import, Recall and Add are on the way." + (owner ? " Settings is the last one." : "") },
    manager
      ? { at: ".assistant-home", title: "Ask the assistant", text: "Ask in plain words: what's open tonight, who's short, which tickets are oldest. It only sees what your level may see, and it can't pay, approve or send anything." }
      : { at: ".assistant-home", title: "Ask the assistant", text: "Ask about your tasks and your shift. If you need a real person about pay, your schedule or anything else, ask here and it goes to customer service." },
    { at: ".bell", title: "Notifications", text: manager ? "What needs you right now: short staff, money waiting for approval, open tickets." : "Your tasks for today and anything that needs you." },
    { at: ".nav", title: "Your sections", text: manager ? "A section shows up once there's something in it to work on, so a new business starts simple." : "Only what's yours: My tasks, your station and your requests." },
  ];
  if (manager) stops.push({ at: ".team-start", title: "Start with your team", text: owner ? "Add the people who work with you. Only you raise someone's level." : "Add the people who work with you. They start at Staff level." });
  if (!manager) stops.push({ at: ".lang", title: "English or Spanish", text: "Switch the language of the screens here." });
  stops.push({
    title: "Nothing changes on one tap",
    text: "Every change shows a confirm card first: Confirm, Fix or Cancel. Money always needs two approvers. Take this tour again from the menu any time.",
  });
  return stops;
}

export const tourKey = (me: Me) => `human-tour-done:${me.id}`;

export function Tour({ me, onDone }: { me: Me; onDone: () => void }) {
  const t = useT();
  const stops = tourStops(me);
  const [i, setI] = useState(0);
  const [box, setBox] = useState<DOMRect | null>(null);
  const stop = stops[i];

  const measure = () => {
    const el = stop.at ? (document.querySelector(stop.at) as HTMLElement | null) : null;
    setBox(el ? el.getBoundingClientRect() : null);
  };
  useLayoutEffect(() => {
    const el = stop.at ? (document.querySelector(stop.at) as HTMLElement | null) : null;
    el?.scrollIntoView({ block: "nearest" });
    measure();
  }, [i]);
  useEffect(() => {
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish();
      if (e.key === "ArrowRight") next();
      if (e.key === "ArrowLeft") setI((x) => Math.max(0, x - 1));
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [i]);

  const finish = () => {
    localStorage.setItem(tourKey(me), "1");
    onDone();
  };
  const next = () => (i < stops.length - 1 ? setI(i + 1) : finish());

  // Card goes under the spotlight if there's room, otherwise above it; centered when nothing is pointed at.
  const pad = 8;
  const cardW = Math.min(380, window.innerWidth - 24);
  let style: React.CSSProperties = { left: "50%", top: "50%", transform: "translate(-50%, -50%)", width: cardW };
  if (box) {
    const left = Math.min(Math.max(12, box.left), window.innerWidth - cardW - 12);
    const below = box.bottom + pad + 12;
    style = below + 220 < window.innerHeight ? { left, top: below, width: cardW } : { left, bottom: window.innerHeight - box.top + pad + 12, width: cardW };
  }
  return (
    <div className="tour" role="dialog" aria-modal="true" aria-label={t("Guided tour")}>
      {box ? (
        <div className="tour-spot" style={{ left: box.left - pad, top: box.top - pad, width: box.width + pad * 2, height: box.height + pad * 2 }} />
      ) : (
        <div className="tour-dim" />
      )}
      <div className="tour-card" style={style}>
        <p className="muted small">
          {i + 1} / {stops.length}
        </p>
        <h2 className="h2">{t(stop.title)}</h2>
        <p>{t(stop.text)}</p>
        <div className="row-gap tour-actions">
          <Button className="btn btn-ghost" onPress={finish}>
            {t(i < stops.length - 1 ? "Skip" : "Close")}
          </Button>
          {i > 0 && (
            <Button className="btn" onPress={() => setI(i - 1)}>
              {t("Previous")}
            </Button>
          )}
          <Button className="btn btn-primary" onPress={next} autoFocus>
            {t(i < stops.length - 1 ? "Next" : "Done")}
          </Button>
        </div>
      </div>
    </div>
  );
}
