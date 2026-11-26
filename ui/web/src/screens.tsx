import { useState } from "react";
import { Button, Input, Label, ListBox, ListBoxItem, Popover, Select, SelectValue, Tab, TabList, TabPanel, Tabs, TextField } from "react-aria-components";
import { api, usd, type Me } from "./api";
import { useProposals } from "./ConfirmCard";
import { useT } from "./i18n";
import { useData } from "./useData";
import { IdIntake, IdView } from "./IdCard";

const TILE_ICON: Record<string, string> = { team: "🧑‍🤝‍🧑", staff: "👥", inventory: "📦", prep: "🔥", cs: "💬", accounting: "📒", tasks: "☐" };

// ---------------- Home: the main menu ----------------
export function Home({ me, go }: { me: Me; go: (s: string) => void }) {
  const t = useT();
  const items = [...me.menu, ...(me.level < 2 ? [{ id: "tasks", label: "My tasks" }] : [])];
  const team = useData<{ members: any[] }>(me.level >= 2 ? "/api/team" : null);
  return (
    <section>
      {team && (
        <div className="panel team-start row-between">
          <div>
            <h2 className="h2">{t("Your team")}</h2>
            <p className="kpi">
              <b>{team.members.length}</b> <span className="muted">{t(team.members.length === 1 ? "person on the roster" : "people on the roster")}</span>
            </p>
            {team.members.length < 2 && <p className="muted small">{t("Start here: add the people who work with you.")}</p>}
          </div>
          <Button className="btn btn-primary" onPress={() => go("team")}>
            {t("Add staff")}
          </Button>
        </div>
      )}
      <h1 className="h1">{t("Choose a section")}</h1>
      <div className="tiles">
        {items.map((m) => (
          <Button key={m.id} className={`tile tile-${m.id}`} onPress={() => go(m.id)}>
            <span className="tile-icon" aria-hidden>
              {TILE_ICON[m.id]}
            </span>
            <span className="tile-label">{t(m.label)}</span>
          </Button>
        ))}
      </div>
    </section>
  );
}

// ---------------- Team: adding staff is where the app starts ----------------
const LEVELS = ["Staff", "Lead", "Manager", "Finance"];

export function Team() {
  const t = useT();
  const { propose } = useProposals();
  const v = useData<{ members: any[]; can_set_level: boolean; can_set_pay: boolean; can_view_id: boolean; roles: string[]; doc_types: Record<string, string> }>("/api/team");
  const [idFor, setIdFor] = useState<{ id: string; name: string; view: boolean } | null>(null);
  const [first, setFirst] = useState("");
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState("en");
  const [lvl, setLvl] = useState("0");
  const [role, setRole] = useState("none");
  const [rate, setRate] = useState("");
  if (!v) return <p className="muted">{t("The team roster needs Manager level.")}</p>;
  const add = () => {
    const params: Record<string, any> = { first_name: first, title, language, level: Number(lvl) };
    if (v.can_set_pay && role !== "none") {
      params.role = role;
      params.hourly_cents = Math.round(Number(rate) * 100);
    }
    propose("add_member", params);
  };
  return (
    <section>
      <h1 className="h1">{t("Team")}</h1>
      <div className="panel">
        <h2 className="h2">{t("Add staff")}</h2>
        <p className="muted small">{t("First name only. They start at level 0 (Staff); only the account holder raises a level.")}</p>
        <div className="form-row">
          <TextField className="field" value={first} onChange={setFirst} isRequired>
            <Label>{t("First name")}</Label>
            <Input autoComplete="off" />
          </TextField>
          <TextField className="field" value={title} onChange={setTitle} isRequired>
            <Label>{t("Job title")}</Label>
            <Input placeholder="Cook, grill" />
          </TextField>
          <Pick
            label={t("Language")}
            value={language}
            onChange={setLanguage}
            options={[
              ["en", "English"],
              ["es", "Español"],
            ]}
          />
          {v.can_set_level && <Pick label={t("Level")} value={lvl} onChange={setLvl} options={LEVELS.map((n, i) => [String(i), `${i} ${t(n)}`])} />}
          {v.can_set_pay && (
            <>
              <Pick label={t("Schedule role")} value={role} onChange={setRole} options={[["none", t("Not on the schedule")], ...v.roles.map((r) => [r, r])]} />
              {role !== "none" && (
                <TextField className="field" value={rate} onChange={setRate} inputMode="decimal">
                  <Label>{t("Pay ($/hour)")}</Label>
                  <Input placeholder="16.00" />
                </TextField>
              )}
            </>
          )}
          <Button className="btn btn-primary" onPress={add} isDisabled={!first.trim() || !title.trim()}>
            {t("Add")}
          </Button>
        </div>
      </div>
      {idFor &&
        (idFor.view ? (
          <IdView key={idFor.id} member={idFor.id} name={idFor.name} onClose={() => setIdFor(null)} />
        ) : (
          <IdIntake key={idFor.id} member={idFor.id} name={idFor.name} docTypes={v.doc_types} onClose={() => setIdFor(null)} />
        ))}
      <div className="panel">
        <h2 className="h2">
          {t("Roster")} <span className="muted">· {v.members.length}</span>
        </h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("Name")}</th>
                <th>{t("Job title")}</th>
                <th>{t("Level")}</th>
                <th>{t("Language")}</th>
                <th>{t("Schedule role")}</th>
                {v.can_set_pay && <th>{t("Pay")}</th>}
                <th>{t("Sign-in")}</th>
                <th>{t("ID")}</th>
              </tr>
            </thead>
            <tbody>
              {v.members.map((m) => (
                <tr key={m.id}>
                  <td>
                    <b>{m.name}</b>
                    <div className="muted small">{m.id}</div>
                  </td>
                  <td>{m.title}</td>
                  <td>
                    <span className="badge">
                      L{m.level} {t(m.level_name)}
                    </span>
                  </td>
                  <td>{m.language.toUpperCase()}</td>
                  <td className="cap">{m.role ?? "—"}</td>
                  {v.can_set_pay && <td>{m.hourly_cents ? `${usd(m.hourly_cents)}/h` : "—"}</td>}
                  <td>
                    <span className={`badge ${m.sign_in.enrolled ? "badge-ok" : ""}`}>
                      {m.sign_in.enrolled ? t("Authenticator") : m.sign_in.setup_code_open ? t("Code given") : t("Not set up")}
                    </span>
                    {m.sign_in.last_sign_in && <div className="muted small">{m.sign_in.last_sign_in.replace("T", " ").slice(0, 16)}</div>}
                    {m.can_issue_code && (
                      <Button className="btn btn-ghost btn-sm" onPress={() => propose("setup_code", { member_id: m.id })}>
                        {m.sign_in.enrolled ? t("Reset") : t("Setup code")}
                      </Button>
                    )}
                  </td>
                  <td>
                    {m.id_card.on_file ? (
                      <span className={`badge ${m.id_card.expiring ? "badge-warn" : "badge-ok"}`}>
                        {t(m.id_card.doc_label)}
                        {m.id_card.expires_on ? ` · ${m.id_card.expires_on}` : ""}
                      </span>
                    ) : (
                      <span className="badge">{t("Not on file")}</span>
                    )}
                    {m.id_card.on_file && !m.id_card.work_auth_verified && <div className="muted small">{t("Work authorization not checked")}</div>}
                    {v.can_view_id && m.id_card.on_file && (
                      <Button className="btn btn-ghost btn-sm" onPress={() => setIdFor({ id: m.id, name: m.name, view: true })}>
                        {t("Open")}
                      </Button>
                    )}
                    {m.can_enter_id && (
                      <Button className="btn btn-ghost btn-sm" onPress={() => setIdFor({ id: m.id, name: m.name, view: false })}>
                        {m.id_card.on_file ? t("Update") : t("Add ID")}
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function Pick({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: string[][] }) {
  return (
    <Select className="field" selectedKey={value} onSelectionChange={(k) => onChange(String(k))}>
      <Label>{label}</Label>
      <Button className="select-btn">
        <SelectValue />
        <span aria-hidden>▾</span>
      </Button>
      <Popover className="popover">
        <ListBox className="listbox">
          {options.map(([id, text]) => (
            <ListBoxItem key={id} id={id} className="option" textValue={text}>
              {text}
            </ListBoxItem>
          ))}
        </ListBox>
      </Popover>
    </Select>
  );
}

// ---------------- Staff ----------------
export function Staff() {
  const t = useT();
  const { propose } = useProposals();
  const v = useData<any>("/api/staff");
  const [fill, setFill] = useState<any>(null);
  if (!v) return null;
  if (v.view === "own")
    return (
      <section>
        <h1 className="h1">{t("Staff")}</h1>
        <div className="panel">
          <h2 className="h2">{t("Your shift")}</h2>
          <p className="big">{v.my_shift ? `${v.my_shift.start_hour}:00 – ${v.my_shift.end_hour}:00` : "—"}</p>
        </div>
        <div className="panel">
          <h2 className="h2">{t("Your requests")}</h2>
          {v.my_requests.length === 0 ? <p className="muted">—</p> : v.my_requests.map((r: any, i: number) => <p key={i}>{r.kind}</p>)}
        </div>
      </section>
    );
  const openFill = async (role: string) => setFill(await api(`/api/staff/fill?role=${role}`));
  return (
    <section>
      <h1 className="h1">
        {t("Staff")} · {t("Tonight")} <span className="muted">{v.demand.covers} covers</span>
      </h1>
      {v.roles.length === 0 && <p className="muted">{t("No staffing needs set up yet.")}</p>}
      <div className="grid-2">
        {v.roles.map((r: any) => (
          <div key={r.role} className={`panel ${r.short ? "panel-alert" : ""}`}>
            <div className="row-between">
              <h2 className="h2 cap">{r.role}</h2>
              <span className={`badge ${r.short ? "badge-bad" : "badge-ok"}`}>
                {r.short ? `${t("Short")} ${r.short}` : "OK"}
              </span>
            </div>
            <p className="kpi">
              <b>{r.on_shift.length}</b> / {r.need} <span className="muted">{t("On shift")}</span>
            </p>
            <p className="muted small">{r.because}</p>
            <ul className="chips">
              {r.on_shift.map((m: string) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
            {r.short > 0 && (
              <Button className="btn btn-primary btn-wide" onPress={() => openFill(r.role)}>
                {t("Find cover")}
              </Button>
            )}
          </div>
        ))}
      </div>
      {v.callouts.length > 0 && (
        <div className="panel">
          <h2 className="h2">{t("Called out today")}</h2>
          {v.callouts.map((c: any, i: number) => (
            <p key={i}>
              <b>{c.member}</b> ({c.detail}) · approved by {c.actor} {c.at.slice(11)}
            </p>
          ))}
        </div>
      )}
      {fill && (
        <div className="panel">
          <h2 className="h2">
            {t("Find cover")}: <span className="cap">{fill.role}</span> {fill.starts_at.slice(11)}–{fill.ends_at.slice(11)}
          </h2>
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Experience</th>
                <th>Reliability</th>
                <th>Lang</th>
              </tr>
            </thead>
            <tbody>
              {fill.candidates.map((c: any) => (
                <tr key={c.candidate_id}>
                  <td>{c.name}</td>
                  <td>{c.months} mo</td>
                  <td>{c.reliability}</td>
                  <td>{c.language}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <Button className="btn btn-primary btn-wide" onPress={() => propose("send_offers", { role: fill.role, top: 3, hourly_cents: 1600 })}>
            {t("Text them")} (top 3)
          </Button>
          {v.offers.length > 0 && (
            <p className="muted small">
              Offers out: {v.offers.map((o: any) => `${o.name} (${o.answer ?? "waiting"})`).join(", ")}
            </p>
          )}
        </div>
      )}
      <div className="panel">
        <h2 className="h2">{t("HR requests")}</h2>
        {v.hr_requests.length === 0 && <p className="muted">—</p>}
        {v.hr_requests.map((h: any) => (
          <div key={h.id} className="hr-row">
            <div>
              <b>{h.member}</b> · {h.kind.replace("_", " ")}
              {h.from_day && ` ${h.from_day} → ${h.to_day}`}
              <div className="muted small">{h.detail}</div>
            </div>
            {h.kind === "pay_question" ? (
              <div className="hr-actions">
                <Button className="btn btn-sm" onPress={() => propose("hr_decide", { request_id: h.id, status: "answered" })}>
                  {t("Mark answered")}
                </Button>
              </div>
            ) : (
              <div className="hr-actions">
                <Button className="btn btn-sm btn-primary" onPress={() => propose("hr_decide", { request_id: h.id, status: "approved" })}>
                  {t("Approve")}
                </Button>
                <Button className="btn btn-sm" onPress={() => propose("hr_decide", { request_id: h.id, status: "denied" })}>
                  {t("Deny")}
                </Button>
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

// ---------------- Prep: the fryer's station screen ----------------
export function Prep() {
  const t = useT();
  const { propose, refresh } = useProposals();
  const p = useData<any>("/api/prep");
  if (!p) return null;
  if (p.empty)
    return (
      <section>
        <h1 className="h1">{t("My station")}</h1>
        <p className="muted">{t("No stations set up yet.")}</p>
      </section>
    );
  const hhmm = (h: number) => `${((h + 11) % 12) + 1}:00 ${h >= 12 ? "PM" : "AM"}`;
  const move = async (h: number) => {
    await api("/api/demo/clock", { hour: h });
    refresh();
  };
  const fry = p.fry_batches_now;
  return (
    <section className="prep">
      <div className="row-between">
        <h1 className="h1 cap">
          {p.station} · {p.item}
        </h1>
        <div className="clock" role="group" aria-label={t("Move clock")}>
          <span className="muted small">Sample clock {hhmm(p.clock)}</span>
          {[17, 18, 19, 20].filter((h) => h > p.clock).slice(0, 2).map((h) => (
            <Button key={h} className="btn btn-sm" onPress={() => move(h)}>
              → {hhmm(h)}
            </Button>
          ))}
        </div>
      </div>
      <div className={`fry-call ${fry ? "fry-go" : "fry-wait"}`}>
        {fry ? (
          <>
            <span className="fry-big">
              {t("Fry now")}: {fry} {t("batches")}
            </span>
            <span className="fry-sub">
              {p.batch} · {t("uses")} {p.input}
            </span>
            <Button className="btn btn-xl" onPress={() => propose("fry", { batches: fry })}>
              {t("Fry now")} ({fry})
            </Button>
          </>
        ) : (
          <span className="fry-big">{t("Nothing to fry yet")}</span>
        )}
        <span className="fry-sub">
          {t("Next batch about")} <b>{p.next_batch_at}</b>
        </span>
      </div>
      <div className="grid-4">
        <Kpi label={t("Sold today")} value={`${p.sold} ${p.unit}`} sub={p.running} />
        <Kpi label={t("Good on hand")} value={`${p.good_on_hand} ${p.unit}`} sub={`${t("Waste")} ${p.waste} ${p.unit}`} warn={p.waste > 0} />
        <Kpi label={t("Next hour")} value={`${p.next_hour} ${p.unit}`} sub={`${p.reservations} vs ${p.usual_reservations} usual`} />
        <Kpi label={t("Rest of night")} value={`${p.rest_of_night} ${p.unit}`} sub={`${Math.floor(p.input_left / p.input_per_batch)} batches of ${p.input_material} ${t("left in inventory")}`} />
      </div>
      <div className="panel">
        <h2 className="h2">{t("Recent batches")}</h2>
        <ul className="chips">
          {p.recent_batches.map((b: any, i: number) => (
            <li key={i}>
              {b.made_at.slice(11)} · {b.qty} {p.unit} · {b.member}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function Kpi({ label, value, sub, warn }: { label: string; value: string; sub?: string; warn?: boolean }) {
  return (
    <div className={`panel kpi-tile ${warn ? "panel-alert" : ""}`}>
      <span className="stat-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {sub && <span className="muted small">{sub}</span>}
    </div>
  );
}

// ---------------- Inventory: Warehouse, Stew, Lab ----------------
export function Inventory({ tab }: { tab?: string }) {
  const t = useT();
  return (
    <section>
      <h1 className="h1">{t("Inventory")}</h1>
      <Tabs defaultSelectedKey={tab ?? "stew"}>
        <TabList className="tabs" aria-label={t("Inventory")}>
          <Tab id="warehouse" className="tab">
            {t("Warehouse")}
          </Tab>
          <Tab id="stew" className="tab">
            {t("Stew")}
          </Tab>
          <Tab id="lab" className="tab">
            {t("Lab")}
          </Tab>
        </TabList>
        <TabPanel id="warehouse">
          <Warehouse />
        </TabPanel>
        <TabPanel id="stew">
          <Stew />
        </TabPanel>
        <TabPanel id="lab">
          <Lab />
        </TabPanel>
      </Tabs>
    </section>
  );
}

function Warehouse() {
  const t = useT();
  const { propose } = useProposals();
  const w = useData<any>("/api/inventory/warehouse");
  if (!w) return null;
  return (
    <div className="panel">
      <h2 className="h2">{t("Pull list")}</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Aisle</th>
            <th>Item</th>
            <th>{t("Floor")}</th>
            <th>{t("Back")}</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {w.pull_list.map((r: any) => (
            <tr key={r.sku} className={r.status.startsWith("OUT") ? "row-alert" : ""}>
              <td>{r.aisle}</td>
              <td>
                {r.item}
                <div className="muted small">{r.status}</div>
              </td>
              <td>
                {r.floor}/{r.par}
              </td>
              <td>{r.back}</td>
              <td>
                {r.pull > 0 && (
                  <Button className="btn btn-sm btn-primary" onPress={() => propose("pull", { sku: r.sku, qty: r.pull })}>
                    {t("Pull")} {r.pull}
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Stew() {
  const t = useT();
  const s = useData<any>("/api/inventory/stew");
  if (!s) return null;
  const cost = Object.fromEntries((s.cost_cards ?? []).map((c: any) => [c.dish, c]));
  const max = Math.max(...s.portions.map((p: any) => p.portions), 1);
  return (
    <>
      <div className="panel">
        <h2 className="h2">{t("Portions left")}</h2>
        {s.portions.map((p: any) => (
          <div key={p.dish} className="bar-row">
            <span className="bar-name">{p.dish}</span>
            <div className="bar-track" aria-hidden>
              <div className={`bar-fill ${p.portions < 30 ? "bar-low" : ""}`} style={{ width: `${(p.portions / max) * 100}%` }} />
            </div>
            <span className="bar-num">{p.portions}</span>
            <span className="muted small bar-why">
              {t("limited by")} {p.limited_by}
              {cost[p.dish] && ` · ${t("Food cost")} ${cost[p.dish].food_cost_pct}%`}
            </span>
          </div>
        ))}
        {!s.shows_costs && <p className="muted small">Costs are hidden at your level.</p>}
      </div>
      {s.tickets.length > 0 && (
        <div className="panel">
          <h2 className="h2">{t("Open tickets")}</h2>
          {s.tickets.map((tk: any) => (
            <p key={tk.id}>
              <span className="badge badge-warn">{tk.id}</span> {tk.status} · {tk.subject}
            </p>
          ))}
        </div>
      )}
    </>
  );
}

function Lab() {
  const l = useData<any>("/api/inventory/lab");
  return <div className="panel muted">{l?.note}</div>;
}

// ---------------- Customer service: hidden-ingredient check ----------------
export function CustomerService() {
  const t = useT();
  const dishes = useData<string[]>("/api/cs/dishes") ?? [];
  const [dish, setDish] = useState<string>("House burger");
  const [note, setNote] = useState("no tomato");
  const [res, setRes] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <section>
      <h1 className="h1">{t("Customer service")}</h1>
      <div className="panel">
        <h2 className="h2">{t("Order note check")}</h2>
        <div className="form-row">
          <Select className="field" selectedKey={dish} onSelectionChange={(k) => setDish(String(k))}>
            <Label>{t("Dish")}</Label>
            <Button className="select-btn">
              <SelectValue />
              <span aria-hidden>▾</span>
            </Button>
            <Popover className="popover">
              <ListBox className="listbox">
                {dishes.map((d) => (
                  <ListBoxItem key={d} id={d} className="option">
                    {d}
                  </ListBoxItem>
                ))}
              </ListBox>
            </Popover>
          </Select>
          <TextField className="field" value={note} onChange={setNote}>
            <Label>{t("Guest note")}</Label>
            <Input />
          </TextField>
          <Button className="btn btn-primary" onPress={async () => setRes(await api("/api/cs/check", { dish, note }))}>
            {t("Check")}
          </Button>
        </div>
        {res && (
          <p className={`callout ${res.ok ? "callout-ok" : "callout-bad"}`} role="status">
            {res.text}
          </p>
        )}
      </div>
    </section>
  );
}

// ---------------- Accounting (Finance level and up) ----------------
export function Accounting() {
  const t = useT();
  const { propose } = useProposals();
  const rows = useData<any[]>("/api/accounting/money-actions");
  if (!rows) return <p className="muted">Accounting needs Finance level.</p>;
  return (
    <section>
      <h1 className="h1">{t("Accounting")}</h1>
      <div className="panel">
        <h2 className="h2">{t("Money waiting for approval")}</h2>
        {rows.map((a) => (
          <div key={a.id} className="money-row">
            <div>
              <div className="row-gap">
                <span className="badge">{a.scope}</span>
                <b className="money-amt">{usd(a.amount_cents)}</b>
              </div>
              <div>{a.description}</div>
              <div className="muted small">
                Requested by {a.requested_by} · approvals: {a.approvals.map((x: any) => `${x.approver} (${x.decision})`).join(", ") || "none yet"}
              </div>
              <span className={`badge ${a.status.startsWith("APPROVED") ? "badge-ok" : a.status === "REJECTED" ? "badge-bad" : "badge-warn"}`}>{a.status}</span>
            </div>
            {a.i_can_approve && a.status.startsWith("PENDING") && (
              <div className="hr-actions">
                <Button className="btn btn-sm btn-primary" onPress={() => propose("money_decide", { money_action_id: a.id, decision: "approve" })}>
                  {t("Approve")}
                </Button>
                <Button className="btn btn-sm" onPress={() => propose("money_decide", { money_action_id: a.id, decision: "reject" })}>
                  {t("Reject")}
                </Button>
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

// ---------------- My tasks (employees) ----------------
export function Tasks() {
  const t = useT();
  const { propose } = useProposals();
  const rows = useData<any[]>("/api/tasks") ?? [];
  return (
    <section>
      <h1 className="h1">{t("My tasks")}</h1>
      <div className="panel">
        {rows.map((r) => (
          <div key={r.id} className={`task-row ${r.status === "done" ? "task-done" : ""}`}>
            <span className="task-box" aria-hidden>
              {r.status === "done" ? "☑" : "☐"}
            </span>
            <span className="task-title">{r.title}</span>
            {r.status !== "done" && (
              <Button className="btn btn-sm btn-primary" onPress={() => propose("task_done", { assignment_id: r.id })}>
                {t("Mark done")}
              </Button>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
