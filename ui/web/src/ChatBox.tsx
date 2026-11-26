// One chat box for the whole app: the assistant (Home and its own screen) and team chat in Mesh look and work
// the same. Same bubbles (yours on the right), same input, Enter sends and Shift+Enter makes a new line, same Send
// button. The log scrolls inside itself, so the page never jumps.
import { useEffect, useRef, type ReactNode } from "react";
import { Button, Label, TextArea, TextField } from "react-aria-components";
import { useT } from "./i18n";

export type ChatLine = { key: string | number; who: string; text: string; mine: boolean; time?: string };

export function ChatBox({
  lines,
  text,
  setText,
  onSend,
  busy = false,
  busyText,
  empty,
  placeholder,
  error,
}: {
  lines: ChatLine[];
  text: string;
  setText: (s: string) => void;
  onSend: (q: string) => void;
  busy?: boolean;
  busyText?: string;
  empty?: ReactNode;
  placeholder?: string;
  error?: string;
}) {
  const t = useT();
  const log = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [lines.length, busy]);
  const send = () => {
    if (text.trim() && !busy) onSend(text.trim());
  };
  return (
    <div className="chatbox">
      <div className="chat-log" ref={log} aria-live="polite">
        {lines.length === 0 && !busy && (empty ?? <p className="muted small">{t("No messages yet.")}</p>)}
        {lines.map((m) => (
          <div key={m.key} className={`chat-msg ${m.mine ? "chat-user" : "chat-assistant"}`}>
            <span className="chat-who">
              {m.mine ? t("You") : m.who}
              {m.time ? ` · ${m.time}` : ""}
            </span>
            {m.text}
          </div>
        ))}
        {busy && <div className="chat-msg chat-assistant muted">{busyText ?? "…"}</div>}
      </div>
      {error && (
        <p className="callout callout-bad" role="alert">
          {error}
        </p>
      )}
      <form
        className="chat-form"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <TextField className="field" value={text} onChange={setText} aria-label={placeholder ?? t("Message")}>
          <Label className="sr-only">{placeholder ?? t("Message")}</Label>
          <TextArea
            rows={2}
            placeholder={placeholder ?? t("Write a message")}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
        </TextField>
        <Button type="submit" className="btn btn-primary" isDisabled={busy || !text.trim()}>
          {t("Send")}
        </Button>
      </form>
    </div>
  );
}
