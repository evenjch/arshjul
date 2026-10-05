# Veikart for årshjulet

## Formål

Årshjulet er et verktøy for ingeniørene som jobber med faglærerne. Det skal gi oversikt over årets viktige frister, først og fremst **hvilket prosjekt som kjøres når, og hva ingeniøren må bestille til det**.

## Utviklingsprinsipp: lokalt først, klart for VPS

Vi utvikler på egen PC, men slik at årshjulet når som helst kan flyttes til en VPS på universitetsnettet uten å skrive det om.

- **Alt som er ulikt mellom PC og server settes med miljøvariabler.** Ingen kodeendring ved flytting.
- **Data ligger utenfor koden**, i én datamappe. Kodemappen kan byttes ut fritt.
- **Samme server begge steder** (waitress). Det som virker lokalt, virker på serveren.
- **Funksjoner som bare gir mening på egen PC** (åpne Excel, åpne mappe, åpne nettleser) er slått av i servermodus, og knappene skjules.
- **Tester** passer på fristlogikken og API-et, så større ombygginger kan gjøres trygt.

| Variabel | Standard | Brukes til |
|---|---|---|
| `ARSHJUL_DATA` | mappen over `arshjul/` | datamappen: `BOM/`, `Bestillingslister/`, `arshjul.db`, `.cache/` |
| `ARSHJUL_MODUS` | `lokal` | `lokal` eller `server` |
| `ARSHJUL_HOST` | `127.0.0.1` | adressen serveren lytter på |
| `ARSHJUL_PORT` | `5057` | porten |
| `ARSHJUL_SECRET_KEY` | tilfeldig lokalt | påkrevd i servermodus (innlogging) |
| `ARSHJUL_NO_OPEN` | ikke satt | satt = åpne aldri filer eller nettleser |

## Beslutninger så langt

- Faglærerens endringer gjelder **med en gang**, men ingeniøren får **varsel** om dem.
- Året regnes som **skoleår**: august til juli (uke 32 til uke 31).
- Bestillingsstatus føres **per leverandør** i hver gjennomføring, med et eget steg for **innlevering og telling** etter prosjektet.
- Tellingen kan oppdatere **kun «på lager»-kolonnen** i BOM-en, og **bare etter at du har bekreftet**. Dette er et bevisst unntak fra regelen om at årshjulet aldri skriver til en BOM.
- **Faglæreren bestemmer *når* og *hvor mange*:** navn, type, semester, start- og sluttuke, antall sett og notat.
  **Ingeniøren bestemmer *hvor lang tid bestillingen trenger*:** leveringstid, buffer, hvilken BOM som er koblet til, og farge. Faglæreren ser disse, men kan ikke endre dem.

## Fase 0 – Grunnmur

- [x] Innstillinger via miljøvariabler (`config.py`), data skilt fra kode, servermodus
- [x] waitress som server, `--debug` for utvikling
- [x] Tester (`arshjul/tests/`)
- [x] Git (lokalt)
- [x] **Ny datamodell** i SQLite (`arshjul.db` i datamappen, `db.py`). Prosjekt og gjennomføring er på plass; bestilling, telling og endringslogg kommer i senere faser:

  ```
  Skoleår        2026/27
  Prosjekt       Insektsløp · BOM · farge · leveringstid og buffer · notat
  Gjennomføring  Insektsløp 2026/27 · uke 44 2026 · 30 sett
   └ Bestilling  per leverandør · status · dato · ordrenr · frosset kopi   (fase 1)
   └ Telling     per linje · innlevert antall                              (fase 3)
  Endringslogg   hvem · når · hva                                          (fase 4)
  ```

  *Prosjekt* er «malen» som går igjen hvert år. *Gjennomføring* er én konkret kjøring med ekte år og uker. `arshjul.json` ble hentet inn i skoleåret 2026/27 og heter nå `arshjul.json.importert`.
- [x] **Uke- og fristlogikken på ett sted** (`frister.py`). Serveren regner ut alle datoer; nettleseren plasserer dem bare i hjulet.
- [x] Hjulet viser ett skoleår om gangen og starter i august. Uke 53 og årsskiftet regnes med ekte datoer. «Neste 8 uker» og «Neste frist» ser på tvers av skoleår, så juni-frister for høsten kommer med.
- [x] Stengte perioder lagres i databasen og endres nederst på siden.

## Fase 1 – Bestillingsstatus

- Én rad per leverandør, hentet fra Leverandør-kolonnen i BOM-en. Linjer uten leverandør samles i «Ukjent leverandør».
- Status: Ikke bestilt → Bestilt → Delvis mottatt / Mottatt → Innlevert og telt. Gjennomføringen får den «dårligste» statusen.
- Når du trykker «Bestilt», lagres dato, hvem, ordrenummer og en **frosset kopi** av linjene (bestillingslisten har allerede en `list_id`).
- **«BOM-en er endret etter at du bestilte»**: den frosne kopien sammenlignes med BOM-en med dagens `diff()`, og du ser hva som må bestilles i tillegg.
- I hjulet og «neste 8 uker»: grønn = bestilt, gul = frist snart, **rød = fristen er passert og ingenting er bestilt**. «Neste frist» hopper over det som er bestilt.

## Fase 2 – Skoleår og historikk

- **«Kopier til neste skoleår»**: lager neste års gjennomføringer fra årets, med uker og antall sett fylt ut. (Databasen støtter allerede flere gjennomføringer per prosjekt; det mangler bare knappen.)
- **Påminnelse om våren.** Eksempel: Arduino starter kit starter i uke 34, med 2 uker levering og 3 uker buffer. Fellesferien hoppes over, så bestillingsfristen blir **uke 25, i juni, i forrige skoleår**. Neste skoleår må derfor være satt opp senest i april.
- **Historikk**: hva som faktisk ble bestilt, og hva det kostet, per gjennomføring og per skoleår.

## Fase 3 – Telling etter prosjektet

- Før opp innlevert antall per linje.
- Skill mellom **forbruk** (motstander, ledninger), som ventes brukt opp, og **utstyr** (Arduino, Zumo), som skal komme tilbake. Kanskje via Kategori-kolonnen, ellers en ny kolonne.
- Gir **forslag til «på lager»** neste år. Etter bekreftelse skrives tallet inn i BOM-en (bare den kolonnen). `LES MEG.md` oppdateres med unntaket.
- Viser svinn og slitasje per prosjekt.

## Fase 4 – Server og faglærerpanel

**Innlogging og roller**
- Brukere lages fra kommandolinjen. Ingen selvregistrering. Start: 1 ingeniør og 1 faglærer.
- Passord lagres som hash (`werkzeug.security`).
- I lokal modus er du automatisk logget inn som ingeniør.

**Faglærerpanelet**
- Faglæreren legger til, endrer og fjerner sine egne gjennomføringer (feltene står under «Beslutninger»).
- Hele hjulet kan ses, men ikke endres, så det er lett å se kollisjoner.
- Mens faglæreren endrer, vises konsekvensen: «Bestillingsfrist: uke 34, om 2 uker.»
- Sletting er **myk**: gjennomføringen skjules, og du får varsel.

**Varsler til ingeniøren**
- Hver endring logges (hvem, når, felt, fra → til).
- Endringer vises som en boks med «OK, sett», samme mønster som BOM-endringene i dag.
- **Sterkere varsel** når en endring flytter en frist forbi dagens dato eller innenfor 2 uker.

**Sikkerhet**
- HTTPS via Caddy eller nginx foran waitress.
- Sikre informasjonskapsler, CSRF-beskyttelse, låsing etter flere feil innlogginger.
- Bare tilgjengelig på universitetsnettet eller via VPN.

## Flytte til VPS – sjekkliste

1. Installer Python 3.10 eller nyere på VPS-en.
2. Hent koden (`git clone`), og lag et Python-miljø: `python -m venv .venv` og `pip install -r requirements.txt`.
3. Lag en datamappe, for eksempel `/srv/arshjul-data`, og kopier over `arshjul.db` (og BOM-ene, hvis de skal ligge på serveren). Stopp årshjulet lokalt først, så databasen er ferdig lagret.
4. Sett `ARSHJUL_MODUS=server`, `ARSHJUL_DATA`, `ARSHJUL_SECRET_KEY` (lang og tilfeldig), og eventuelt `ARSHJUL_HOST`/`ARSHJUL_PORT`.
5. Kjør `python app.py` som en systemd-tjeneste, med Caddy eller nginx foran for HTTPS.
6. Sett opp backup av datamappen.

## Parkert / senere

- Kalenderabonnement (ICS) med frister og prosjektuker i Outlook
- Påminnelser på e-post eller Teams før frister som ikke er bestilt
- Samle bestillinger på tvers av prosjekter hos samme leverandør
- Leveringstid per leverandør i stedet for per prosjekt
- Felles lager på tvers av BOM-er, koblet på varenummer
- Kommentarfelt per prosjekt («trenger 5 sett ekstra i år»)
- Faglæreren bekrefter planen for høsten før sommeren

## Åpne spørsmål

- **Hvor skal BOM-ene ligge når årshjulet er på serveren?**
  - *Hybrid:* serveren eier prosjektene, PC-en din beholder BOM-ene og Excel-flyten og henter prosjektene fra serveren. Bevarer dagens arbeidsflyt, men det blir to deler å drive.
  - *Alt på serveren:* BOM-ene lastes opp og ned i nettleseren. Én app, men «åpne i Excel – lagre – tilbake» forsvinner.
- Hvordan merke forbruk og utstyr i BOM-en?
- Hva krever IT for en VPS (operativsystem, tilgang, backup, sikkerhetsgjennomgang)?
