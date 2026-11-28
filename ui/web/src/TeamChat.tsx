// Team chat (HiWR -> Mesh): the team channel plus one-to-one messages. Kept on the record (append-only on the
// server); card numbers and SSNs are refused. Money, pay and approvals stay on their own screens.
import { useEffect, useState } from "react";
import { Button } from "react-aria-components";
import { ChatBox } from "./ChatBox";
import { ChatHistory } from "./ChatHistory";
import { api } from "./api";
import { useT } from "./i18n";

type Msg = { id: number; at: string; from: string; name: string; text: string; mine: boolean };
type Person = { id: string; name: string; title: string };

export function TeamChat() {
  const t = useT();
  const [to, setTo] = useState("team");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const [hist, setHist] = useState<{ q: string; n: number } | null>(null);

  const load = () =>
    api<{ messages: Msg[]; people: Person[] }>(`/api/chat?to=${encodeURIComponent(to)}`)
      .then((r) => {
        setMsgs(r.messages);
        setPeople(r.people);
      })
      .catch((e) => setErr(e.message));
  useEffect(() => {
    setMsgs([]);
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, [to]);

  const send = async (q: string) => {
    setErr("");
    try {
      await api("/api/chat", { to, text: q });
      setText("");
      load();
    } catch (e: any) {
      setErr(e.message);
    }
  };
  const who = to === "team" ? t("Whole team") : people.find((p) => p.id === to)?.name ?? to;

  return (
    <div className="panel team-chat">
      <div className="row-between">
        <h2 className="h2">💬 {t("Team chat")}</h2>
        <Button className="btn btn-sm" onPress={() => setHist(hist ? null : { q: "", n: 0 })}>
          🗂 {t("History")}
        </Button>
      </div>
      {hist && <ChatHistory key={hist.n} query={hist.q} source={hist.q ? "" : to === "team" ? "team" : "dm"} onClose={() => setHist(null)} />}
      <div className="tc-wrap">
        <div className="tc-list" role="tablist" aria-label={t("Conversations")}>
          <Button className={`tc-who ${to === "team" ? "tc-on" : ""}`} onPress={() => setTo("team")}>
            👥 {t("Whole team")}
          </Button>
          {people.map((p) => (
            <Button key={p.id} className={`tc-who ${to === p.id ? "tc-on" : ""}`} onPress={() => setTo(p.id)}>
              {p.name} <span className="muted small">{p.title}</span>
            </Button>
          ))}
          {people.length === 0 && <p className="muted small">{t("Add people on the Team screen to message them.")}</p>}
        </div>
        <div className="tc-thread">
          <p className="muted small">
            {who} · {t("kept on the record; no card numbers. Money and approvals stay on their own screens.")}
          </p>
          <ChatBox
            lines={msgs.map((m) => ({ key: m.id, who: m.name, text: m.text, mine: m.mine, time: m.at.slice(11, 16) }))}
            text={text}
            setText={setText}
            onSend={send}
            error={err}
            placeholder={t("Write a message")}
            onFind={(q) => setHist({ q, n: Date.now() })}
          />
        </div>
      </div>
    </div>
  );
}
