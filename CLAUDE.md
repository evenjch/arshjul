# Årshjul for innkjøp – notes for Claude

A Flask app for engineers at an NTNU department who buy components for teaching projects (hackathons, semester kits). It shows a year wheel of when each project runs, computes order deadlines backwards from the start week, tracks orders/receipts/counts per supplier, and reads/writes the engineers' Excel BOMs. Lecturers log in to manage their own projects; an admin panel sees everything.

`VEIKART.md` (roadmap, decisions, VPS checklist) and `LES MEG.md` (user guide) are the source of truth for *why* things are the way they are. Read them before larger changes.

## Layout

```
arshjul/            the app
  app.py            Flask app, main API (/api/state, /api/runs/…, /api/courses, /api/users)
  admin_panel.py    /admin and /api/admin/* (blueprint "admin_panel"), registered at the end of app.py
  auth.py           login, roles, CSRF, first-admin setup page
  config.py         settings from env vars / arshjul/innstillinger.env
  db.py             SQLite: schema migrations (MIGRATIONS) + all queries
  deadlines.py      weeks, school years (week 32 → 31), Easter, deadlines – the ONLY place dates are computed
  orders.py         order status per supplier, frozen copies, "need more"
  counting.py       counting after a project, loss
  bom.py            reading BOM .xlsx, diff, templates, write_stock (column H), write_sets (B10)
  calendar_feed.py  the iCalendar feed (/calendar/<token>.ics) – open without login, the per-user token is the key
  manage.py         CLI for users (backup way in, and how the first admin is made on a server)
  static/, templates/   arshjul.js (main page), admin.js (panel)
  tests/            pytest; tests/helpers.py has BOM helpers
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
- **Migrations are append-only.** Add a new string at the end of `MIGRATIONS` in `db.py`; never edit an existing one (it has run on someone's database). Statements are split on `;`, so no semicolons inside a statement. Migrations 1–15 use the old Norwegian schema on purpose; 16 renamed everything to English. Migrations run with foreign keys off (checked afterwards), and a backup is taken to `Sikkerhetskopi-db/` before any upgrade.
- **Authorization is server-side.** Lecturers may only reach endpoints in `LECTURER_ALLOWED`; ownership of a run is checked centrally in `app._ownership` via `can_handle()`. Everything `admin_panel.*` requires the admin account. The only endpoints open without login are the login/setup pages, static files and `calendar_feed` (`auth._OPEN`). The state endpoint only sends what the user is involved in. When adding an endpoint, decide which list it belongs in.
- **The app never silently writes to a BOM.** Only two functions write, both with a backup in `BOM/Sikkerhetskopi/`, refusing if the file is open in Excel, and with a conflict check:
  - `bom.write_stock`: column H ("På lager"), on the user's confirmation. Receipts *add*; counts/undo *set* with the expected old value.
  - `bom.write_sets`: B10 ("Antall sett"), which the template's "Å bestille" formulas use. The app owns the set count, so `app.sets_to_bom` writes it automatically when the count changes – only for the run B10 follows (`app.active_bom_runs`: the next one per BOM that hasn't started and isn't counted; several school years share a BOM), and only if B10 still has the app's previous value (a hand edit in Excel is never overwritten). Otherwise the engineer sees the mismatch with a button (`/bom/sets`). Every write is logged ("antall sett i BOM-en").
- **Line identity is `supplier|part number`** (`bom.row_key`); part numbers are only unique per supplier.
- **Page JS is tested in V8** (`tests/test_page.py`, `test_admin_panel.py`) with a stub DOM; the harness (inside those test files) calls page functions by name and reads element ids, so keep it in step when renaming. mini-racer crashes under paths with æøå, so the test copies it to an ASCII temp folder. No browser APIs the stub lacks (e.g. `URLSearchParams`, `setTimeout` in tests).
- After any change to `app.py`/`db.py` the user must **restart** the app; JS/CSS only needs a reload.

## Language

- **Code is English**: identifiers, comments, docstrings, API fields and URLs, database tables/columns/codes, test names and commit messages.
- **What users see stays Norwegian bokmål**: UI text, error messages, `LES MEG.md`/`VEIKART.md`, change-log labels ("antall sett"), the Excel templates and their headers (bom.py checks "Beskrivelse" in C13 – that's data), folders in the data folder (`BOM/`, `Bestillingslister/`, `BOM/Sikkerhetskopi/`, `Sikkerhetskopi-db/`), `innstillinger.env`, the `manage.py` command line (`ny-bruker … ingenior`, `passord`, `--fjern`) and page URLs (`/`, `/admin`, `/login`, `/oppsett`).
- Watch out for one-word Norwegian strings that are UI text (e.g. `n === 1 ? "linje" : "linjer"`) – they look like keys but must stay Norwegian.

Glossary (Norwegian domain word → name in code):

| Norwegian | Code | Norwegian | Code |
|---|---|---|---|
| gjennomføring | run | prosjekt | project |
| emne | course | skoleår | school_year |
| frist | deadline | forberedelse | prep |
| bestilling | order | tilleggsbestilling | extra (order) |
| varemottak | receipt | telling | count |
| leverandør | supplier | varenummer | part_number |
| per sett | perSet / per_set | på lager | in_stock (BOM) / stocked (receipts) |
| antall sett | sets | studenter | students |
| gruppestørrelse | group_size | ekstra sett | spare |
| leveringstid | lead | faglærer / ingeniør | lecturer / engineer |
| stengt periode | closed_period | påske | easter |
| svinn | loss | utstyr / forbruk | equipment / consumable |
| varsel / endringslogg | notification / change_log | kvittere | acknowledge |
| BOM-mal | template | sikkerhetskopi | backup |
| semestersett / hackathon / lab | semester_kit / hackathon / lab | uke | week |

## Environment quirks (Windows)

- Shell is PowerShell 5.1: no `&&`; here-strings with `"` inside `python -c` get mangled – write scripts to a file instead. `[IO.File]` uses .NET's working directory, not PowerShell's – use absolute paths.
- Commit messages: write to a file and `git commit -F file` (stdin piping doesn't work). End with the Co-Authored-By trailer.
- The repo path contains `ø`; quote paths.
