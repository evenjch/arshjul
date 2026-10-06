# Årshjul for innkjøp

Et lite lokalt program (Flask) som viser årets innkjøp til undervisningsprosjektene som et årshjul, med bestillingsfrister regnet bakover fra bruksuke. Det leser BOM-ene rett fra mappen.

## Mappene

```
Innkjøp undervisning/
├── BOM/                 Excel-filene – sjefen for komponentene
├── Bestillingslister/   bestillingslister som lages fra hjulet
├── arshjul.db           sjefen for prosjekter, uker, antall sett og hvilken BOM som hører til
└── arshjul/             selve programmet
```

- **Årshjulet viser ett skoleår om gangen**, fra uke 32 (august) til uke 31. Bla med ‹ og › over hjulet. Uke 32–53 er høsten, uke 1–31 er våren etter.
- **Et prosjekt** (navn, type, leveringstid, buffer, BOM) går igjen fra år til år. **Ukene og antall sett** gjelder bare det skoleåret du ser på.
- **Årshjulet skriver nesten aldri til en eksisterende BOM.** Det leser dem og lager nye maler. Eneste unntak: «På lager» (kolonne H), når du registrerer et varemottak eller skriver inn en telling. Det skjer bare når du bekrefter, filen må være lukket i Excel, og det lages alltid en sikkerhetskopi i `BOM/Sikkerhetskopi/` først.
- **Antall sett** styres i årshjulet. Å bestille = per sett × antall sett − på lager.
- **Stengte perioder** (jul, påske, fellesferie) endres nederst på siden.
- Legg gjerne hele mappen i OneDrive, så får du backup automatisk. Ta ikke backup av `arshjul.db` mens årshjulet kjører og lagrer.
- Før versjon med skoleår lå alt i `arshjul.json`. Den hentes inn automatisk første gang og får navnet `arshjul.json.importert`.

## Starte

Dobbeltklikk **`arshjul/Start årshjul.bat`**. Første gang lager den et eget Python-miljø (`.venv`) og installerer Flask, openpyxl og waitress. Det tar et minutt. Når `requirements.txt` endres, installeres pakkene på nytt automatisk. Deretter åpnes årshjulet i nettleseren på http://127.0.0.1:5057/.

**Første gang** viser nettleseren «Velkommen til årshjulet»: lag din egen bruker. Du blir **administrator**. Deretter logger du inn med brukernavn og passord – også lokalt, akkurat som på serveren senere. Du forblir innlogget i 30 dager på din egen PC.

Svartvinduet må stå åpent mens du bruker hjulet. Lukk det for å stoppe programmet. Dobbeltklikker du på startfilen mens hjulet allerede kjører, åpnes bare nettleseren.

Krever Python 3.10 eller nyere. Uten startfilen: `pip install -r requirements.txt` og `python app.py` fra `arshjul/`.

## Vanlig bruk

| Du vil …                       | Gjør slik                                                                 |
|--------------------------------|---------------------------------------------------------------------------|
| Legge til et prosjekt          | Bla til riktig skoleår, trykk «+ Legg til nytt prosjekt»                  |
| Sette opp neste skoleår        | «Kopier til 2027/28 …» over tabellen, velg prosjektene. Årshjulet minner deg på det om våren |
| Telle etter prosjektet         | Åpne «Bestilling» → «Telling etter prosjektet». Velg utstyr/forbruk, før opp det du har igjen, «Lagre telling» |
| Oppdatere lageret i BOM-en     | Etter tellingen: «Oppdater «På lager» i BOM-en …» og bekreft. Lukk BOM-en i Excel først |
| Rette en telling               | Endre tallene, «Lagre endringer», så «Skriv endringene til BOM-en …». Bare det som er endret skrives |
| Angre en telling               | «Angre – sett «På lager» tilbake …». Linjer du har endret i Excel siden, røres ikke |
| Se hva prosjektene har kostet  | «Historikk» under tabellen: bestilt per prosjekt og skoleår, og per sett  |
| Lage BOM for nytt prosjekt     | «Lag BOM-mal» – filen lages i BOM-mappen med navn, dato, frist og antall sett, og åpnes i Excel |
| Koble til en BOM du allerede har | Legg filen i BOM-mappen, velg den i «Koble til fil …»                  |
| Endre en BOM                   | «Åpne i Excel», endre, lagre, gå tilbake til nettleseren                  |
| Se hva som er endret           | Endringer siden sist vises over tabellen. «OK, sett» når du har sett dem  |
| Lage bestillingsliste          | «Bestillingsliste» – lagres i Bestillingslister og åpnes. For én leverandør: «Liste» under «Bestilling» |
| Registrere at du har bestilt   | Trykk på statusen i «Bestilling»-kolonnen, skriv ordrenummer, «Registrer bestilt» for hver leverandør |
| Registrere mottak              | «Registrer mottak …» under leverandøren: skriv hvor mange som kom. Det legges til «På lager» i BOM-en, og status blir Mottatt eller Delvis mottatt |
| Bestille det som mangler       | Når BOM-en eller antall sett endres etter bestilling, vises det over tabellen. Bestill, og trykk «Registrer tilleggsbestilling» |

Siden oppdateres av seg selv når du går tilbake til nettleservinduet.

## Emner, faglærere og ingeniører

Hvert prosjekt hører til et **emne** (f.eks. IELS1001, bachelor eller master). Emnet har **én faglærer** og **én ansvarlig ingeniør**:

- **Ingeniøren** ser og jobber med prosjektene i sine egne emner – bestilling, varemottak, telling, BOM. «Viser mine emner / Viser hele instituttet» over hjulet viser alle emner; andre ingeniørers prosjekter kan da bare ses, ikke endres. Prosjekter uten emne kan alle ingeniører jobbe med, så ingenting blir liggende.
- **Faglæreren** ser hele året, men endrer bare prosjektene i sine egne emner (navn, type, uker, antall sett, notat), og ser ikke BOM, bestillinger eller kostnader. Endringene dukker opp øverst hos **emnets ingeniør** med «OK, sett».
- **Emner** legges inn og endres under «Emner» nederst på siden, av hvilken som helst ingeniør. Bytter et emne ingeniør, overtar den nye varslene som ikke er kvittert. Emner deaktiveres i stedet for å slettes, så historikken står.

**Alle logger inn**, også lokalt: administrator, ingeniør eller faglærer. Det finnes ingen bruker uten navn, så loggen viser alltid hvem som gjorde hva.

- **Administrator** (deg) er en ingeniør med alle emner, og den eneste som ser «Brukere» nederst på siden. Der lager du ingeniører og faglærere, gir nytt passord, gjør andre til administrator, og deaktiverer brukere. Det må alltid finnes minst én aktiv administrator.
- Deaktiverte brukere kan ikke logge inn, men navnet står fortsatt i loggene.

**Reserve fra kommandolinjen**, i `arshjul/` – f.eks. hvis du glemmer passordet ditt:

```
.venv\Scripts\python manage.py liste
.venv\Scripts\python manage.py passord even         (nytt passord)
.venv\Scripts\python manage.py admin even           (gjør en ingeniør til administrator)
.venv\Scripts\python manage.py aktiver even         (også: opphever låsing etter feil passord)
.venv\Scripts\python manage.py ny-bruker even "Even" ingenior --admin
```

### Prøve faglærervisningen

1. Under **«Brukere»**: lag en faglærer, f.eks. `ola` med et startpassord.
2. Under **«Emner»**: «+ Nytt emne», f.eks. `TEST1001` med faglærer Ola og deg selv som ansvarlig ingeniør. Velg så «Emne: TEST1001» under navnet på et prosjekt.
3. **Logg inn som Ola i et privat/inkognito-vindu** (eller en annen nettleser), så kan du være begge samtidig. Endre uker eller antall sett, legg til eller fjern et prosjekt.
4. **Gå tilbake til ditt eget vindu**: endringene står øverst med «OK, sett», og fjernede prosjekter med «Gjenopprett».

Rydd etterpå: fjern haken «aktiv» på testlæreren og testemnet.

## Filene i programmet

- `app.py`: Flask-appen og API-et
- `config.py`: innstillinger fra miljøvariabler (datamappe, modus, port, innlogging)
- `auth.py`: innlogging, roller og CSRF-beskyttelse
- `manage.py`: brukere fra kommandolinjen
- `frister.py`: uker, skoleår og frister – det eneste stedet de regnes ut
- `db.py`: databasen (prosjekter, gjennomføringer per skoleår, bestillinger, stengte perioder) og oppgraderinger av den
- `bestilling.py`: bestillingsstatus per leverandør, frossen kopi og hva som mangler
- `telling.py`: telling etter prosjektet – forventet, avvik og svinn
- `bom.py`: lesing av BOM, utregning, endringer, mal, bestillingsliste og oppdatering av «På lager»
- `maler/`: BOM-malen og bestillingsliste-malen
- `templates/index.html`, `static/`: selve årshjulet
- `tests/`: automatiske tester
- `.cache/bom-sist-sett.json` (i datamappen, ved siden av `BOM/`): hvordan hver BOM så ut sist du trykket «OK, sett»

## Utvikling

Planene videre står i [VEIKART.md](VEIKART.md).

- **Koden ligger i git** (lokalt, gren `main`). BOM-ene, bestillingslistene og `arshjul.db` er brukerdata og holdes utenfor (se `.gitignore`).
- **Kjør med automatisk omlasting:** `.venv\Scripts\python app.py --debug` fra `arshjul/`.
- **Kjør testene:** `.venv\Scripts\python -m pip install -r requirements-dev.txt` én gang, deretter `.venv\Scripts\python -m pytest`. Testene bruker en midlertidig mappe og rører aldri de ekte BOM-ene.
- **Innstillinger** settes med miljøvariabler: `ARSHJUL_DATA` (datamappe), `ARSHJUL_MODUS` (`lokal`/`server`), `ARSHJUL_HOST`, `ARSHJUL_PORT`, `ARSHJUL_SECRET_KEY`. Se `config.py` og VEIKART.md. De kan også skrives i `arshjul/innstillinger.env` (kopier `innstillinger.eksempel.env`), f.eks. `ARSHJUL_BOM=\\server\deling\BOM` for BOM-er på en nettverksmappe.
- **Endre databasen:** legg en ny oppgradering nederst i `MIGRERINGER` i `db.py`. Endre aldri en som allerede finnes; den har kjørt hos noen.
