// Set up role (managers and up): pick a role or start a new one; set its suggested level (no higher than your
// own), who it reports to, its training checklist; see who can certify and who's in the role. Saving makes a new
// version through a confirm card; earlier setups stay on the record.
import { useEffect, useState } from "react";
import { Button, Input, Label, ListBox, ListBoxItem, Popover, Select, SelectValue, TextArea, TextField } from "react-aria-components";
import { useProposals } from "./ConfirmCard";
import { useT } from "./i18n";
import { useData } from "./useData";

type Role = {
  role: string;
  level: number;
  reports_to: string | null;
  steps: string[];
  by: string | null;
  at: string | null;
  staff: { id: string; name: string; level: number; training: { done: number; total: number; trainee: boolean } }[];
  certifiers: string[];
};

function Choose({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
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

export function Roles() {
  const t = useT();
  const { propose, version } = useProposals();
  const v = useData<{ roles: Role[]; max_level: number; level_names: string[] }>("/api/roles", [version]);
  const [pick, setPick] = useState<string>("");
  const [name, setName] = useState("");
  const [level, setLevel] = useState("0");
  const [reports, setReports] = useState("none");
  const [steps, setSteps] = useState("");
  const current = v?.roles.find((r) => r.role === pick) ?? null;

  useEffect(() => {
    if (!v) return;
    if (current) {
      setName(current.role);
      setLevel(String(current.level));
      setReports(current.reports_to ?? "none");
      setSteps(current.steps.join("\n"));
    } else if (pick === "__new") {
      setName("");
      setLevel("0");
      setReports("none");
      setSteps("");
    }
  }, [pick, v]);
  if (!v) return <p className="muted">{t("Setting up roles needs Manager level.")}</p>;

  const levels: [string, string][] = Array.from({ length: v.max_level + 1 }, (_, i) => [String(i), `${i} ${t(v.level_names[i])}`]);
  const others: [string, string][] = [["none", t("No one (top of the tree)")], ...v.roles.filter((r) => r.role !== name).map((r) => [r.role, r.role] as [string, string])];
  // hierarchy: roles that report to each role
  const under = (role: string) => v.roles.filter((r) => r.reports_to === role).map((r) => r.role);
  const tops = v.roles.filter((r) => !r.reports_to && (r.by || under(r.role).length));

  return (
    <section>
      <h1 className="h1">🏷 {t("Set up role")}</h1>
      <div className="roles-wrap">
        <div className="panel roles-list">
          <Button className={`tc-who ${pick === "__new" ? "tc-on" : ""}`} onPress={() => setPick("__new")}>
            ➕ {t("New role")}
          </Button>
          {v.roles.map((r) => (
            <Button key={r.role} className={`tc-who ${pick === r.role ? "tc-on" : ""}`} onPress={() => setPick(r.role)}>
              {r.role}
              <span className="muted small">
                {r.staff.length} {t(r.staff.length === 1 ? "person" : "people")}
                {r.steps.length ? ` · ${r.steps.length} ${t("training steps")}` : ""}
              </span>
            </Button>
          ))}
        </div>

        <div className="roles-edit">
          {!pick ? (
            <div className="panel">
              <p className="muted">{t("Pick a role on the left to set it up, or start a new one.")}</p>
              {tops.length > 0 && (
                <>
                  <h3 className="h3">{t("Who reports to whom")}</h3>
                  <ul className="tree">
                    {tops.map((r) => (
                      <li key={r.role}>
                        <b>{r.role}</b>
                        {under(r.role).length > 0 && (
                          <ul>
                            {under(r.role).map((u) => (
                              <li key={u}>{u}</li>
                            ))}
                          </ul>
                        )}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          ) : (
            <>
              <div className="panel">
                <h2 className="h2">{pick === "__new" ? t("New role") : name}</h2>
                <div className="form-row">
                  <TextField className="field" value={name} onChange={setName} isDisabled={pick !== "__new"}>
                    <Label>{t("Role name")}</Label>
                    <Input placeholder="Barista" />
                  </TextField>
                  <Choose label={t("Suggested level")} value={level} onChange={setLevel} options={levels} />
                  <Choose label={t("Reports to")} value={reports} onChange={setReports} options={others} />
                </div>
                <p className="muted small">
                  {t("You can set a role's level up to your own. A person's actual level is still set by the account holder, and training never changes it.")}
                </p>
                <TextField className="field" value={steps} onChange={setSteps}>
                  <Label>{t("Training checklist, one step per line")}</Label>
                  <TextArea rows={5} placeholder={"Opening checklist\nFood safety and temps\nClosing and cleaning"} />
                </TextField>
                <div className="row-gap">
                  <Button
                    className="btn btn-primary"
                    isDisabled={!name.trim()}
                    onPress={() =>
                      propose("save_role", {
                        role: name,
                        level: Number(level),
                        reports_to: reports === "none" ? null : reports,
                        steps: steps.split(/\r?\n/).filter((s) => s.trim()),
                      })
                    }
                  >
                    {t("Check and save")}
                  </Button>
                </div>
                {current?.at && (
                  <p className="muted small">
                    {t("Last saved")} {current.at.slice(0, 16)} {current.by ? `· ${current.by}` : ""}
                  </p>
                )}
              </div>
              {current && (
                <div className="grid-2">
                  <div className="panel">
                    <h3 className="h3">✅ {t("Who can certify")}</h3>
                    <ul className="settings-list">
                      {current.certifiers.map((c) => (
                        <li key={c}>{c}</li>
                      ))}
                    </ul>
                    <p className="muted small">{t("Managers and up, or a lead who has finished this role's training. Never yourself.")}</p>
                  </div>
                  <div className="panel">
                    <h3 className="h3">👥 {t("Staff in this role")}</h3>
                    {current.staff.length === 0 ? (
                      <p className="muted">{t("No one yet. Add people on the Team screen.")}</p>
                    ) : (
                      <ul className="settings-list">
                        {current.staff.map((s) => (
                          <li key={s.id}>
                            <b>{s.name}</b> <span className="muted small">L{s.level}</span>{" "}
                            {s.training.total > 0 && (
                              <span className={`badge ${s.training.trainee ? "badge-refused" : "badge-confirmed"}`}>
                                {s.training.trainee ? `${t("Trainee")} ${s.training.done}/${s.training.total}` : t("Trained")}
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
