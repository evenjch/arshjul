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
- Utstyr/forbruk lagres i årshjulet per prosjekt og linje, ikke i BOM-en.
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

## Fase 1 – Bestillingsstatus ✓

- [x] Én rad per leverandør, hentet fra Leverandør-kolonnen i BOM-en. Linjer uten leverandør samles i «Ukjent leverandør». Linjer der alt er på lager teller ikke.
- [x] Status: Ikke bestilt → Bestilt → Delvis mottatt / Mottatt. Gjennomføringen får den «dårligste» statusen («2 av 3 bestilt»). Innlevert og telt kommer i fase 3.
- [x] Når du trykker «Registrer bestilt», lagres dato, ordrenummer, antall sett og en **frosset kopi** av linjene (`bestilling.py`, tabellen `bestilling`). «Hvem» lagres når innlogging kommer i fase 4.
- [x] **«Trenger mer enn det som er bestilt»**: den frosne kopien sammenlignes med det BOM-en og antall sett krever nå. Du ser hva som mangler per linje, og registrerer en tilleggsbestilling.
- [x] I hjulet, «neste 8 uker» og toppfeltet: grønn ring = bestilt, gul = frist snart eller må bestille mer, **rød = fristen er passert og ikke bestilt**. «Neste frist» hopper over det som er bestilt. Prosjekter som allerede har startet uten registrert bestilling markeres ikke.
- [x] Bestillingsliste for én leverandør.
- [ ] Prosjekter uten BOM kan ikke markeres som bestilt ennå.

**Merk:** leverandørnavn må skrives likt i alle BOM-er («RS» og «RS-Online» er to leverandører). Det får betydning når bestillinger skal samles på tvers av prosjekter.

## Fase 2 – Skoleår og historikk ✓

- [x] **«Kopier til neste skoleår»**: velg hvilke prosjekter som skal være med. Uker og antall sett kopieres; BOM, leveringstid og buffer følger prosjektet. Bestillinger kopieres ikke. Prosjekter som allerede finnes i neste år hoppes over.
- [x] **Påminnelse om våren** (`frister.oppsett_varsel`). Når prosjekter fra i år mangler i neste skoleår, og den første fristen deres er mindre enn 12 uker unna, vises et varsel øverst med en knapp for å sette opp året. Eksempel: Arduino starter kit i uke 34 har frist i **uke 25, i juni** – varselet kommer i starten av april.
- [x] **Historikk**: hva som er registrert som bestilt (eks. mva.), per prosjekt og skoleår, med pris per sett og sum per år. Regnes fra de frosne kopiene, så tallene står seg selv om BOM-en endres eller fjernes.
- [ ] Vise de enkelte linjene som ble bestilt et tidligere år (i dag: bla til året og åpne «Bestilling»).

## Fase 3 – Telling etter prosjektet ✓

- [x] Under «Bestilling» → «Telling etter prosjektet»: før opp det du har igjen per linje. Åpnes av seg selv når innleveringsuka er passert.
- [x] **Utstyr** (skal komme tilbake) eller **forbruk** (brukes opp) velges per linje i tellingen og huskes for prosjektet til neste år (tabellen `linjetype`). Kategori-kolonnen brukes ikke – den beskriver hva slags komponent det er.
- [x] Forventet = på lager + bestilt (registrert, ellers det BOM-en sa), minus det som er delt ut for forbruk. Avvik og **svinn** (utstyr som ikke kom tilbake, i kroner) vises, og svinn står også i historikken.
- [x] **«Oppdater På lager i BOM-en»** skriver de telte tallene inn i kolonne H, etter bekreftelse. Bare kolonne H endres; formler, formatering og validering står (testet på kopi av en ekte BOM). Det lages alltid en sikkerhetskopi i `BOM/Sikkerhetskopi/` først, og filen må være lukket i Excel. Etter skriving er tellingen låst.
- [x] Neste års BOM-utregning bruker den nye lagerbeholdningen.

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
- Hva krever IT for en VPS (operativsystem, tilgang, backup, sikkerhetsgjennomgang)?
