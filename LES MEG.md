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
- **Årshjulet skriver aldri til en eksisterende BOM.** Det leser dem, og det lager nye maler.
- **Antall sett** styres i årshjulet. Å bestille = per sett × antall sett − på lager.
- **Stengte perioder** (jul, påske, fellesferie) endres nederst på siden.
- Legg gjerne hele mappen i OneDrive, så får du backup automatisk. Ta ikke backup av `arshjul.db` mens årshjulet kjører og lagrer.
- Før versjon med skoleår lå alt i `arshjul.json`. Den hentes inn automatisk første gang og får navnet `arshjul.json.importert`.

## Starte

Dobbeltklikk **`arshjul/Start årshjul.bat`**. Første gang lager den et eget Python-miljø (`.venv`) og installerer Flask, openpyxl og waitress. Det tar et minutt. Når `requirements.txt` endres, installeres pakkene på nytt automatisk. Deretter åpnes årshjulet i nettleseren på http://127.0.0.1:5057/.

Svartvinduet må stå åpent mens du bruker hjulet. Lukk det for å stoppe programmet. Dobbeltklikker du på startfilen mens hjulet allerede kjører, åpnes bare nettleseren.

Krever Python 3.10 eller nyere. Uten startfilen: `pip install -r requirements.txt` og `python app.py` fra `arshjul/`.

## Vanlig bruk

| Du vil …                       | Gjør slik                                                                 |
|--------------------------------|---------------------------------------------------------------------------|
| Legge til et prosjekt          | Bla til riktig skoleår, trykk «+ Legg til nytt prosjekt»                  |
| Sette opp neste skoleår        | «Kopier til 2027/28 …» over tabellen, velg prosjektene. Årshjulet minner deg på det om våren |
| Se hva prosjektene har kostet  | «Historikk» under tabellen: bestilt per prosjekt og skoleår, og per sett  |
| Lage BOM for nytt prosjekt     | «Lag BOM-mal» – filen lages i BOM-mappen med navn, dato, frist og antall sett, og åpnes i Excel |
| Koble til en BOM du allerede har | Legg filen i BOM-mappen, velg den i «Koble til fil …»                  |
| Endre en BOM                   | «Åpne i Excel», endre, lagre, gå tilbake til nettleseren                  |
| Se hva som er endret           | Endringer siden sist vises over tabellen. «OK, sett» når du har sett dem  |
| Lage bestillingsliste          | «Bestillingsliste» – lagres i Bestillingslister og åpnes. For én leverandør: «Liste» under «Bestilling» |
| Registrere at du har bestilt   | Trykk på statusen i «Bestilling»-kolonnen, skriv ordrenummer, «Registrer bestilt» for hver leverandør |
| Registrere mottak              | Velg «Delvis mottatt» eller «Mottatt» for leverandøren                    |
| Bestille det som mangler       | Når BOM-en eller antall sett endres etter bestilling, vises det over tabellen. Bestill, og trykk «Registrer tilleggsbestilling» |

Siden oppdateres av seg selv når du går tilbake til nettleservinduet.

## Filene i programmet

- `app.py`: Flask-appen og API-et
- `config.py`: innstillinger fra miljøvariabler (datamappe, modus, port)
- `frister.py`: uker, skoleår og frister – det eneste stedet de regnes ut
- `db.py`: databasen (prosjekter, gjennomføringer per skoleår, bestillinger, stengte perioder) og oppgraderinger av den
- `bestilling.py`: bestillingsstatus per leverandør, frossen kopi og hva som mangler
- `bom.py`: lesing av BOM, utregning, endringer, mal og bestillingsliste
- `maler/`: BOM-malen og bestillingsliste-malen
- `templates/index.html`, `static/`: selve årshjulet
- `tests/`: automatiske tester
- `.cache/bom-sist-sett.json` (i datamappen, ved siden av `BOM/`): hvordan hver BOM så ut sist du trykket «OK, sett»

## Utvikling

Planene videre står i [VEIKART.md](VEIKART.md).

- **Koden ligger i git** (lokalt, gren `main`). BOM-ene, bestillingslistene og `arshjul.db` er brukerdata og holdes utenfor (se `.gitignore`).
- **Kjør med automatisk omlasting:** `.venv\Scripts\python app.py --debug` fra `arshjul/`.
- **Kjør testene:** `.venv\Scripts\python -m pip install -r requirements-dev.txt` én gang, deretter `.venv\Scripts\python -m pytest`. Testene bruker en midlertidig mappe og rører aldri de ekte BOM-ene.
- **Innstillinger** settes med miljøvariabler: `ARSHJUL_DATA` (datamappe), `ARSHJUL_MODUS` (`lokal`/`server`), `ARSHJUL_HOST`, `ARSHJUL_PORT`, `ARSHJUL_SECRET_KEY`. Se `config.py` og VEIKART.md.
- **Endre databasen:** legg en ny oppgradering nederst i `MIGRERINGER` i `db.py`. Endre aldri en som allerede finnes; den har kjørt hos noen.
