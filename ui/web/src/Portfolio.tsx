// Portfolio: several lines in one account. Each line's RPM in its own unit (pipes/hour, clients/day,
// table turns per table-hour) and what's next on the schedule; margins per line; lots; appointments with their
// own bookkeeping and late fees that wait for a person (alert one business day ahead, two approvers to charge).
import { useState } from "react";
import { Button, Input, Label, TextField } from "react-aria-components";
import { api, usd } from "./api";
import { useProposals } from "./ConfirmCard";
import { useT } from "./i18n";
import { useData } from "./useData";

const PER: Record<string, string> = { hour: "per hour", day: "per day", "table-hour": "per table per hour" };
const STATE: Record<string, [string, string]> = {
  paid: ["PAID", "badge-confirmed"],
  open: ["OPEN", ""],
  alert: ["DUE NEXT BUSINESS DAY", "badge-refused"],
  late_fee_pending: ["PAST DUE", "badge-refused"],
  late_fee_awaiting_approval: ["FEE PENDING - DOUBLE APPROVAL", "badge-refused"],
  late_fee_charged: ["LATE FEE CHARGED", "badge-refused"],
};
const when = (s: string) => s.replace("T", " ");

export function Portfolio() {
  const t = useT();
  const { propose, version } = useProposals();
  const v = useData<any>("/api/portfolio", [version]);
  const [hours, setHours] = useState("1.5");
  const [fee, setFee] = useState<any>(null);
  const [client, setClient] = useState("");
  if (!v) return <p className="muted">{t("The portfolio needs Lead level or higher.")}</p>;
  const money = v.shows_money;
  return (
    <section>
      <h1 className="h1">📊 {t("Portfolio")}</h1>
      {money && (
        <div className="panel row-between">
          <div>
            <p className="kpi">
              <b>{v.margin_pct}%</b> <span className="muted">{t("margin across all lines")}</span>
            </p>
            <p className="muted small">
              {usd(v.revenue_cents)} {t("revenue")} · {usd(v.cost_cents)} {t("materials and labor")} · {t("last 5 days, sample data")}
            </p>
          </div>
          {v.next_up && (
            <div className="next-up">
              <span className="muted small">{t("Next client to fulfill")}</span>
              <b>{v.next_up.client}</b>
              <span className="small">
                {v.next_up.what} · {when(v.next_up.due_at)}
              </span>
            </div>
          )}
        </div>
      )}

      <div className="rpm-grid">
        {v.lines.map((x: any) => (
          <div key={x.id} className="panel rpm">
            <h2 className="h2">{t(x.name)}</h2>
            <p className="rpm-num">
              <b>{x.rpm}</b> <span className="muted">
                {t(x.unit)} {t(PER[x.per] ?? x.per)}
              </span>
            </p>
            {x.minutes_per_unit && (
              <p className="small">
                {x.minutes_per_unit} {t("min per pipe")}
              </p>
            )}
            {money && (
              <p className="small">
                {t("Margin")} <b>{x.margin_pct}%</b> · {usd(x.revenue_cents)}
                {x.avg_ticket_cents ? (
                  <>
                    {" "}
                    · {t("avg visit")} {usd(x.avg_ticket_cents)}
                  </>
                ) : null}
              </p>
            )}
            {x.next && (
              <p className="small next-line">
                ⏭ {t("Next")}: <b>{x.next.client}</b> · {x.next.what} · {when(x.next.due_at)}
              </p>
            )}
          </div>
        ))}
      </div>

      <div className="panel">
        <h2 className="h2">🔧 {t("Technician fee calculator")}</h2>
        <p className="muted small">
          {t("Billed hours × rate, with a minimum, plus a trip fee per visit.")} ({usd(v.fee_rule.tech_rate_cents)}/h, {v.fee_rule.min_hours} h {t("minimum")}, {usd(v.fee_rule.trip_cents)} {t("trip")})
        </p>
        <div className="form-row">
          <TextField className="field" value={hours} onChange={setHours} inputMode="decimal">
            <Label>{t("Hours on site")}</Label>
            <Input />
          </TextField>
          <Button className="btn btn-primary" onPress={async () => setFee(await api(`/api/portfolio/tech-fee?hours=${encodeURIComponent(hours)}`))}>
            {t("Calculate")}
          </Button>
        </div>
        {fee && (
          <p className="callout callout-ok" role="status">
            {fee.billed_hours} h × {usd(fee.rate_cents)} = {usd(fee.labor_cents)} + {t("trip")} {usd(fee.trip_cents)} = <b>{usd(fee.total_cents)}</b>
          </p>
        )}
      </div>

      {v.appointments.length > 0 && (
        <div className="panel">
          <h2 className="h2">📅 {t("Appointments and their books")}</h2>
          <p className="muted small">
            {t("Each appointment keeps its own ledger. One business day before a balance is due you're alerted, with a reminder ready to send, so the late fee can be avoided. A late fee is only proposed, and needs two approvers.")}{" "}
            ({t("Late fee")}: {usd(v.fee_rule.flat_cents)} {t("or")} {Math.round(v.fee_rule.pct * 100)}%, {t("whichever is more")})
          </p>
          {v.appointments.map((a: any) => (
            <div key={a.id} className="appt">
              <div className="row-between">
                <div>
                  <b>{a.client}</b> <span className="muted small">· {a.what} · {when(a.starts_at)}</span>
                </div>
                <span className={`badge ${STATE[a.state]?.[1] ?? ""}`}>{t(STATE[a.state]?.[0] ?? a.state)}</span>
              </div>
              <p className="small">
                {t("Balance")} <b>{usd(a.balance_cents)}</b> · {t("due")} {a.due_on} · {t("alert on")} {a.alert_on}
                {a.fee_if_late_cents > 0 && a.state !== "late_fee_charged" && (
                  <>
                    {" "}
                    · {t("fee if late")} {usd(a.fee_if_late_cents)}
                  </>
                )}
              </p>
              <ul className="ledger">
                {a.ledger.map((l: any, i: number) => (
                  <li key={i} className="small">
                    {l.at.slice(0, 10)} · {t(l.kind)} · {l.kind === "invoice" || l.kind === "late_fee" ? "+" : "−"}
                    {usd(l.amount_cents)}
                  </li>
                ))}
              </ul>
              <div className="row-gap">
                {a.reminder && (
                  <Button className="btn btn-sm" onPress={() => navigator.clipboard?.writeText(a.reminder)}>
                    {t("Copy reminder (you send it)")}
                  </Button>
                )}
                {a.state === "late_fee_pending" && (
                  <Button className="btn btn-sm btn-primary" onPress={() => propose("propose_late_fee", { appt_id: a.id })}>
                    {t("Propose late fee")}
                  </Button>
                )}
                {money && a.balance_cents > 0 && (
                  <Button className="btn btn-sm" onPress={() => propose("record_payment", { appt_id: a.id, amount_cents: a.balance_cents })}>
                    {t("Record payment received")}
                  </Button>
                )}
              </div>
            </div>
          ))}
          <h3 className="h3">{t("Open slots")}</h3>
          <div className="form-row">
            <TextField className="field" value={client} onChange={setClient}>
              <Label>{t("Book for (client)")}</Label>
              <Input placeholder="Cape Fear Brewing" />
            </TextField>
          </div>
          <div className="row-gap slots">
            {v.slots.service?.slice(0, 6).map((s: string) => (
              <Button
                key={s}
                className="btn btn-sm"
                isDisabled={!client.trim()}
                onPress={() => propose("book_appointment", { line: "service", client, starts_at: s, what: "Service visit", price_cents: 22000, deposit_cents: 5000 })}
              >
                🔧 {when(s)}
              </Button>
            ))}
          </div>
        </div>
      )}

      <div className="panel">
        <h2 className="h2">🏷 {t("Lots")}</h2>
        <p className="muted small">{t("Every batch followed from receiving, through production, to sale.")}</p>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("Lot")}</th>
                <th>{t("Material")}</th>
                <th>{t("Received")}</th>
                <th>{t("Left")}</th>
                <th>{t("Last move")}</th>
              </tr>
            </thead>
            <tbody>
              {v.lots.map((l: any) => {
                const last = l.moves[l.moves.length - 1];
                return (
                  <tr key={l.lot}>
                    <td>
                      <b>{l.lot}</b>
                      <div className="muted small">{l.vendor}</div>
                    </td>
                    <td>{l.material}</td>
                    <td>
                      {l.qty} {l.unit} · {l.received_on}
                    </td>
                    <td>
                      {l.left} {l.unit}
                    </td>
                    <td className="small">
                      {last?.kind} {last?.ref}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
