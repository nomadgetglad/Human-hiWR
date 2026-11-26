// Demo scene (Human logo -> "Demo scene for Dad"): a guided walk-through on demo data of how prior invoices and
// payments settle the numbers Human works from. Read only.
import { useState } from "react";
import { Button } from "react-aria-components";
import { usd } from "./api";
import { useT } from "./i18n";
import { useData } from "./useData";

const STATUS: Record<string, string> = { paid: "PAID", paid_short: "PAID FOR WHAT ARRIVED", disputed: "DISPUTED" };

export function Scene({ go }: { go: (s: string) => void }) {
  const t = useT();
  const v = useData<any>("/api/scene");
  const [i, setI] = useState(0);
  if (!v) return <p className="muted">{t("The demo scene runs on the demos. Open one from the Human logo.")}</p>;
  const money = v.shows_costs;
  const steps = [
    {
      title: "1. Every invoice tells two stories",
      body: "What the vendor billed, and what actually came through the door. Human keeps both, side by side.",
      view: (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("Invoice")}</th>
                <th>{t("Item")}</th>
                <th>{t("Billed / received")}</th>
                {money && <th>{t("Billed $ / paid $")}</th>}
                <th>{t("Status")}</th>
              </tr>
            </thead>
            <tbody>
              {v.invoices.map((x: any) => (
                <tr key={x.number}>
                  <td>
                    <b>{x.number}</b>
                    <div className="muted small">{x.vendor}</div>
                  </td>
                  <td>{x.material}</td>
                  <td>
                    {x.billed_qty} / {x.received_qty}
                  </td>
                  {money && (
                    <td>
                      {usd(x.billed_cents)} / {usd(x.paid_cents)}
                    </td>
                  )}
                  <td>
                    <span className={`badge ${x.status === "disputed" ? "badge-refused" : "badge-confirmed"}`}>{STATUS[x.status] ?? x.status}</span>
                    <div className="muted small">{x.note}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ),
    },
    {
      title: "2. Payment settles it",
      body: "Only what was paid for what arrived counts as settled. A short or disputed invoice is not guessed at: the gap stays in anticipated costs, on a ticket, until two certified people approve how it is resolved.",
      view: (
        <ul className="settings-list tips">
          {v.invoices.map((x: any) => (
            <li key={x.number}>
              <b>{x.number}</b> · {x.settles ? t("settled: it now feeds the real cost") : t("not settled: waiting on the vendor and two approvals")}
              {money && x.anticipated_cents ? (
                <>
                  {" "}
                  · {t("held as anticipated")}: <b>{usd(x.anticipated_cents)}</b>
                </>
              ) : null}
            </li>
          ))}
          {money && v.anticipated_cents ? (
            <li>
              {t("Total still anticipated, not counted twice")}: <b>{usd(v.anticipated_cents)}</b>
            </li>
          ) : null}
        </ul>
      ),
    },
    {
      title: "3. The algorithm learns the real cost",
      body: "Settled invoices replace the catalog price with what you really paid per unit. Every recipe that uses that item updates its food cost and margin.",
      view: money ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("Item")}</th>
                  <th>{t("Catalog price")}</th>
                  <th>{t("Paid, per unit")}</th>
                </tr>
              </thead>
              <tbody>
                {v.units.map((u: any) => (
                  <tr key={u.material}>
                    <td>{u.material}</td>
                    <td>{u.before}¢</td>
                    <td>
                      <b>{u.after}¢</b> {u.after > u.before && <span className="badge badge-refused">{t("UP")}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("Dish")}</th>
                  <th>{t("Price")}</th>
                  <th>{t("Food cost before → after")}</th>
                  <th>{t("Margin before → after")}</th>
                </tr>
              </thead>
              <tbody>
                {v.dishes.map((x: any) => (
                  <tr key={x.dish}>
                    <td>
                      <b>{x.dish}</b>
                    </td>
                    <td>{usd(x.price)}</td>
                    <td>
                      {usd(x.cost_before)} → <b>{usd(x.cost_after)}</b>
                    </td>
                    <td>
                      {x.margin_before}% → <b>{x.margin_after}%</b>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="muted">{t("Costs and margins show for managers and up.")}</p>
      ),
    },
    {
      title: "4. Nothing moves on its own",
      body: "Refunds, credits and payroll are proposals. Each needs two certified approvers, and the person who asked cannot approve it.",
      view: (
        <ul className="settings-list">
          {v.approvals.map((a: any, n: number) => (
            <li key={n}>
              <span className="badge">{a.scope}</span> {a.amount != null && <b>{usd(a.amount)}</b>} {a.description}{" "}
              <span className="muted small">· {a.status}</span>
            </li>
          ))}
        </ul>
      ),
    },
    {
      title: "5. Your turn",
      body: "Try it for real on the demo: approve or reject in Accounting, bring in register sales with HiWR → Import, or ask the assistant on Home why fish tacos cost more this week.",
      view: (
        <div className="row-gap">
          <Button className="btn btn-primary" onPress={() => go("accounting")}>
            {t("Accounting")}
          </Button>
          <Button className="btn" onPress={() => go("home")}>
            {t("Ask the assistant")}
          </Button>
          <Button className="btn" onPress={() => go("mesh")}>
            Mesh
          </Button>
        </div>
      ),
    },
  ];
  const s = steps[i];
  return (
    <section>
      <h1 className="h1">🎬 {t("Prior invoices settle the numbers")}</h1>
      <div className="panel scene">
        <p className="muted small">
          {i + 1} / {steps.length} · {t("demo data")}
        </p>
        <h2 className="h2">{t(s.title)}</h2>
        <p>{t(s.body)}</p>
        {s.view}
        <div className="row-gap tour-actions">
          {i > 0 && (
            <Button className="btn" onPress={() => setI(i - 1)}>
              {t("Previous")}
            </Button>
          )}
          {i < steps.length - 1 && (
            <Button className="btn btn-primary" onPress={() => setI(i + 1)}>
              {t("Next")}
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}
