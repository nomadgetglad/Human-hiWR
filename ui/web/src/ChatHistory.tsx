// Chat history: every assistant and team-chat message you may see, by month, then by day and time. Find works
// here and from any chat box (type "/find" and the words). The server decides what you may see: your own
// assistant chats, the team channel, and only the direct-message threads you're in. Search runs on this
// computer: words first, then (when the local embedding model is on) messages close in meaning. No AI model writes
// anything here.
import { useContext, useEffect, useRef, useState } from "react";
import { Button, Input, Label, TextField } from "react-aria-components";
import { api } from "./api";
import { LangContext, useT } from "./i18n";

type Msg = { key: string; source: "assistant" | "team" | "dm"; at: string; time?: string; who: string; mine: boolean; where: string; text: string };
type Month = { month: string; label: string; count: number; days: number; by_source: Record<string, number> };
type Day = { day: string; label: string; messages: Msg[] };
type Hit = Msg & { month: string; day: string; day_label: string; time: string; match: "words" | "meaning" | "filter"; score?: number };
type Found = { hits: Hit[]; total: number; how: string; note: string | null; held: number };

const ICON: Record<string, string> = { assistant: "✨", team: "👥", dm: "✉️" };

/** "/find fryer oil" or "find: fryer oil" -> "fryer oil"; anything else -> null. */
export function findCommand(text: string): string | null {
  const m = text.trim().match(/^(?:\/find|\/buscar|find:|buscar:)\s*(.*)$/i);
  return m ? m[1].trim() : null;
}

function Mark({ text, words }: { text: string; words: string[] }) {
  if (!words.length) return <>{text}</>;
  const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "ig");
  return (
    <>
      {text.split(re).map((p, i) => (i % 2 ? <mark key={i}>{p}</mark> : <span key={i}>{p}</span>))}
    </>
  );
}

export function ChatHistory({ query = "", source = "", onClose }: { query?: string; source?: string; onClose?: () => void }) {
  const t = useT();
  const lang = useContext(LangContext);
  const loc = lang === "es" ? "es-US" : "en-US";
  const dayName = (d: string) => new Date(`${d}T12:00`).toLocaleDateString(loc, { weekday: "long", month: "short", day: "numeric" });
  const monthName = (m: string) => new Date(`${m}-15T12:00`).toLocaleDateString(loc, { month: "long", year: "numeric" });
  const [months, setMonths] = useState<Month[]>([]);
  const [held, setHeld] = useState(0);
  const [month, setMonth] = useState("");
  const [only, setOnly] = useState(source);
  const [days, setDays] = useState<Day[]>([]);
  const [q, setQ] = useState(query);
  const [found, setFound] = useState<Found | null>(null);
  const [jump, setJump] = useState("");
  const [err, setErr] = useState("");
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ months: Month[]; held: number }>("/api/history/months")
      .then((r) => {
        setMonths(r.months);
        setHeld(r.held);
        if (r.months.length) setMonth((cur) => cur || r.months[0].month); // a search may have picked one already
      })
      .catch((e) => setErr(e.message));
  }, []);
  useEffect(() => {
    if (!month) return;
    api<{ days: Day[] }>(`/api/history?month=${month}${only ? `&source=${only}` : ""}`)
      .then((r) => setDays(r.days))
      .catch((e) => setErr(e.message));
  }, [month, only]);
  useEffect(() => {
    if (query) {
      setQ(query);
      run(query);
    }
  }, [query]);
  useEffect(() => {
    if (!jump) return;
    const el = box.current?.querySelector(`[data-key="${jump}"]`);
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [days, jump]);

  const run = async (s: string) => {
    setErr("");
    if (!s.trim()) return setFound(null);
    try {
      const r = await api<Found & { filters: { month: string | null } }>(`/api/history/search?q=${encodeURIComponent(s.trim())}`);
      setFound(r);
      if (r.filters.month) setMonth(r.filters.month); // "october" also opens October below

    } catch (e: any) {
      setErr(e.message);
    }
  };
  const words = found ? q.toLowerCase().replace(/\b(from|in):\S+/g, "").split(/\s+/).filter((w) => w.length > 2) : [];
  const show = (h: Hit) => {
    setOnly("");
    setMonth(h.month);
    setJump(h.key);
  };
  const where = (m: Msg) => `${ICON[m.source]} ${m.source === "dm" ? `${t("With")} ${m.where}` : t(m.where)}`;

  return (
    <div className="panel history" ref={box}>
      <div className="row-between">
        <h2 className="h2">🗂 {t("Chat history")}</h2>
        {onClose && (
          <Button className="btn btn-sm btn-ghost" onPress={onClose}>
            {t("Close")}
          </Button>
        )}
      </div>
      <p className="muted small">
        {t("Your assistant chats, the team channel and your own direct messages. Nobody else's direct messages, whatever their level.")}
      </p>
      <form
        className="hist-find"
        onSubmit={(e) => {
          e.preventDefault();
          run(q);
        }}
      >
        <TextField className="field" value={q} onChange={setQ} aria-label={t("Find in chat history")}>
          <Label className="sr-only">{t("Find in chat history")}</Label>
          <Input placeholder={t("Find: fryer oil, october, from:marco, in:dm")} />
        </TextField>
        <Button type="submit" className="btn btn-primary">
          🔍 {t("Find")}
        </Button>
      </form>
      {err && (
        <p className="callout callout-bad" role="alert">
          {err}
        </p>
      )}
      {found && (
        <div className="hist-results" aria-live="polite">
          <p className="muted small">
            {found.total} {t(found.total === 1 ? "match" : "matches")} ·{" "}
            {found.how === "words+meaning" ? t("by words, then by meaning (on this computer)") : t("by words")}
            {found.note && <> · {found.note}</>}
          </p>
          {found.hits.map((h) => (
            <Button key={h.key} className="hist-hit" onPress={() => show(h)}>
              <span className="hist-meta">
                {dayName(h.day)} · {h.time} · {where(h)} · {h.mine ? t("You") : t(h.who)}
                {h.match === "meaning" && <span className="hist-badge">{t("close in meaning")}</span>}
              </span>
              <span className="hist-text">
                <Mark text={h.text} words={words} />
              </span>
            </Button>
          ))}
          {found.hits.length === 0 && <p className="muted">{t("Nothing found. Try fewer words, or another month.")}</p>}
        </div>
      )}
      <div className="hist-months" role="tablist" aria-label={t("Months")}>
        {months.map((m) => (
          <Button key={m.month} className={`hist-month ${m.month === month ? "tc-on" : ""}`} onPress={() => setMonth(m.month)}>
            <b>{monthName(m.month)}</b>
            <span className="muted small">
              {m.count} {t("messages")} · {m.days} {t("days")}
            </span>
          </Button>
        ))}
        {months.length === 0 && <p className="muted small">{t("No messages yet.")}</p>}
      </div>
      <div className="hist-filter" role="group" aria-label={t("Show")}>
        {[["", "All"], ["assistant", "Assistant"], ["team", "Whole team"], ["dm", "Direct"]].map(([k, l]) => (
          <Button key={k} className={`btn btn-sm ${only === k ? "btn-on" : ""}`} onPress={() => setOnly(k)}>
            {k ? ICON[k] : ""} {t(l)}
          </Button>
        ))}
      </div>
      <div className="hist-days">
        {days.map((d) => (
          <div key={d.day} className="hist-day">
            <h3 className="hist-day-label">{dayName(d.day)}</h3>
            {d.messages.map((m) => (
              <div key={m.key} data-key={m.key} className={`hist-row ${m.key === jump ? "hist-jump" : ""}`}>
                <span className="hist-time">{m.time}</span>
                <span className="hist-where muted small">{where(m)}</span>
                <span className="hist-body">
                  <b>{m.mine ? t("You") : t(m.who)}</b> {m.text}
                </span>
              </div>
            ))}
          </div>
        ))}
        {month && days.length === 0 && <p className="muted small">{t("Nothing here for this month.")}</p>}
      </div>
      {held > 0 && (
        <p className="muted small">
          {held} {t("assistant answers from when you had a higher level are held back.")}
        </p>
      )}
    </div>
  );
}
