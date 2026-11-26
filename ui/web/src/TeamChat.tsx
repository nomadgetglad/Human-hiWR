// Team chat (HiWR -> Mesh): the team channel plus one-to-one messages. Kept on the record (append-only on the
// server); card numbers and SSNs are refused. Money, pay and approvals stay on their own screens.
import { useEffect, useRef, useState } from "react";
import { Button, Label, TextArea, TextField } from "react-aria-components";
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
  const end = useRef<HTMLDivElement>(null);

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
  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [msgs.length]);

  const send = async () => {
    const q = text.trim();
    if (!q) return;
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
      <h2 className="h2">💬 {t("Team chat")}</h2>
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
          <div className="tc-log" aria-live="polite">
            {msgs.length === 0 && <p className="muted">{t("No messages yet.")}</p>}
            {msgs.map((m) => (
              <div key={m.id} className={`chat-msg ${m.mine ? "chat-user" : "chat-assistant"}`}>
                <span className="chat-who">
                  {m.mine ? t("You") : m.name} · {m.at.slice(11, 16)}
                </span>
                {m.text}
              </div>
            ))}
            <div ref={end} />
          </div>
          {err && (
            <p className="callout callout-bad" role="alert">
              {err}
            </p>
          )}
          <form
            className="chat-form"
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <TextField className="field" value={text} onChange={setText} aria-label={t("Message")}>
              <Label className="sr-only">{t("Message")}</Label>
              <TextArea
                rows={2}
                placeholder={t("Write a message")}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
              />
            </TextField>
            <Button type="submit" className="btn btn-primary" isDisabled={!text.trim()}>
              {t("Send")}
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
