# Putting humanhiwr.com live

The site is the single page in `site/`. GitHub Pages hosts it for free from this repository, and the workflow in `.github/workflows/pages.yml` publishes it every time `site/` changes on `main`.

## 1. Buy the domain

Buy `humanhiwr.com` at any registrar (Cloudflare Registrar, Namecheap, Porkbun). Turn on auto-renew and WHOIS privacy.

## 2. Verify the domain with GitHub (do this before pointing DNS)

Verifying stops anyone else from claiming the domain on GitHub Pages.

1. GitHub, top-right picture, **Settings**, then **Pages** (under Code, planning, and automation).
2. **Add a domain**, type `humanhiwr.com`, press **Add domain**.
3. GitHub shows a TXT record. Add it at the registrar's DNS page:
   - Type: `TXT`
   - Name/Host: `_github-pages-challenge-nomadgetglad` (some registrars want the full `_github-pages-challenge-nomadgetglad.humanhiwr.com`)
   - Value: the code GitHub shows
4. Wait a few minutes, then press **Verify**. Leave the TXT record in place.

## 3. Point the domain at GitHub Pages

At the registrar's DNS page, add:

| Type | Name | Value |
|---|---|---|
| A | `@` | `185.199.108.153` |
| A | `@` | `185.199.109.153` |
| A | `@` | `185.199.110.153` |
| A | `@` | `185.199.111.153` |
| AAAA | `@` | `2606:50c0:8000::153` |
| AAAA | `@` | `2606:50c0:8001::153` |
| AAAA | `@` | `2606:50c0:8002::153` |
| AAAA | `@` | `2606:50c0:8003::153` |
| CNAME | `www` | `nomadgetglad.github.io` |

Delete any "parking" A or CNAME records the registrar added. On Cloudflare, set these records to **DNS only** (grey cloud) until the certificate below is issued.

## 4. Turn on Pages for this repository

1. Repository **Settings**, then **Pages**.
2. Source: **GitHub Actions**.
3. Custom domain: `humanhiwr.com`, then **Save**. (The `site/CNAME` file says the same.)
4. When the DNS check passes, tick **Enforce HTTPS**. The certificate can take up to an hour.

## 5. Check

- https://humanhiwr.com and https://www.humanhiwr.com both open the page.
- The padlock shows in the browser.

## Still to do before announcing it

- Connect the walkthrough form to an inbox (it says "not connected" right now).
- Add the contact email and phone.
- Real prices, or keep "Quoted".
- Dad's OK, and a link from humansoftware.com.
