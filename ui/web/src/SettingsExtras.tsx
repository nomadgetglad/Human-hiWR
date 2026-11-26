// Settings: the 1-10 sensitivity to falling numbers (alerts + how much the assistant may estimate), when that check
// refreshes, and the draft disclaimers and About us.
import { useState } from "react";
import { Button } from "react-aria-components";
import { useProposals } from "./ConfirmCard";
import { useT } from "./i18n";

export function SensitivityDial({ s }: { s: any }) {
  const t = useT();
  const { propose } = useProposals();
  const [v, setV] = useState<number>(s.level);
  const at = s.levels[String(v)];
  return (
    <div className="panel">
      <h2 className="h2">📉 {t("Sensitivity to falling numbers")}</h2>
      <p className="muted small">
        {t("Low: only big drops raise an alert, and the assistant sticks to computed data. High: small dips raise an alert early, and the assistant may estimate from averages. Every estimate is labeled.")}
      </p>
      <div className="dial">
        <span className="small">1</span>
        <input type="range" min={1} max={10} step={1} value={v} onChange={(e) => setV(Number(e.target.value))} aria-label={t("Sensitivity")} />
        <span className="small">10</span>
        <b className="dial-num">{v}</b>
      </div>
      <ul className="settings-list">
        <li>
          {t("Alerts when a number falls")} <b>{at.threshold_pct}%</b> {t("or more below typical")}
          {s.preview[String(v)] != null && (
            <span className="muted small">
              {" "}
              · {t("right now that flags")} {s.preview[String(v)]} {t("item(s)")}
            </span>
          )}
        </li>
        <li>
          {t("The assistant")}: <b>{t(at.ai)}</b>
        </li>
      </ul>
      <div className="row-gap">
        <Button className="btn btn-primary" isDisabled={v === s.level} onPress={() => propose("set_sensitivity", { level: v })}>
          {t("Save")} {v}/10
        </Button>
        {v !== s.level && <span className="muted small">{t("Now")}: {s.level}/10</span>}
      </div>

      <h3 className="h3">🔄 {t("When the check runs")}</h3>
      <div className="row-gap refresh-opts">
        {Object.entries(s.refresh_options as Record<string, string>).map(([k, label]) => (
          <Button key={k} className={`btn btn-sm ${s.refresh === k ? "btn-primary" : ""}`} onPress={() => s.refresh !== k && propose("set_refresh", { mode: k })}>
            {t(label)}
          </Button>
        ))}
      </div>
      <p className="muted small">
        {t("Last check")}: {s.snapshot.at} {s.snapshot.scheduled ? `(${t("scheduled")})` : `(${t("live")})`} ·{" "}
        {s.snapshot.drops.length === 0 ? t("nothing falling") : s.snapshot.drops.map((x: any) => `${x.what} −${x.down_pct}%`).join(", ")}
      </p>
    </div>
  );
}

export function AboutAndDisclaimers() {
  const t = useT();
  return (
    <>
      <div className="panel">
        <h2 className="h2">
          ⚖️ {t("Disclaimers")} <span className="badge">{t("DRAFT: for legal review")}</span>
        </h2>
        <ul className="settings-list small">
          <li>{t("Human's assistant makes suggestions. A person confirms every change, and money always needs two approvers.")}</li>
          <li>{t("Human doesn't give tax, legal, financial or investment advice. Check with a qualified professional before acting on anything about taxes, contracts, payroll or money.")}</li>
          <li>{t("Numbers marked estimated are projections from averages, not records.")}</li>
          <li>{t("Demo businesses use made-up sample data. People, prices and fees in them aren't real.")}</li>
          <li>{t("Your data stays on this computer. The assistant runs locally; photos and files you scan or import are read on the device.")}</li>
          <li>{t("Tax forms shown in the app are copies for reference; the forms you file come from your payroll provider.")}</li>
        </ul>
      </div>
      <div className="panel">
        <h2 className="h2">
          👋 {t("About us")} <span className="badge">{t("DRAFT: edit freely")}</span>
        </h2>
        <p>
          {t("Human hiWR is built in Wilmington by Alexandre, carrying forward his dad's years in image software: the idea that a tool should help you see clearly and say what you mean.")}
        </p>
        <p>
          {t("HiWR is the record: what your team puts in. Human works from it, puts drafts in front of a person, and never moves money or changes access on its own. We built it for small businesses that run on people: kitchens, shops, crews and service teams.")}
        </p>
      </div>
    </>
  );
}
