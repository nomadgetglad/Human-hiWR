# Human UI (first pass)

The screens for **Human**, the AI work app on HiWR data. A local web app (PWA) served from the Windows
desktop: tablets and phones on the shop Wi-Fi open it in a browser. It runs on the sample data in `../hiwr`.

- **Server:** `server/` is FastAPI over the `hiwr/` modules. It filters by access level, so a browser never
  receives data above the signed-in member's level.
- **Web:** `web/` is React + React Aria (accessible buttons, dialogs, menus), built with Vite.

## Run it

```
cd ui
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
python -m uvicorn server.app:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 on the desktop, or http://<desktop-ip>:8000 on a tablet. For live editing run
`npm run dev` in `web/` (port 5173) with the server running.

It starts as **your own account with no test data**: just the account holder, and adding staff is the first step.
What you add is saved in `ui/human.db` (not in git), so it is still there after a restart.

To use the sample restaurant instead (the tests and screenshots use it), start the server with `HUMAN_DATA=sample`.
In PowerShell: `$env:HUMAN_DATA="sample"` before the uvicorn line; `Remove-Item Env:HUMAN_DATA` to go back.
The account holder can also switch with `POST /api/demo/reset?sample=true` or `?sample=false`; neither deletes anything.

## Signing in (your own account)

Your own account needs a real sign-in: first name + a 6-digit code from an authenticator app
(Google Authenticator, Microsoft Authenticator, 1Password, any TOTP app). No passwords.

1. **First start:** the server window prints the account holder's setup code (`Setup code: ABCD-EFGH`).
   On your phone, open the app, tap your name, then "First time on this phone? I have a setup code".
   Type the code, scan the QR code with the authenticator app, type the 6-digit code it shows. Done.
2. **Everyone else:** in Team, press **Setup code** on their row and confirm. The code shows once; hand it to
   them in person. They do the same three steps on their phone. Managers give codes to people below their level;
   only the account holder gives codes to managers.
3. **Lost phone:** press **Reset** on their row (a new setup code; the old app and their open sessions stop working).
   The account holder's own reset is done at the server computer: `python -m server.signin_code owner_alex`.

5 wrong codes lock that person for 15 minutes. Sessions last 12 hours. Every setup, sign-in, failure and reset is
logged with the device and time (`GET /api/auth/history`, and `hiwr_auth.settings_block()` for the settings page).

The secret behind each authenticator and all ID details are encrypted with the key in `ui/human.key`, created on
first start next to `human.db`. **Back up human.key** (USB stick in the safe). Without it ID records can't be read
and everyone sets up their authenticator again. Neither file goes in git. Turn on BitLocker on the desktop too.

The sample restaurant keeps the old name switcher so demos and tests can jump between people.

Passkeys (Face ID / fingerprint) can be added later as a second method. Browsers only allow them on https with a
real name, and the desktop on the shop Wi-Fi is plain http for now.

## Employee ID intake

In Team, **Add ID** on a person's row. Type what's on their ID (kind, legal name, birth date, number, issuer,
expiry), plus address and emergency contact, an optional photo of the ID (the phone camera opens), and tick
"I checked work authorization in person". It goes through the confirm card, which shows only the last 4 of the number.

- Managers enter ID for people below their level. Nobody enters their own.
- Managers then see only: on file, kind of ID, expiry, verified. **Finance and the account holder** press **Open** to
  see the details and photo, and every opening is logged ("Who opened this").
- IDs expiring within 30 days show in managers' notifications.
- Corrections add a new version; nothing is overwritten. Social Security numbers are not collected here
  (that stays with the payroll provider).

Handy links (sample restaurant): `?as=fry_rosa&screen=prep&lang=es`, `?as=owner_alex`, `?as=manager_jo&screen=accounting`.

## What is in it

| Part | What it shows |
| --- | --- |
| Top bar | One key number set per level, plus the notifications bell. Account holder: revenue today and tonight's forecast. Managers: kitchen labor % and who is on shift (Finance level also sees labor dollars; level 2 sees % and hours only). Employees: their tasks. Notifications are filtered the same way. |
| Team | First in the menu for managers, and the first card on Home: who works here, and Add staff. Managers add people at level 0 (first name, job title, language). Only the account holder gives a level above 0; only Finance level and up put someone on the schedule with a pay rate, and only they see pay. Every add is a confirm card, logged, never edited or deleted. Sign-in reads this roster. |
| Main menu | Staff, Inventory (Warehouse, Stew, Lab), My station, Customer service, Accounting (Finance level and up only), My tasks (employees). |
| Staff | Chef/manager view: needed vs on shift per role, call-outs, find same-night cover and text the top 3, HR requests. Staff level sees only their own shift and requests. |
| My station | The fryer's prep screen: what to fry now, sold today vs forecast, good on hand, next hour, rest of night, tortillas left. A sample clock moves the night forward. |
| Inventory | Warehouse pull list with Pull buttons, Stew portions left (food cost only for Manager and up), open tickets. Lab is a placeholder until a job exists. |
| Customer service | Order note check: "no tomato" finds tomato hidden in sub-recipes. |
| Accounting | Money actions waiting for double approval. Approve/Reject only shows for certified approvers who did not request it. |
| My tasks | The day's tasks the owner assigned, marked done with a confirm card. |
| Assistant | Chat with the operations assistant (`../agent/`) on the local model only (Ollama on this computer; `agent/config.toml` sets the model). The web app builds no cloud backend, so `/cloud` and a hybrid config can't send anything out. It answers as the signed-in person, with that person's level, and has no tool that approves, pays or sends. One conversation per person, kept in memory until "New conversation" or a reset. The app keeps working while the model thinks: the database lock is held only for each tool call. Ollama has to be running (`ollama serve`). |

## The confirm card

Nothing changes on one tap. Every change (fry, approve, pull stock, text candidates, mark a task done) is
sent to the server as a proposal. The server checks it and answers with a card: what will happen and who it
affects. The member picks **Confirm**, **Fix** (edit and check again) or **Cancel**. The server checks again on
Confirm, because things may have changed. After a confirmed change comes the authority check-in: did it work,
and did it do what you meant. Money cards are marked "PENDING - requires double approval".

## Design rules

- WCAG 2.2 AA contrast, 60px minimum touch targets (gloves), big numbers readable at arm's length.
- Colour is never the only signal: badges carry words (SHORT 1, PENDING, OK).
- English and Spanish for screen labels. Text from the records (names, notes, ticket subjects, server
  messages) stays as written for now.
- Each member's default language comes from their record (Rosa opens in Spanish).

## Not done yet

- Real sign-in. The member picker in the top bar is a sample switcher (it sends `X-Member`).
- Your own account is saved in `ui/human.db`. The sample restaurant is in memory and resets when the server restarts.
- Lab screens, payroll runs, ordering and the accounting summary screens.
- Spanish for server-written text; a local model could translate those.
- The offline shell (service worker) only works over https or on localhost.
