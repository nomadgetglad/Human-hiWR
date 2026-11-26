import type React from "react";
import { useEffect, useRef, useState } from "react";
import { Button, Input, Label, ListBox, ListBoxItem, Popover, Select, SelectValue, Tab, TabList, TabPanel, Tabs, TextArea, TextField } from "react-aria-components";
import { api, usd, type Me } from "./api";
import { useProposals } from "./ConfirmCard";
import { useT } from "./i18n";
import { useData } from "./useData";
import { TeamChat } from "./TeamChat";
import { ChatBox } from "./ChatBox";
import { MyDocuments, NoticesPanel, TaxFormsAdmin, WeekSchedule } from "./Notices";
import { ScanDialog } from "./Scan";
import { sampleNote, sampleQr, sampleUpc } from "./samples";
import { BUSINESSES, switchBusiness } from "./TopBar";
import { IdIntake, IdView } from "./IdCard";

const TILE_ICON: Record<string, string> = { team: "🧑‍🤝‍🧑", staff: "👥", inventory: "📦", prep: "🔥", cs: "💬", accounting: "📒", tasks: "☐", assistant: "✨" };

// ---------------- Home: the main menu ----------------
export function Home({ me, go }: { me: Me; go: (s: string) => void }) {
  const t = useT();
  const items = me.menu;
  const team = useData<{ members: any[] }>(me.level >= 2 ? "/api/team" : null);
  return (
    <section>
      <Assistant embedded me={me} />
      <MyDocuments />
      {me.sample && <DemoTry me={me} go={go} />}
      {me.sample && (
        <div className="panel row-between">
          <div>
            <h2 className="h2">🎬 {t("Prior invoices settle the numbers")}</h2>
            <p className="muted small">{t("A five-step demo scene: invoices, payments, real costs and two-person approvals.")}</p>
          </div>
          <Button className="btn btn-primary" onPress={() => go("scene")}>
            {t("Watch the scene")}
          </Button>
        </div>
      )}
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
      {me.level >= 4 && !me.sample && team && team.members.length < 2 && (
        <div className="panel">
          <h2 className="h2">{t("Try a demo before you buy a plan")}</h2>
          <p className="muted small">{t("Pick the one closest to your business. Demos reset; your own business stays as it is.")}</p>
          <div className="tiles">
            {BUSINESSES.filter((b) => b.demo).map((b) => (
              <Button key={b.key} className="tile" onPress={() => switchBusiness(me, b)}>
                <span className="tile-icon" aria-hidden>
                  {b.icon}
                </span>
                <span className="tile-label">{t(b.name)}</span>
                <span className="muted small">{t(b.detail)}</span>
              </Button>
            ))}
          </div>
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

// ---------------- Demo: "Try it" with ready-made examples ----------------
// Scan orders, replenish stock and feed Human, on demo data. Every change still goes through a confirm card.
function DemoTry({ me, go }: { me: Me; go: (s: string) => void }) {
  const t = useT();
  const { propose } = useProposals();
  const [sample, setSample] = useState<string | null>(null);
  const [scanOpen, setScanOpen] = useState(false);
  const pulls = useData<any>(me.menu.some((m) => m.children?.includes("warehouse")) ? "/api/inventory/warehouse" : null);
  const dishes = useData<string[]>(me.menu.some((m) => m.id === "cs") ? "/api/cs/dishes" : null) ?? [];
  const hasPrep = me.menu.some((m) => m.id === "prep");
  const canManage = me.level >= 2;
  const scan = async (make: () => Promise<string> | string) => {
    setSample(await make());
    setScanOpen(true);
  };
  const firstPull = pulls?.pull_list?.find((p: any) => p.pull > 0);
  const tonight = dishes.slice(0, 3).map((d, i) => ({ item: d, qty: [6, 4, 3][i], hour: 19 }));
  return (
    <div className="panel demo-try">
      <h2 className="h2">🧪 {t("Try it on the demo")}</h2>
      <p className="muted small">{t("Ready-made examples. Nothing changes until you confirm, and demos reset.")}</p>
      <div className="try-grid">
        <div>
          <h3 className="h3">📷 {t("Scan orders")}</h3>
          {dishes.length > 0 && (
            <Button className="btn btn-sm btn-wide" onPress={() => scan(() => sampleQr(`ORDER POS-1207 table 12: 2 ${dishes[0]}, 1 ${dishes[1] ?? dishes[0]}`))}>
              {t("An order ticket (QR code)")}
            </Button>
          )}
          <Button className="btn btn-sm btn-wide" onPress={() => scan(() => sampleUpc("036000291452"))}>
            {t("A product label (UPC)")}
          </Button>
          <Button className="btn btn-sm btn-wide" onPress={() => scan(() => sampleNote(["Sysco INV-8812", "White fish 160 oz", "Received 60 oz"]))}>
            {t("A delivery note (text)")}
          </Button>
        </div>
        <div>
          <h3 className="h3">📦 {t("Replenish stock")}</h3>
          {firstPull && (
            <Button className="btn btn-sm btn-wide" onPress={() => propose("pull", { sku: firstPull.sku, qty: firstPull.pull })}>
              {t("Pull to the floor")}: {firstPull.item} ×{firstPull.pull}
            </Button>
          )}
          {hasPrep && (
            <Button className="btn btn-sm btn-wide" onPress={() => propose("fry", { batches: 2 })}>
              {t("Fry 2 batches of chips")}
            </Button>
          )}
          <Button className="btn btn-sm btn-wide" onPress={() => go("inventory")}>
            {t("See what's low")}
          </Button>
          {!firstPull && !hasPrep && <p className="muted small">{t("This demo has no shelves or fryer to restock.")}</p>}
        </div>
        <div>
          <h3 className="h3">🍽 {t("Feed Human")}</h3>
          {canManage && tonight.length > 0 && (
            <Button className="btn btn-sm btn-wide" onPress={() => propose("import_sales", { rows: tonight })}>
              {t("Import tonight's register sales")}
            </Button>
          )}
          <Button className="btn btn-sm btn-wide" onPress={() => go("mesh")}>
            {t("Post to the team in Mesh")}
          </Button>
          <Button className="btn btn-sm btn-wide" onPress={() => document.querySelector<HTMLTextAreaElement>(".assistant-home textarea")?.focus()}>
            {t("Ask the assistant (top of Home)")}
          </Button>
        </div>
      </div>
      <ScanDialog isOpen={scanOpen} sample={sample} onClose={() => { setScanOpen(false); setSample(null); }} />
    </div>
  );
}

// ---------------- Team: adding staff is where the app starts ----------------
const LEVELS = ["Staff", "Lead", "Manager", "Finance"];

export function Team() {
  const t = useT();
  const { propose } = useProposals();
  const v = useData<{ members: any[]; can_set_level: boolean; can_set_pay: boolean; can_view_id: boolean; roles: string[]; doc_types: Record<string, string>; job_roles: string[] }>("/api/team");
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
          <Pick
            label={t("Job role")}
            value={title}
            onChange={setTitle}
            placeholder={t("Pick a job role")}
            options={v.job_roles.map((r) => [r, r])}
          />
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
                <th>{t("Job role")}</th>
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

function Pick({ label, value, onChange, options, placeholder }: { label: string; value: string; onChange: (v: string) => void; options: string[][]; placeholder?: string }) {
  return (
    <Select className="field" selectedKey={value || null} onSelectionChange={(k) => onChange(String(k))} placeholder={placeholder}>
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

// ---------------- Mesh screen (HiWR -> Mesh): team chat + who makes what ----------------
export function MeshScreen() {
  const t = useT();
  return (
    <section>
      <h1 className="h1">🕸 {t("Mesh")}</h1>
      <TeamChat />
      <NoticesPanel />
      <WeekSchedule />
      <MeshPanel />
    </section>
  );
}

// ---------------- Mesh: who makes what, refreshed from order history ----------------
function MeshPanel() {
  const t = useT();
  const v = useData<any>("/api/mesh");
  const [who, setWho] = useState<string | null>(null);
  if (!v || v.lines === 0) return null;
  const items = Object.keys(v.by_item);
  const person = v.by_member.find((p: any) => p.member === who) ?? v.by_member[0];
  const pct = (x: number) => `${x}%`;
  return (
    <div className="panel mesh">
      <h2 className="h2">🕸 {t("Mesh")}</h2>
      <p className="muted small">
        {t("Who makes what, how fast, and how often it's remade, refreshed from the last two weeks of orders.")} ({v.lines} {t("order lines")})
      </p>
      {!v.own && (
        <>
          <h3 className="h3">{t("Best fit per item")}</h3>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("Item")}</th>
                  <th>{t("Best fit")}</th>
                  <th>{t("Made")}</th>
                  <th>{t("Avg time")}</th>
                  <th>{t("Remade")}</th>
                  <th>{t("Also makes it")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => {
                  const b = v.best[it];
                  const others = v.by_item[it].filter((x: any) => !b || x.member !== b.member);
                  return (
                    <tr key={it}>
                      <td>
                        <b>{it}</b>
                      </td>
                      <td>{b ? <span className="badge badge-confirmed">{b.name}</span> : <span className="muted">—</span>}</td>
                      <td>{b?.made ?? "—"}</td>
                      <td>{b?.avg_min != null ? `${b.avg_min} min` : "—"}</td>
                      <td>{b ? pct(b.remake_pct) : "—"}</td>
                      <td className="small">
                        {others.map((x: any) => `${x.name} (${x.made}, ${x.avg_min ?? "—"} min, ${pct(x.remake_pct)})`).join(" · ") || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      <h3 className="h3">{v.own ? t("Your items") : t("Each person's items")}</h3>
      {!v.own && (
        <div className="row-gap mesh-people">
          {v.by_member.map((p: any) => (
            <Button key={p.member} className={`btn btn-sm ${p.member === person.member ? "btn-primary" : ""}`} onPress={() => setWho(p.member)}>
              {p.name}
            </Button>
          ))}
        </div>
      )}
      <ul className="settings-list">
        {person.items.map((x: any) => (
          <li key={x.item}>
            <b>{x.item}</b>{" "}
            <span className="muted small">
              · {x.made} {t("made")} · {x.avg_min ?? "—"} min · {pct(x.remake_pct)} {t("remade")} · {t("last")} {x.last}
            </span>
            {v.best[x.item]?.member === x.member && <span className="badge badge-confirmed"> {t("BEST FIT")}</span>}
          </li>
        ))}
      </ul>
    </div>
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
        <MeshPanel />
      </section>
    );
  const openFill = async (role: string) => setFill(await api(`/api/staff/fill?role=${role}`));
  return (
    <section>
      <MeshPanel />
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
export function Inventory({ tab, tabs }: { tab?: string; tabs: string[] }) {
  const t = useT();
  // Only the parts that have something in them (the server decides which).
  const all: [string, string, React.ReactNode][] = [
    ["warehouse", "Warehouse", <Warehouse />],
    ["stew", "Stew", <Stew />],
    ["lab", "Lab", <Lab />],
  ];
  const shown = all.filter(([id]) => tabs.includes(id));
  const first = tab && tabs.includes(tab) ? tab : tabs.includes("stew") ? "stew" : tabs[0];
  return (
    <section>
      <h1 className="h1">{t("Inventory")}</h1>
      <Tabs defaultSelectedKey={first}>
        <TabList className="tabs" aria-label={t("Inventory")}>
          {shown.map(([id, label]) => (
            <Tab key={id} id={id} className="tab">
              {t(label)}
            </Tab>
          ))}
        </TabList>
        {shown.map(([id, , panel]) => (
          <TabPanel key={id} id={id}>
            {panel}
          </TabPanel>
        ))}
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
  const t = useT();
  const l = useData<any>("/api/inventory/lab");
  if (!l) return null;
  if (!l.jobs.length) return <div className="panel muted">{l.note}</div>;
  const STATUS: Record<string, string> = { passed: "PASSED", failed: "FAILED", "in progress": "IN PROGRESS", "not started": "NOT STARTED" };
  return (
    <>
      {l.jobs.map((j: any) => (
        <div key={j.id} className="panel">
          <h2 className="h2">{j.name}</h2>
          <p className="muted small">
            {t("Permit")} {j.permit} · {j.starts_on} → {j.target_end}
            {l.shows_costs && j.contract != null && <> · {t("Contract")} {usd(j.contract)}</>}
          </p>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t("Phase")}</th>
                  <th>{t("Inspection")}</th>
                  {l.shows_costs && <th>{t("Crew labor / budget")}</th>}
                </tr>
              </thead>
              <tbody>
                {j.phases.map((p: any) => (
                  <tr key={p.seq}>
                    <td>{p.seq}</td>
                    <td>{p.phase}</td>
                    <td>
                      <span className={`badge ${p.status === "failed" ? "badge-refused" : p.status === "passed" ? "badge-confirmed" : ""}`}>{STATUS[p.status] ?? p.status}</span>{" "}
                      <span className="muted small">{p.inspection}</span>
                    </td>
                    {l.shows_costs && (
                      <td>
                        {usd(p.labor)} / {usd(p.labor_budget)} {p.over && <span className="badge badge-refused">OVER</span>}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h3 className="h3">{t("Subcontractors")}</h3>
          <ul className="settings-list">
            {j.subs.map((s: any) => (
              <li key={s.sub}>
                <b>{s.sub}</b> <span className="muted small">· {s.scope}{s.amount != null ? ` · ${usd(s.amount)}` : ""}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {l.tickets.length > 0 && (
        <div className="panel">
          <h2 className="h2">{t("Open tickets")}</h2>
          <ul className="settings-list">
            {l.tickets.map((tk: any) => (
              <li key={tk.id}>
                <span className="badge">{tk.type}</span> {tk.subject} <span className="muted small">· {tk.status}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

// ---------------- Customer service: hidden-ingredient check ----------------
export function CustomerService() {
  const t = useT();
  const dishes = useData<string[]>("/api/cs/dishes") ?? [];
  const [dish, setDish] = useState<string>("House burger");
  const [note, setNote] = useState("no tomato");
  const [res, setRes] = useState<{ ok: boolean; text: string } | null>(null);
  const support = useData<{ requests: any[]; queue: boolean }>("/api/cs/support");
  const ROUTE: Record<string, string> = { customer_service: "Customer service", on_duty_manager: "On-duty manager", owner: "Owner", approvers: "Approvers" };
  return (
    <section>
      <h1 className="h1">{t("Customer service")}</h1>
      {support && (support.queue || support.requests.length > 0) && (
        <div className="panel">
          <h2 className="h2">
            {t(support.queue ? "Support requests" : "My support requests")} <span className="muted">· {support.requests.length}</span>
          </h2>
          <p className="muted small">
            {t(support.queue
              ? "People who asked the assistant for a person. Nothing is sent on its own: call or text them back."
              : "What you asked the assistant to pass on. Someone will follow up.")}
          </p>
          {support.requests.length === 0 ? (
            <p className="muted">{t("No requests right now.")}</p>
          ) : (
            <ul className="settings-list">
              {support.requests.map((r) => (
                <li key={r.id}>
                  <span className="badge">{t(ROUTE[r.route_to] ?? r.route_to)}</span> <b>{r.requested_for}</b>{" "}
                  <span className="muted small">
                    · {r.channel}
                    {r.best_time ? ` · ${r.best_time}` : ""} · {r.at}
                  </span>
                  {r.reason && <div className="small">{r.reason}</div>}
                  <div className="muted small">{r.status}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {dishes.length > 0 && (
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
      )}
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
      <TaxFormsAdmin />
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
              <Button
                className="btn btn-sm btn-primary"
                onPress={() => (r.kind === "notice" ? propose("notice_task_done", { task_id: r.id }) : propose("task_done", { assignment_id: r.id }))}
              >
                {t("Mark done")}
              </Button>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

// ---------------- Assistant: the operations agent, on the local model ----------------
type ChatMsg = { role: "user" | "assistant"; text: string };

export function Assistant({ embedded = false, me }: { embedded?: boolean; me?: Me }) {
  const t = useT();
  const asked = useRef(false);
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [model, setModel] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ model: string | null; busy: boolean; messages: ChatMsg[] }>("/api/assistant")
      .then((h) => {
        setMsgs(h.messages);
        setModel(h.model);
        setBusy(h.busy);
      })
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    // On Home, don't jump the page down to the chat when it loads; only follow along once a question is asked.
    if (!embedded || asked.current) end.current?.scrollIntoView({ block: "nearest" });
  }, [msgs, busy]);

  const ask = async (q: string) => {
    q = q.trim();
    if (!q || busy) return;
    asked.current = true;
    setError("");
    setBusy(true);
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setText("");
    try {
      const r = await api<ChatMsg>("/api/assistant", { text: q });
      setMsgs((m) => [...m, { role: "assistant", text: r.text }]);
    } catch (e: any) {
      setMsgs((m) => m.slice(0, -1)); // the question didn't get an answer; put it back in the box
      setText(q);
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const clear = async () => {
    try {
      await api("/api/assistant/clear", {});
      setMsgs([]);
      setError("");
    } catch (e: any) {
      setError(e.message);
    }
  };

  return (
    <section className={embedded ? "panel assistant-home" : undefined}>
      <div className="row-between">
        {embedded ? <h2 className="h2">✨ {t("Ask the assistant")}</h2> : <h1 className="h1">{t("Ask the assistant")}</h1>}
        {msgs.length > 0 && (
          <Button className="btn btn-sm btn-ghost" onPress={clear} isDisabled={busy}>
            {t("New conversation")}
          </Button>
        )}
      </div>
      <p className="muted small">
        {t("Runs on this computer. Nothing leaves it.")} {model && <>({model}) </>}
        {t("It sees only what your level may see, and it can't approve, pay or send anything.")}
      </p>
      {msgs.length === 0 && !busy && (
        <div className="chat-try">
          <span className="muted small">{t("Try:")}</span>
          {(me && me.level < 2
            ? ["What are my tasks today?", "I need to talk to someone about my pay"]
            : ["What's open tonight?", "Are we short anyone tonight?", "Which tickets are oldest?"]
          ).map((q) => (
            <Button key={q} className="btn btn-sm" onPress={() => ask(t(q))}>
              {t(q)}
            </Button>
          ))}
        </div>
      )}
      <ChatBox
        lines={msgs.map((m, i) => ({ key: i, who: t("Assistant"), text: m.text, mine: m.role === "user" }))}
        text={text}
        setText={setText}
        onSend={ask}
        busy={busy}
        busyText={t("Thinking… the first answer can take up to a minute.")}
        placeholder={t("Your question")}
        error={error}
        empty={<></>}
      />
    </section>
  );
}

// ---------------- Business profile: the Human logo (account holder only) ----------------
type Settings = {
  business: { owner: string; data: string; people: number };
  suggestions: { id: string; text: string }[];
  sensitivity: { levels: { level: number; name: string; sees: string; people: number }[]; files: [string, number][]; assistant: string; money: string };
  confirmations: { at: string; actor: string; kind: string; title: string; outcome: string; detail: string | null }[];
  checkins: { at: string | null; subject: string; by: string; works: boolean; intended: boolean; note: string | null }[];
  authenticator: { status: string; note: string; events: { at: string; member: string; event: string; origin: string }[] };
};

const OUTCOME_BADGE: Record<string, string> = { confirmed: "CONFIRMED", refused: "REFUSED", cancelled: "CANCELLED" };

export function BusinessProfile() {
  const t = useT();
  const s = useData<Settings>("/api/settings");
  if (!s) return <p className="muted">{t("The business profile needs the account holder.")}</p>;
  return (
    <section>
      <h1 className="h1">{t("Business profile")}</h1>
      <div className="panel">
        <p className="kpi">
          <b>{s.business.owner}</b> <span className="muted">· {t("Account holder")}</span>
        </p>
        <p className="muted small">
          {s.business.data} · {s.business.people} {t(s.business.people === 1 ? "person on the roster" : "people on the roster")}
        </p>
      </div>

      {s.suggestions.length > 0 && (
        <div className="panel">
          <h2 className="h2">💡 {t("Get more out of Human")}</h2>
          <ul className="settings-list tips">
            {s.suggestions.map((x) => (
              <li key={x.id}>{x.text}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="panel">
        <h2 className="h2">{t("Sensitivity")}: {t("Who sees what")}</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("Level")}</th>
                <th>{t("Sees")}</th>
                <th>{t("People")}</th>
              </tr>
            </thead>
            <tbody>
              {s.sensitivity.levels.map((l) => (
                <tr key={l.level}>
                  <td>
                    <span className="badge">
                      L{l.level} {t(l.name)}
                    </span>
                  </td>
                  <td>{l.sees}</td>
                  <td>{l.people}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {s.sensitivity.files.length > 0 && (
          <>
            <h3 className="h3">{t("Business files the assistant may open")}</h3>
            <ul className="settings-list">
              {s.sensitivity.files.map(([pattern, lvl]) => (
                <li key={pattern}>
                  <code>{pattern}</code> <span className="muted">→ L{lvl} {t("and up")}</span>
                </li>
              ))}
            </ul>
          </>
        )}
        <p className="small">{s.sensitivity.assistant}</p>
        <p className="small">{s.sensitivity.money}</p>
        <p className="muted small">{t("Only the account holder changes levels, from the Team screen.")}</p>
      </div>

      <div className="panel">
        <h2 className="h2">
          {t("Confirmations")} <span className="muted">· {s.confirmations.length}</span>
        </h2>
        <p className="muted small">{t("Every confirm card: what was confirmed, refused on the re-check, or cancelled. Kept forever, never edited.")}</p>
        {s.confirmations.length === 0 ? (
          <p className="muted">{t("No confirmations yet.")}</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("When")}</th>
                  <th>{t("Who")}</th>
                  <th>{t("Change")}</th>
                  <th>{t("Outcome")}</th>
                </tr>
              </thead>
              <tbody>
                {s.confirmations.map((c, i) => (
                  <tr key={i}>
                    <td className="small">{c.at}</td>
                    <td>{c.actor}</td>
                    <td>
                      {c.title}
                      {c.detail && <div className="muted small">{c.detail}</div>}
                    </td>
                    <td>
                      <span className={`badge badge-${c.outcome}`}>{OUTCOME_BADGE[c.outcome] ?? c.outcome}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {s.checkins.length > 0 && (
          <>
            <h3 className="h3">{t("Check-ins")}</h3>
            <ul className="settings-list">
              {s.checkins.map((c, i) => (
                <li key={i}>
                  <span className={`badge ${c.works && c.intended ? "badge-confirmed" : "badge-refused"}`}>{c.works && c.intended ? "OK" : "NEEDS REVIEW"}</span>{" "}
                  {c.subject} <span className="muted small">· {c.by}{c.note ? ` · ${c.note}` : ""}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <div className="panel">
        <h2 className="h2">{t("Authenticator")}</h2>
        <p>
          <span className="badge">{s.authenticator.status === "not_set_up" ? "NOT SET UP" : s.authenticator.status.toUpperCase()}</span>{" "}
          <span className="muted small">{s.authenticator.note}</span>
        </p>
        {s.authenticator.events.length === 0 ? (
          <p className="muted">{t("No sign-in history yet. Each sign-in will show here with where it came from (device and authenticator).")}</p>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("When")}</th>
                  <th>{t("Who")}</th>
                  <th>{t("Event")}</th>
                  <th>{t("Origin")}</th>
                </tr>
              </thead>
              <tbody>
                {s.authenticator.events.map((e, i) => (
                  <tr key={i}>
                    <td className="small">{e.at}</td>
                    <td>{e.member}</td>
                    <td>{e.event}</td>
                    <td>{e.origin}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
