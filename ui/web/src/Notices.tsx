// Week schedule, notices that become tasks (deep clean), and tax forms (W-2). Demo data on the demos.
import { useState } from "react";
import { Button } from "react-aria-components";
import { api, usd } from "./api";
import { useProposals } from "./ConfirmCard";
import { useT } from "./i18n";
import { useData } from "./useData";

const DAY = (iso: string) => new Date(iso + "T12:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });

export function WeekSchedule() {
  const t = useT();
  const v = useData<{ week: { day: string; shifts: { name: string; start: number; end: number; closes: boolean }[] }[] }>("/api/schedule");
  if (!v || v.week.length === 0) return null;
  return (
    <div className="panel">
      <h2 className="h2">🗓 {t("This week's schedule")}</h2>
      <div className="week-grid">
        {v.week.map((d) => (
          <div key={d.day} className="week-day">
            <b>{DAY(d.day)}</b>
            {d.shifts.map((s) => (
              <div key={s.name} className={`shift ${s.closes ? "shift-close" : ""}`}>
                {s.name} <span className="muted small">{s.start}–{s.end}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
      <p className="muted small">{t("Highlighted shifts close the kitchen. Sample schedule.")}</p>
    </div>
  );
}

export function NoticesPanel() {
  const t = useT();
  const { propose, version } = useProposals();
  const v = useData<any>("/api/notices", [version]);
  if (!v) return null;
  return (
    <div className="panel">
      <div className="row-between">
        <h2 className="h2">📌 {t("Notices")}</h2>
        {v.can_post && v.has_schedule && (
          <Button className="btn btn-primary btn-sm" onPress={() => propose("post_notice", { kind: "deep_clean" })}>
            {t("Post a deep-clean notice")}
          </Button>
        )}
      </div>
      <p className="muted small">{t("A notice turns into dated tasks for whoever closes each night, one area per night.")}</p>
      {v.notices.length === 0 && <p className="muted">{t("No notices yet.")}</p>}
      {v.notices.map((n: any) => (
        <div key={n.id} className="notice">
          <b>{t(n.title)}</b>{" "}
          <span className="muted small">
            · {t("week of")} {DAY(n.week_start)} · {t("posted by")} {n.posted_by_name}
          </span>
          <ul className="settings-list">
            {n.tasks.map((x: any) => (
              <li key={x.id}>
                <span className={`badge ${x.status === "done" ? "badge-confirmed" : ""}`}>{x.status === "done" ? "DONE" : "OPEN"}</span> {DAY(x.due_on)} ·{" "}
                <b>{x.name}</b> · {x.area}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function TaxFormsAdmin() {
  const t = useT();
  const { propose, version } = useProposals();
  const v = useData<any>("/api/docs", [version]);
  if (!v || !v.can_post) return null;
  return (
    <div className="panel">
      <div className="row-between">
        <h2 className="h2">🧾 {t("Tax forms")}</h2>
        <Button className="btn btn-primary btn-sm" onPress={() => propose("post_w2s", { year: 2025 })}>
          {t("Post 2025 W-2s")}
        </Button>
      </div>
      <p className="muted small">{t("Each person sees only their own form. Every opening is logged. No Social Security numbers or last names.")}</p>
      {v.posted.length === 0 ? (
        <p className="muted">{t("Nothing posted yet.")}</p>
      ) : (
        <ul className="settings-list">
          {v.posted.map((d: any) => (
            <li key={d.id}>
              <span className="badge">{d.kind} {d.year}</span> <b>{d.name}</b>{" "}
              <span className="muted small">
                · {t("opened")} {d.views}×
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function MyDocuments() {
  const t = useT();
  const { version } = useProposals();
  const v = useData<any>("/api/docs", [version]);
  const [open, setOpen] = useState<any>(null);
  if (!v || v.mine.length === 0) return null;
  return (
    <div className="panel">
      <h2 className="h2">🧾 {t("My documents")}</h2>
      <div className="row-gap">
        {v.mine.map((d: any) => (
          <Button key={d.id} className="btn btn-sm" onPress={async () => setOpen(await api(`/api/docs/${d.id}`))}>
            {d.kind} {d.year}
          </Button>
        ))}
      </div>
      {open && (
        <div className="w2">
          <div className="row-between">
            <b>
              {open.kind} {open.year} · {open.name}
            </b>
            <Button className="btn btn-ghost btn-sm" onPress={() => setOpen(null)}>
              {t("Close")}
            </Button>
          </div>
          <p className="muted small">{open.employer}</p>
          <dl className="id-dl">
            <dt>1 {t("Wages, tips, other pay")}</dt>
            <dd>{usd(open.box1_wages)}</dd>
            <dt>2 {t("Federal income tax withheld")}</dt>
            <dd>{usd(open.box2_federal_withheld)}</dd>
            <dt>3 {t("Social Security wages")}</dt>
            <dd>{usd(open.box3_ss_wages)}</dd>
            <dt>4 {t("Social Security tax withheld")}</dt>
            <dd>{usd(open.box4_ss_withheld)}</dd>
            <dt>5 {t("Medicare wages")}</dt>
            <dd>{usd(open.box5_medicare_wages)}</dd>
            <dt>6 {t("Medicare tax withheld")}</dt>
            <dd>{usd(open.box6_medicare_withheld)}</dd>
          </dl>
          <p className="muted small">{t("Opening this form was logged. Questions? Ask the assistant to reach customer service.")}</p>
        </div>
      )}
    </div>
  );
}
