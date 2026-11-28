// Sign-in on your own account: pick your name, type the 6-digit code from your authenticator app.
// First time on a phone: type the setup code your manager gave you, scan the QR code, type the first 6-digit code.
import { useEffect, useState } from "react";
import { Button, Input, Label, TextField } from "react-aria-components";
import { api, type Member } from "./api";
import { useT } from "./i18n";

type Setup = { secret: string; otpauth: string; qr_svg: string | null };

export function SignIn({ onDone }: { onDone: () => void }) {
  const t = useT();
  const [members, setMembers] = useState<Member[]>([]);
  const [who, setWho] = useState("");
  const [mode, setMode] = useState<"code" | "setup">("code");
  const [setupCode, setSetupCode] = useState("");
  const [setup, setSetup] = useState<Setup | null>(null);
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Member[]>("/api/members").then(setMembers).catch(() => setMembers([]));
  }, []);

  const run = async (f: () => Promise<void>) => {
    setErr("");
    setBusy(true);
    try {
      await f();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  const signIn = () => run(async () => {
    await api("/api/auth/signin", { member_id: who, code });
    onDone();
  });
  const start = () => run(async () => setSetup(await api<Setup>("/api/auth/setup/start", { member_id: who, setup_code: setupCode })));
  const finish = () => run(async () => {
    await api("/api/auth/setup/finish", { member_id: who, setup_code: setupCode, code });
    onDone();
  });

  const six = code.replace(/\D/g, "").length === 6;
  return (
    <main className="main signin" id="main">
      <div className="panel signin-panel">
        <h1 className="h1">Human</h1>
        {!who ? (
          <>
            <h2 className="h2">{t("Who are you?")}</h2>
            <div className="signin-names">
              {members.map((m) => (
                <Button key={m.id} className="btn btn-wide" onPress={() => { setWho(m.id); setMode((m as any).set_up === false ? "setup" : "code"); }}>
                  {m.name} <span className="muted small">{m.title}</span>
                </Button>
              ))}
            </div>
          </>
        ) : (
          <>
            <p className="row-between">
              <b>{members.find((m) => m.id === who)?.name ?? who}</b>
              <Button className="btn btn-ghost btn-sm" onPress={() => { setWho(""); setSetup(null); setCode(""); setErr(""); }}>
                {t("Not you?")}
              </Button>
            </p>
            {mode === "code" && (
              <>
                <TextField className="field" value={code} onChange={setCode} autoFocus>
                  <Label>{t("6-digit code from your authenticator app")}</Label>
                  <Input inputMode="numeric" autoComplete="one-time-code" maxLength={7} className="code-input" />
                </TextField>
                <Button className="btn btn-primary btn-wide" isDisabled={!six || busy} onPress={signIn}>
                  {t("Sign in")}
                </Button>
                <Button className="btn btn-wide" onPress={() => { setMode("setup"); setCode(""); setErr(""); }}>
                  {t("First time? Set up your authenticator (QR code)")}
                </Button>
              </>
            )}
            {mode === "setup" && !setup && (
              <>
                <TextField className="field" value={setupCode} onChange={setSetupCode} autoFocus>
                  <Label>{t("Setup code from your manager")}</Label>
                  <Input autoCapitalize="characters" autoComplete="off" className="code-input" placeholder="ABCD-EFGH" />
                </TextField>
                <p className="muted small">
                  {t("The account holder's first code is printed in the app server window. Press Next and the QR code for your authenticator app appears.")}
                </p>
                <Button className="btn btn-primary btn-wide" isDisabled={setupCode.replace(/[^a-z0-9]/gi, "").length !== 8 || busy} onPress={start}>
                  {t("Next")}
                </Button>
                <Button className="btn btn-ghost btn-wide" onPress={() => { setMode("code"); setErr(""); }}>
                  {t("I already have the app set up")}
                </Button>
              </>
            )}
            {mode === "setup" && setup && (
              <>
                <ol className="steps">
                  <li>{t("Open Google Authenticator, Microsoft Authenticator or any authenticator app")}</li>
                  <li>{t("Tap + and scan this code")}</li>
                </ol>
                {setup.qr_svg && <div className="qr" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} aria-label={t("QR code to scan")} />}
                <p className="muted small">
                  {t("Can't scan? Enter this key by hand:")} <code className="secret">{setup.secret.replace(/(.{4})/g, "$1 ").trim()}</code>
                </p>
                <p className="muted small">
                  <a href={setup.otpauth}>{t("On this phone: open in the authenticator app")}</a>
                </p>
                <TextField className="field" value={code} onChange={setCode}>
                  <Label>{t("3. Type the 6-digit code the app shows")}</Label>
                  <Input inputMode="numeric" autoComplete="one-time-code" maxLength={7} className="code-input" />
                </TextField>
                <Button className="btn btn-primary btn-wide" isDisabled={!six || busy} onPress={finish}>
                  {t("Link and sign in")}
                </Button>
              </>
            )}
          </>
        )}
        {err && (
          <p className="error" role="alert">
            {err}
          </p>
        )}
      </div>
    </main>
  );
}
