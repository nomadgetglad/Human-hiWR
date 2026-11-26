// Employee ID intake (on the Team screen). A manager types what's on the ID, optionally takes a photo,
// and confirms on the card. Details are sealed on the server; only Finance and the account holder open them.
import { useEffect, useState } from "react";
import { Button, Checkbox, Input, Label, TextField } from "react-aria-components";
import { api } from "./api";
import { useProposals } from "./ConfirmCard";
import { useT } from "./i18n";

const MAX_SIDE = 1600; // phone photos are shrunk before upload; plenty to read an ID

async function shrink(file: File): Promise<string> {
  const img = await createImageBitmap(file);
  const k = Math.min(1, MAX_SIDE / Math.max(img.width, img.height));
  const c = document.createElement("canvas");
  c.width = Math.round(img.width * k);
  c.height = Math.round(img.height * k);
  c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
  return c.toDataURL("image/jpeg", 0.85);
}

export function IdIntake({ member, name, docTypes, onClose }: { member: string; name: string; docTypes: Record<string, string>; onClose: () => void }) {
  const t = useT();
  const { propose } = useProposals();
  const [f, setF] = useState<Record<string, any>>({ doc_type: "drivers_license", work_auth_verified: false });
  const [photo, setPhoto] = useState<string | null>(null);
  const set = (k: string) => (v: any) => setF((x) => ({ ...x, [k]: v }));
  const field = (k: string, label: string, props: Record<string, any> = {}) => (
    <TextField className="field" value={f[k] ?? ""} onChange={set(k)} isRequired={props.required}>
      <Label>{label}</Label>
      <Input autoComplete="off" {...props.input} />
    </TextField>
  );
  return (
    <div className="panel id-intake">
      <div className="row-between">
        <h2 className="h2">
          {t("ID on file for")} {name}
        </h2>
        <Button className="btn btn-ghost btn-sm" onPress={onClose}>
          {t("Close")}
        </Button>
      </div>
      <p className="muted small">{t("Look at the ID in person. Type it as printed. The details are encrypted; managers only see that it's on file and when it expires.")}</p>
      <div className="form-row">
        <div className="field">
          <label htmlFor="doc_type">{t("Kind of ID")}</label>
          <select id="doc_type" className="select-btn" value={f.doc_type} onChange={(e) => set("doc_type")(e.target.value)}>
            {Object.entries(docTypes).map(([k, v]) => (
              <option key={k} value={k}>
                {t(v)}
              </option>
            ))}
          </select>
        </div>
        {field("legal_name", t("Legal name (as on the ID)"), { required: true })}
        {field("birth_date", t("Date of birth"), { required: true, input: { type: "date" } })}
        {field("doc_number", t("Document number"), { required: true, input: { autoCapitalize: "characters" } })}
        {field("issuer", t("Issued by (state or country)"), { required: true, input: { placeholder: "NC" } })}
        {field("expires_on", t("Expires"), { input: { type: "date" } })}
        {field("address", t("Home address"))}
        {field("emergency_name", t("Emergency contact"))}
        {field("emergency_phone", t("Emergency phone"), { input: { inputMode: "tel" } })}
      </div>
      <div className="form-row">
        <div className="field">
          <label htmlFor="id_photo">{t("Photo of the ID (optional)")}</label>
          <input
            id="id_photo"
            type="file"
            accept="image/jpeg,image/png"
            capture="environment"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              setPhoto(file ? await shrink(file) : null);
            }}
          />
          {photo && <span className="muted small">{t("Photo ready")} ({Math.round((photo.length * 3) / 4 / 1024)} KB)</span>}
        </div>
        <Checkbox className="check" isSelected={f.work_auth_verified} onChange={set("work_auth_verified")}>
          <span className="check-box" aria-hidden />
          {t("I checked work authorization in person (I-9)")}
        </Checkbox>
      </div>
      <Button
        className="btn btn-primary"
        isDisabled={!f.legal_name || !f.birth_date || !f.doc_number || !f.issuer}
        onPress={() => propose("id_intake", { member_id: member, fields: f, ...(photo ? { photo } : {}) })}
      >
        {t("Check and save")}
      </Button>
    </div>
  );
}

export function IdView({ member, name, onClose }: { member: string; name: string; onClose: () => void }) {
  const t = useT();
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState("");
  const [img, setImg] = useState<string | null>(null);
  useEffect(() => {
    api(`/api/team/${member}/id`).then(setD).catch((e) => setErr(e.message));
    return () => {
      if (img) URL.revokeObjectURL(img);
    };
  }, [member]);
  const showPhoto = async () => {
    const r = await fetch(`/api/team/${member}/id/photo`, { credentials: "same-origin", headers: { "X-Human": "1" } });
    if (r.ok) setImg(URL.createObjectURL(await r.blob()));
  };
  return (
    <div className="panel id-intake">
      <div className="row-between">
        <h2 className="h2">
          {t("ID for")} {name}
        </h2>
        <Button className="btn btn-ghost btn-sm" onPress={onClose}>
          {t("Close")}
        </Button>
      </div>
      {err && <p className="error">{err}</p>}
      {d && (
        <>
          <dl className="id-dl">
            <dt>{t("Legal name")}</dt><dd>{d.legal_name}</dd>
            <dt>{t("Date of birth")}</dt><dd>{d.birth_date}</dd>
            <dt>{t("Kind of ID")}</dt><dd>{d.doc_label}</dd>
            <dt>{t("Document number")}</dt><dd>{d.doc_number}</dd>
            <dt>{t("Issued by")}</dt><dd>{d.issuer}</dd>
            <dt>{t("Expires")}</dt><dd>{d.expires_on ?? "—"}</dd>
            <dt>{t("Work authorization")}</dt><dd>{d.work_auth_verified ? t("Checked in person") : t("Not checked")}</dd>
            <dt>{t("Home address")}</dt><dd>{d.address || "—"}</dd>
            <dt>{t("Emergency contact")}</dt><dd>{[d.emergency_name, d.emergency_phone].filter(Boolean).join(" · ") || "—"}</dd>
          </dl>
          {d.has_photo && !img && (
            <Button className="btn btn-sm" onPress={showPhoto}>
              {t("Show photo")}
            </Button>
          )}
          {img && <img className="id-photo" src={img} alt={t("Photo of the ID")} />}
          <h3 className="h3">{t("Who opened this")}</h3>
          <ul className="muted small">
            {d.access_log.map((a: any, i: number) => (
              <li key={i}>
                {a.at.replace("T", " ").slice(0, 16)} · {a.viewer} · {a.what}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
