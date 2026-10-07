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
| `ARSHJUL_BOM` | `<datamappen>/BOM` | BOM-mappen, f.eks. på en nettverksmappe |
| `ARSHJUL_MODUS` | `lokal` | `lokal` eller `server` |
| `ARSHJUL_HOST` | `127.0.0.1` | adressen serveren lytter på |
| `ARSHJUL_PORT` | `5057` | porten |
| `ARSHJUL_SECRET_KEY` | tilfeldig lokalt | påkrevd i servermodus (innlogging), minst 32 tegn |
| `ARSHJUL_BAK_PROXY` | `av` lokalt, `på` på server | stol på `X-Forwarded-*` fra Caddy/nginx |
| `ARSHJUL_NO_OPEN` | ikke satt | satt = åpne aldri filer eller nettleser |

Innstillingene kan også stå i `arshjul/innstillinger.env` (se `innstillinger.eksempel.env`). Satte miljøvariabler går foran.

## Beslutninger så langt

- Faglærerens endringer gjelder **med en gang**, men ingeniøren får **varsel** om dem.
- Året regnes som **skoleår**: august til juli (uke 32 til uke 31).
- Bestillingsstatus føres **per leverandør** i hver gjennomføring, med et eget steg for **innlevering og telling** etter prosjektet.
- Tellingen kan oppdatere **kun «på lager»-kolonnen** i BOM-en, og **bare etter at du har bekreftet**. Dette er et bevisst unntak fra regelen om at årshjulet aldri skriver til en BOM.
- Utstyr/forbruk lagres i årshjulet per prosjekt og linje, ikke i BOM-en.
- **Faglæreren bestemmer *når* og *hvor mange*:** navn, type, semester, start- og sluttuke, antall sett og notat.
  **Ingeniøren bestemmer *hvor lang tid bestillingen trenger*:** leveringstid, buffer, hvilken BOM som er koblet til, og farge. Faglæreren ser disse, men kan ikke endre dem.
- **BOM-ene ligger i en delt nettverksmappe** som både serveren og ingeniørens PC når. Én app og én database; ingeniøren åpner BOM-ene i Excel fra en tilordnet stasjon. At bare ingeniøren skal se BOM-ene, løses med roller i appen, ikke med hvor filene ligger. (Valgt fremfor *hybrid* – to apper og synkronisering – og fremfor opplasting/nedlasting i nettleseren.)

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
- [x] **Varemottak** (migrering 5): «Registrer mottak …» legger det som kom til «På lager» i BOM-en (helt eller per linje), og setter status til Mottatt eller Delvis mottatt. Legges til det cellen har nå, så endringer i Excel imens ikke forsvinner. «Angre mottak» trekker det fra igjen (aldri under 0). Sikkerhetskopi og logg som ved telling. BOM-en viser dermed alltid det som står på hylla, og «Å bestille» i Excel blir riktig.
- [x] «Trenger mer / bestilt for mye» sjekkes bare før prosjektet starter og før det er telt – etterpå endres BOM-en av tellinger og neste års innkjøp.
- [ ] Prosjekter uten BOM kan ikke markeres som bestilt ennå.

**En BOM-linje kjennes igjen på leverandør + varenummer** (eller leverandør + beskrivelse uten varenummer), fordi varenumre bare er unike hos én leverandør – RS og Farnell kan ha samme nummer på hver sin vare. Bytter en linje leverandør, regnes den som en ny linje. (Migrering 6 gjorde om lagrede bestillinger; tellinger fra testingen ble fjernet.)

**Merk:** leverandørnavn må skrives likt i alle BOM-er («RS» og «RS-Online» er to leverandører). Det får betydning når bestillinger skal samles på tvers av prosjekter.

## Fase 2 – Skoleår og historikk ✓

- [x] **«Kopier til neste skoleår»**: velg hvilke prosjekter som skal være med. Uker og antall sett kopieres; BOM, leveringstid og buffer følger prosjektet. Bestillinger kopieres ikke. Prosjekter som allerede finnes i neste år hoppes over.
- [x] **Påminnelse om våren** (`frister.oppsett_varsel`). Når prosjekter fra i år mangler i neste skoleår, og den første fristen deres er mindre enn 12 uker unna, vises et varsel øverst med en knapp for å sette opp året. Eksempel: Arduino starter kit i uke 34 har frist i **uke 25, i juni** – varselet kommer i starten av april.
- [x] **Historikk**: hva som er registrert som bestilt (eks. mva.), per prosjekt og skoleår, med pris per sett og sum per år. Regnes fra de frosne kopiene, så tallene står seg selv om BOM-en endres eller fjernes.
- [ ] Vise de enkelte linjene som ble bestilt et tidligere år (i dag: bla til året og åpne «Bestilling»).

## Fase 3 – Telling etter prosjektet ✓

- [x] Under «Bestilling» → «Telling etter prosjektet»: før opp det du har igjen per linje. Åpnes av seg selv når innleveringsuka er passert.
- [x] **Utstyr** (skal komme tilbake) eller **forbruk** (brukes opp) velges per linje i tellingen og huskes for prosjektet til neste år (tabellen `linjetype`). Kategori-kolonnen brukes ikke – den beskriver hva slags komponent det er.
- [x] Forventet = på lager + bestilt som ikke er lagt på lager ennå (registrert, ellers det BOM-en sa), minus det som er delt ut for forbruk. Avvik og **svinn** (utstyr som ikke kom tilbake, i kroner) vises, og svinn står også i historikken.
- [x] **«Oppdater På lager i BOM-en»** skriver de telte tallene inn i kolonne H, etter bekreftelse. Bare kolonne H endres; formler, formatering og validering står (testet på kopi av en ekte BOM). Det lages alltid en sikkerhetskopi i `BOM/Sikkerhetskopi/` først, og filen må være lukket i Excel.
- [x] **Rette og angre** (migrering 4). En telling kan rettes etter skriving; «før»-tallene fra den opprinnelige tellingen beholdes, så ingenting telles to ganger. Bare endrede linjer skrives på nytt. **«Angre»** setter «På lager» tilbake til tallene fra før tellingen. Begge sjekker først at cellen fortsatt har tallet årshjulet skrev sist; er den endret i Excel siden, røres den ikke, og du får beskjed. Hver skriving og angring logges med sikkerhetskopi. En telling som står i BOM-en kan ikke slettes – angre først.
- [x] **Endret i Excel etter skriving:** tellingen endres ikke av seg selv (den er det som faktisk ble telt, og svinn regnes fra den), men linjen viser «endret i Excel: 30». Var endringen en retting av tellingen, henter **«Bruk tallene fra BOM-en i tellingen»** tallene inn.
- [x] Neste års BOM-utregning bruker den nye lagerbeholdningen.

## Fase 4 – Faglærerpanel ✓ (lokalt) · Server ⏳ (venter på IT)

**Innlogging og roller** (`auth.py`, `manage.py`, migrering 7)
- [x] **Administrator** (migrering 9): en ingeniør med alle emner, som styrer brukerne under «Brukere» på nettsiden (lage, nytt passord, admin, deaktivere). Det må alltid finnes minst én aktiv administrator. Ingen selvregistrering.
- [x] **Administratorpanel** (`/admin`, `adminpanel.py`, `admin.js`) i stedet for en adminbryter: på hovedsiden er administratoren alltid en vanlig ingeniør; panelet ser og retter alt. Faner: Oversikt (det som bør ryddes), Prosjekter (rett/gjenopprett/slett, med konsekvens først), Emner, Brukere, Bestillinger og tellinger (fjern feilregistreringer; BOM-skrivinger angres først), Endringslogg (søk), Opprydding (kvitter alle, slett skoleår), System (database, BOM-mappe, sikkerhetskopi). Alle `adminpanel.*`-endepunkter krever administrator på serveren, og rettinger logges med rolle «admin».
- [x] Første administrator: lokalt på siden «Velkommen til årshjulet» når det ikke finnes brukere; på serveren bare med `python manage.py ny-bruker <brukernavn> "<Navn>" ingenior --admin`, så ingen andre rekker det først. `manage.py` er også reserve (`passord`, `admin`, `aktiver`, `deaktiver`, `liste`).
- [x] Passord lagres som hash (scrypt), minst 10 tegn. Låst i 15 minutter etter 5 feil; i tillegg en grense per IP-adresse. Samme melding for feil brukernavn og feil passord.
- [x] **Innlogging er alltid på – lokalt også.** Ingen bruker uten navn, så det som testes lokalt er det som kjører på serveren, og loggen viser alltid hvem. Lokalt lagres nøkkelen i datamappen, og du forblir innlogget i 30 dager (serveren: 12 timer).
- [x] Økten varer 12 timer, og slutter å gjelde når passordet endres eller brukeren deaktiveres.
- [x] **Rollene sjekkes på serveren**, ikke bare i nettleseren. En faglærer når bare de endepunktene som er listet som tillatt (`FAGLARER_TILLATT`) – alt annet er stengt som standard.

**Faglærerpanelet**
- [x] Faglæreren ser bare prosjektene i sine egne emner, og ikke BOM, bestillinger, telling eller kostnader (de sendes ikke engang til nettleseren).
- [x] Faglæreren legger til, endrer og fjerner **sine egne** prosjekter: navn, type, uker, antall sett, notat. Leveringstid, buffer, BOM og farge er ingeniørens.
- [x] ~~Faglærer per prosjekt~~ → erstattet av emner (under).

**Emner og flere ingeniører** (migrering 8)
- [x] Studieprogram (bachelor/master) → **emne** (én faglærer, én ansvarlig ingeniør) → prosjekt → gjennomføring per skoleår. En faglærer kan ha emner med ulike ingeniører.
- [x] **Alle ser bare det de er involvert i** – filtrert på serveren. Ingeniøren: egne emner, pluss prosjekter uten emne/ingeniør (så ingenting blir usynlig). Faglæreren: egne emner. Administratoren: alt. (En instituttoversikt – kollisjoner, felles lager – er parkert; se under.)
- [x] **Eierskap sjekkes på serveren for alt som endrer en gjennomføring** (`kan_handle` i `_eierskap`): ingeniører kan ikke bestille, telle, endre eller slette i andres emner; faglærere bare i egne emner.
- [x] Faglærerens endringer varsles til emnets ingeniør. Bytter emnet ingeniør, overtar den nye varslene som ikke er kvittert.
- [x] Alle ingeniører kan legge inn og endre emner (seksjonen «Emner»). Emner deaktiveres, ikke slettes.
- [x] **Faglæreren lager sine egne emner og velger ingeniør** (migrering 10). Faglæreren kan ikke bytte faglærer eller endre andres emner. Ingeniøren som velges – og den som tas av – får et varsel med «OK, sett» (ikke når du velger deg selv). Ingen godkjenning: ingeniøren får emnet med en gang.
- [x] «Kopier til neste skoleår» og vårpåminnelsen gjelder egne emner.
- Lager og BOM er fortsatt per prosjekt (felles lager på tvers av emner er parkert).
- [x] Mens faglæreren endrer, vises konsekvensen ved fristen: «Fristen er om 1 uke – ingeniøren varsles».
- [x] Fjerning er **myk**: prosjektet skjules, og ingeniøren kan gjenopprette eller bekrefte sletting.
- [ ] Endre navn og notat i tabellen (i dag bare når prosjektet legges til).

**Varsler til ingeniøren**
- [x] Hver endring logges (hvem, når, felt, fra → til) – også ingeniørens, for sporbarhet. Bestilling, varemottak og telling lagrer hvem som gjorde det.
- [x] Faglærernes endringer vises øverst som en boks per prosjekt med «OK, sett», og ny bestillingsfrist.
- [x] **Rød** når fristen er passert eller innen 2 uker og ikke alt er bestilt, eller du må bestille mer.

**Sikkerhet**
- [x] Informasjonskapsel HttpOnly, SameSite=Lax, og Secure i servermodus.
- [x] CSRF-token på alt som endrer noe – også lokalt, så en annen nettside ikke kan få nettleseren din til å endre årshjulet. Nytt token ved innlogging.
- [x] Sikkerhetshoder: Content-Security-Policy, X-Frame-Options, nosniff, ingen mellomlagring av API-svar.
- [x] `ARSHJUL_SECRET_KEY` må være minst 32 tegn i servermodus.
- [x] Bak Caddy/nginx: riktig klientadresse og https via `X-Forwarded-*` (`ARSHJUL_BAK_PROXY`, på som standard i servermodus).
- [ ] HTTPS og tilgang bare fra universitetsnettet/VPN – settes opp på VPS-en.

## Flytte til VPS – sjekkliste

1. Installer Python 3.10 eller nyere på VPS-en.
2. Hent koden (`git clone`), og lag et Python-miljø: `python -m venv .venv` og `pip install -r requirements.txt`.
3. Lag en datamappe, for eksempel `/srv/arshjul-data`, og kopier over `arshjul.db`. Stopp årshjulet lokalt først, så databasen er ferdig lagret.
4. Monter BOM-fellesmappen (CIFS, med `iocharset=utf8` for æøå), for eksempel på `/mnt/arshjul-bom`.
5. Sett `ARSHJUL_MODUS=server`, `ARSHJUL_DATA`, `ARSHJUL_BOM=/mnt/arshjul-bom` og `ARSHJUL_SECRET_KEY` (lag med `python -c "import secrets; print(secrets.token_hex(32))"`) – i `innstillinger.env` (lesbar bare for tjenestebrukeren) eller i systemd-tjenesten. La `ARSHJUL_HOST=127.0.0.1`, så bare Caddy/nginx når appen.
6. Lag administratoren: `python manage.py ny-bruker <brukernavn> "<Navn>" ingenior --admin`. Resten av brukerne lages på nettsiden under «Brukere». (Tar du med `arshjul.db` fra PC-en, følger brukerne med.)
7. Kjør `python app.py` som en systemd-tjeneste, med Caddy foran for HTTPS, f.eks. `arshjul.ntnu.no { reverse_proxy 127.0.0.1:5057 }`.
8. Sett opp backup av datamappen.

## Parkert / senere

- **Koden over på engelsk**: variabelnavn, funksjoner, kommentarer, commit-meldinger og API-felt. Tekst brukerne ser (grensesnitt, feilmeldinger, LES MEG) forblir norsk. Gjøres samlet, med testene som sikkerhetsnett – ikke bit for bit.

- **Instituttoversikt**: se alle emner samtidig for å finne kollisjoner (travle uker, delte rom og utstyr) og felles lager. Bevisst tatt bort for nå – i et stort institutt blir det rot. Administratoren ser allerede alt; en egen visning kan komme senere.

- Kalenderabonnement (ICS) med frister og prosjektuker i Outlook
- Påminnelser på e-post eller Teams før frister som ikke er bestilt
- Samle bestillinger på tvers av prosjekter hos samme leverandør
- Leveringstid per leverandør i stedet for per prosjekt
- Felles lager på tvers av BOM-er, koblet på varenummer
- Kommentarfelt per prosjekt («trenger 5 sett ekstra i år»)
- Faglæreren bekrefter planen for høsten før sommeren

## BOM-er på nettverksmappe – testet

Testet 6. oktober 2026 fra Windows mot `\\felles.ansatt.ntnu.no\...\evenjc\claude-testing\BOM`, med kopier av de ekte BOM-ene:

| Hva | Resultat |
|---|---|
| Lese en BOM | ca. 25 ms |
| Laste siden (`/api/state`) | 130 ms første gang, deretter ca. 60 ms |
| Oppdatere «På lager» med sikkerhetskopi | ca. 70 ms, bare kolonne H endret |
| Endringer gjort i filen oppdages | ja |
| Fil åpen i Excel (`~$`-fil) eller låst | stoppes med tydelig melding; siden lastes likevel |
| Ny BOM-mal, filnavn med æøå og tankestrek | virker |

**Testet av ingeniøren 6. oktober 2026:** to nye BOM-er laget med «Lag BOM-mal», åpnet og endret i Excel – alt havnet på nettverksmappen og ble lest riktig av årshjulet.

**Gjenstår å teste:**
- «Oppdater På lager» mens BOM-en er åpen i Excel på nettverksmappen (skal nekte).
- Linux-VPS-en sin tilkobling til mappen (CIFS-montering). Kan bare testes når VPS-en finnes.
- Hvordan «Åpne i Excel» skal virke fra en server: knapp som kopierer stien, eller en Office-lenke (`ms-excel:`) – må prøves.

## Åpne spørsmål (til IT)

- Kan vi få en **egen fellesmappe for tjenesten** (ikke under en personlig `ansatte\…`-mappe), som VPS-en kan montere med lese- og skrivetilgang via en tjenestekonto, og som ingeniøren kan tilordne som stasjon?
- Hva krever IT for en VPS (operativsystem, tilgang, backup, sikkerhetsgjennomgang)?
- Tas det backup av fellesmappen? (Sikkerhetskopiene i `BOM/Sikkerhetskopi/` dekker bare årshjulets egne endringer.)
