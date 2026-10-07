# Årshjul for innkjøp – notes for Claude

A Flask app for engineers at an NTNU department who buy components for teaching projects (hackathons, semester kits). It shows a year wheel of when each project runs, computes order deadlines backwards from the start week, tracks orders/receipts/counts per supplier, and reads/writes the engineers' Excel BOMs. Lecturers log in to manage their own projects; an admin panel sees everything.

`VEIKART.md` (roadmap, decisions, VPS checklist) and `LES MEG.md` (user guide) are the source of truth for *why* things are the way they are. Read them before larger changes.

## Layout

```
arshjul/            the app
  app.py            Flask app, main API (/api/state, /api/gjennomforinger/…, /api/emner, /api/brukere)
  adminpanel.py     /admin and /api/admin/* (blueprint "adminpanel"), registered at the end of app.py
  auth.py           login, roles, CSRF, first-admin setup page
  config.py         settings from env vars / arshjul/innstillinger.env
  db.py             SQLite: schema migrations (MIGRERINGER) + all queries
  frister.py        weeks, school years (week 32 → 31), deadlines – the ONLY place dates are computed
  bestilling.py     order status per supplier, frozen copies, "need more"
  telling.py        counting after a project, loss
  bom.py            reading BOM .xlsx, diff, templates, write_stock (column H only)
  manage.py         CLI for users (backup way in, and how the first admin is made on a server)
  static/, templates/   arshjul.js (main page), admin.js (panel)
  tests/            pytest; tests/hjelp.py has BOM helpers
```

Data lives **outside the code** in the data folder (default: repo root): `arshjul.db`, `BOM/`, `Bestillingslister/`, `.cache/` (incl. the local secret key). The BOM folder can be elsewhere (`ARSHJUL_BOM`, e.g. an NTNU network share). None of this is in git.

## Run and test

```
cd arshjul
.venv\Scripts\python -m pip install -r requirements-dev.txt   # once
.venv\Scripts\python -m pytest -q                            # all tests, ~15 s
.venv\Scripts\python app.py --debug                          # dev server with reload
```

`Start årshjul.bat` creates `.venv` and runs the app (waitress) for the user. On a fresh database the browser shows "Velkommen til årshjulet" to create the first admin.

## Rules that matter

- **Never touch the user's real data in tests or experiments.** Tests use a temp data folder (`tests/conftest.py` pins every path). For manual checks, copy `arshjul.db`/BOMs to the scratchpad and set `ARSHJUL_DATA`/`ARSHJUL_BOM`. Writes to BOMs only on copies.
- **Migrations are append-only.** Add a new string at the end of `MIGRERINGER` in `db.py`; never edit an existing one (it has run on someone's database). Statements are split on `;`, so no semicolons inside a statement.
- **Authorization is server-side.** Lecturers may only reach endpoints in `FAGLARER_TILLATT`; ownership of a run is checked centrally in `app._eierskap` via `kan_handle()`. Everything `adminpanel.*` requires the admin account. The state endpoint only sends what the user is involved in. When adding an endpoint, decide which list it belongs in.
- **The app never silently writes to a BOM.** Only `bom.write_stock` writes, only column H ("På lager"), with a backup in `BOM/Sikkerhetskopi/`, refusing if the file is open in Excel, and with a conflict check. Receipts *add*; counts/undo *set* with the expected old value.
- **Line identity is `supplier|part number`** (`bom.rad_key`); part numbers are only unique per supplier.
- **Page JS is tested in V8** (`tests/test_side.py`, `test_adminpanel.py`) with a stub DOM. mini-racer crashes under paths with æøå, so the test copies it to an ASCII temp folder. No browser APIs the stub lacks (e.g. `URLSearchParams`, `setTimeout` in tests).
- After any change to `app.py`/`db.py` the user must **restart** the app; JS/CSS only needs a reload.

## Language

- Text users see (UI, error messages, docs) is **Norwegian bokmål**.
- Code identifiers, comments and commit messages are currently Norwegian too. **The owner does not like coding in Norwegian** and plans to move the code to English (tracked in `VEIKART.md`). Until that cleanup is done, match the surrounding code; don't mass-rename unless asked.

## Environment quirks (Windows)

- Shell is PowerShell 5.1: no `&&`; here-strings with `"` inside `python -c` get mangled – write scripts to a file instead. `[IO.File]` uses .NET's working directory, not PowerShell's – use absolute paths.
- Commit messages: write to a file and `git commit -F file` (stdin piping doesn't work). End with the Co-Authored-By trailer.
- The repo path contains `ø`; quote paths.
