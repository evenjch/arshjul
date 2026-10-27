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
| `ARSHJUL_BOM_WINDOWS` | ikke satt | på serveren: BOM-mappen slik ingeniørens PC ser den (`\\kunde.it.ntnu.no\ies-arshjul$\BOM`) – gir «Kopier sti» og «Åpne i Excel» |

Innstillingene kan også stå i `arshjul/innstillinger.env` (se `innstillinger.eksempel.env`). Satte miljøvariabler går foran.

## Beslutninger så langt

- Faglærerens endringer gjelder **med en gang**, men ingeniøren får **varsel** om dem.
- Året regnes som **skoleår**: august til juli (uke 32 til uke 31).
- Bestillingsstatus føres **per leverandør** i hver gjennomføring, med et eget steg for **innlevering og telling** etter prosjektet.
- Tellingen kan oppdatere **kun «på lager»-kolonnen** i BOM-en, og **bare etter at du har bekreftet**. Dette er et bevisst unntak fra regelen om at årshjulet aldri skriver til en BOM.
- **Antall sett styres i årshjulet, og skrives i BOM-en (B10)** – formlene for «Å bestille» i Excel bruker det. Andre unntak. Flere skoleår deler BOM-en, så B10 følger **den neste gjennomføringen som ikke har startet og ikke er telt** – bare den kan få avvik og skrives til. Skrives automatisk når antallet endres, med sikkerhetskopi, når filen ikke er åpen i Excel, og når B10 ikke er endret for hånd. Ellers vises avviket hos ingeniøren med knappen «Skriv N til BOM-en».
- Utstyr/forbruk lagres i årshjulet per prosjekt og linje, ikke i BOM-en.
- **Faglæreren bestemmer *når* og *hvor mange*:** navn, type, semester, start- og sluttuke, antall studenter, gruppestørrelse, ekstra sett og notat.
  **Ingeniøren bestemmer *hvor lang tid bestillingen trenger*:** leveringstid, buffer, hvilken BOM som er koblet til, og farge. Faglæreren ser disse, men kan ikke endre dem.
- **BOM-ene ligger i en delt nettverksmappe** som både serveren og ingeniørens PC når. Én app og én database; ingeniøren åpner BOM-ene i Excel fra en tilordnet stasjon. At bare ingeniøren skal se BOM-ene, løses med roller i appen, ikke med hvor filene ligger. (Valgt fremfor *hybrid* – to apper og synkronisering – og fremfor opplasting/nedlasting i nettleseren.)
  **Mappen blir et eget unix-område på `kunde.it.ntnu.no`** (valgt 27. oktober 2026, etter forslag fra IT): VPS-en monterer det med NFS (tilgang etter IP, automount), ingeniørene bruker det skjulte sharet `\\kunde.it.ntnu.no\ies-arshjul$`, og tilgangen styres med en gruppe i BAS. Valgt fremfor CIFS-montering av `felles.ansatt.ntnu.no` med tjenestekonto, fordi passordet til tjenestekontoen måtte byttes hvert år – glemmes det, når ikke VPS-en BOM-ene lenger. Stien har heller ingen æøå, så «Åpne i Excel» virker for BOM-er med vanlige filnavn. Databasen blir liggende på VPS-ens egen disk – SQLite og NFS passer ikke sammen.

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
- [x] **Uke- og fristlogikken på ett sted** (`deadlines.py`). Serveren regner ut alle datoer; nettleseren plasserer dem bare i hjulet.
- [x] Hjulet viser ett skoleår om gangen og starter i august. Uke 53 og årsskiftet regnes med ekte datoer. «Neste 8 uker» og «Neste frist» ser på tvers av skoleår, så juni-frister for høsten kommer med.
- [x] Stengte perioder lagres i databasen og endres nederst på siden.

## Fase 1 – Bestillingsstatus ✓

- [x] Én rad per leverandør, hentet fra Leverandør-kolonnen i BOM-en. Linjer uten leverandør samles i «Ukjent leverandør». Linjer der alt er på lager teller ikke.
- [x] Status: Ikke bestilt → Bestilt → Delvis mottatt / Mottatt. Gjennomføringen får den «dårligste» statusen («2 av 3 bestilt»). Innlevert og telt kommer i fase 3.
- [x] Når du trykker «Registrer bestilt», lagres dato, ordrenummer, antall sett og en **frosset kopi** av linjene (`orders.py`, tabellen `orders`). «Hvem» lagres når innlogging kommer i fase 4.
- [x] **«Trenger mer enn det som er bestilt»**: den frosne kopien sammenlignes med det BOM-en og antall sett krever nå. Du ser hva som mangler per linje, og registrerer en tilleggsbestilling.
- [x] I hjulet, «neste 8 uker» og toppfeltet: grønn ring = bestilt, gul = frist snart eller må bestille mer, **rød = fristen er passert og ikke bestilt**. «Neste frist» hopper over det som er bestilt. Prosjekter som allerede har startet uten registrert bestilling markeres ikke.
- [x] Bestillingsliste for én leverandør.
- [x] **Varemottak** (migrering 5): «Registrer mottak …» legger det som kom til «På lager» i BOM-en (helt eller per linje), og setter status til Mottatt eller Delvis mottatt. Legges til det cellen har nå, så endringer i Excel imens ikke forsvinner. «Angre mottak» trekker det fra igjen (aldri under 0). Sikkerhetskopi og logg som ved telling. BOM-en viser dermed alltid det som står på hylla, og «Å bestille» i Excel blir riktig.
- [x] «Trenger mer / bestilt for mye» sjekkes bare før prosjektet starter og før det er telt – etterpå endres BOM-en av tellinger og neste års innkjøp.
- [x] Prosjekter uten BOM markeres som bestilt med en knapp (migrering 15), så fristen ikke står som passert.

**En BOM-linje kjennes igjen på leverandør + varenummer** (eller leverandør + beskrivelse uten varenummer), fordi varenumre bare er unike hos én leverandør – RS og Farnell kan ha samme nummer på hver sin vare. Bytter en linje leverandør, regnes den som en ny linje. (Migrering 6 gjorde om lagrede bestillinger; tellinger fra testingen ble fjernet.)

**Merk:** leverandørnavn må skrives likt i alle BOM-er («RS» og «RS-Online» er to leverandører). Det får betydning når bestillinger skal samles på tvers av prosjekter.

## Fase 2 – Skoleår og historikk ✓

- [x] **«Kopier til neste skoleår»**: velg hvilke prosjekter som skal være med. Uker, studenter, gruppestørrelse, ekstra sett, BOM, leveringstid og buffer kopieres. Bestillinger kopieres ikke. Prosjekter som allerede finnes i neste år hoppes over.
- [x] **Påminnelse om våren** (`deadlines.setup_warning`). Når prosjekter fra i år mangler i neste skoleår, og den første fristen deres er mindre enn 12 uker unna, vises et varsel øverst med en knapp for å sette opp året. Eksempel: Arduino starter kit i uke 34 har frist i **uke 25, i juni** – varselet kommer i starten av april.
- [x] **Historikk**: hva som er registrert som bestilt (eks. mva.), per prosjekt og skoleår, med pris per sett og sum per år. Regnes fra de frosne kopiene, så tallene står seg selv om BOM-en endres eller fjernes.
- [ ] Vise de enkelte linjene som ble bestilt et tidligere år (i dag: bla til året og åpne «Bestilling»).

- [x] **Sommeren og årsskiftet**: fra uke 26 åpner årshjulet på skoleåret som kommer (`deadlines.school_year_shown`). Hvert hjul viser nøyaktig sitt skoleårs uker; det som stikker inn fra året før eller etter (neste års frister i juni, fjorårets telling i august) tegnes svakt, med statusring på fristene.
- [x] **«Kopier til neste skoleår» er en sjekkliste**: uker og antall studenter kan rettes per prosjekt før kopiering (med «Bruk emnets N» når emnet har et annet tall), datoen for startuka neste år vises, og om BOM-en er telt og skrevet etter i år – ellers blir «På lager» og neste års bestilling feil. Bare det som er endret sendes (`changes` i `/api/school-years/<år>/copy`).
- [x] **BOM, leveringstid og buffer per gjennomføring** (migrering 14), ikke per prosjekt. Endres de, følger senere skoleår med hvis de hadde samme verdi (`db.FORWARD_FIELDS`) – tidligere år endres aldri, så en ny BOM-versjon ikke endrer historikken. Kolonnene i `prosjekt` står ubrukt.
- [x] **Påsken flytter seg** (migrering 13): en stengt periode kan «følge påsken» – uka med påskedag, regnet ut for hvert år (`deadlines.easter_week`). Den gamle «Påske» i uke 12 ble gjort om.
- [x] **Semester-valget i skjemaet er fjernet**: semesteret følger startuka, og skjemaet viser de faktiske datoene («Vår 2027: 18. jan – 13. juni 2027»).

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
- [x] **Administratorpanel** (`/admin`, `admin_panel.py`, `admin.js`) i stedet for en adminbryter: på hovedsiden er administratoren alltid en vanlig ingeniør; panelet ser og retter alt. Faner: Oversikt (det som bør ryddes), Prosjekter (rett/gjenopprett/slett, med konsekvens først), Emner, Brukere, Bestillinger og tellinger (fjern feilregistreringer; BOM-skrivinger angres først), Endringslogg (søk), Opprydding (kvitter alle, slett skoleår), System (database, BOM-mappe, sikkerhetskopi). Alle `admin_panel.*`-endepunkter krever administrator på serveren, og rettinger logges med rolle «admin».
- [x] Første administrator: lokalt på siden «Velkommen til årshjulet» når det ikke finnes brukere; på serveren bare med `python manage.py ny-bruker <brukernavn> "<Navn>" ingenior --admin`, så ingen andre rekker det først. `manage.py` er også reserve (`passord`, `admin`, `aktiver`, `deaktiver`, `liste`).
- [x] Passord lagres som hash (scrypt), minst 10 tegn. Låst i 15 minutter etter 5 feil; i tillegg en grense per IP-adresse. Samme melding for feil brukernavn og feil passord.
- [x] **Innlogging er alltid på – lokalt også.** Ingen bruker uten navn, så det som testes lokalt er det som kjører på serveren, og loggen viser alltid hvem. Lokalt lagres nøkkelen i datamappen, og du forblir innlogget i 30 dager (serveren: 12 timer).
- [x] Økten varer 12 timer, og slutter å gjelde når passordet endres eller brukeren deaktiveres.
- [x] **Rollene sjekkes på serveren**, ikke bare i nettleseren. En faglærer når bare de endepunktene som er listet som tillatt (`LECTURER_ALLOWED`) – alt annet er stengt som standard.

**Faglærerpanelet**
- [x] Faglæreren ser bare prosjektene i sine egne emner, og ikke BOM, bestillinger, telling eller kostnader (de sendes ikke engang til nettleseren).
- [x] Faglæreren legger til, endrer og fjerner **sine egne** prosjekter: navn, type, uker, studenter/grupper, notat. Leveringstid, buffer, BOM og farge er ingeniørens.
- [x] ~~Faglærer per prosjekt~~ → erstattet av emner (under).

**Emner og flere ingeniører** (migrering 8)
- [x] Studieprogram (bachelor/master) → **emne** (én faglærer, én ansvarlig ingeniør) → prosjekt → gjennomføring per skoleår. En faglærer kan ha emner med ulike ingeniører.
- [x] **Alle ser bare det de er involvert i** – filtrert på serveren. Ingeniøren: egne emner, pluss prosjekter uten emne/ingeniør (så ingenting blir usynlig). Faglæreren: egne emner. Administratoren: alt. (En instituttoversikt – kollisjoner, felles lager – er parkert; se under.)
- [x] **Eierskap sjekkes på serveren for alt som endrer en gjennomføring** (`can_handle` i `_ownership`): ingeniører kan ikke bestille, telle, endre eller slette i andres emner; faglærere bare i egne emner.
- [x] Faglærerens endringer varsles til emnets ingeniør. Bytter emnet ingeniør, overtar den nye varslene som ikke er kvittert.
- [x] Alle ingeniører kan endre emner (seksjonen «Emner»). Emner deaktiveres, ikke slettes.
- [x] **Nye emner godkjennes av administratoren** (migrering 18): alle andre *foreslår* emner (`approved = 0`, `proposed_by`). Et forslag ses og brukes bare av den som foreslo, faglæreren og ingeniøren på emnet (`app.course_visible` – også i `_course`, så ingen kan legge prosjekter i andres forslag), så det ikke blir rot i nedtrekkslistene. Administratoren godkjenner eller avviser i panelet (`/api/admin/courses/<id>/approve`, `DELETE /api/admin/courses/<id>`); avvist emne slettes og prosjektene står uten emne. Den som foreslo får varsel – også faglærere, som nå får varsler adressert til dem.
- [x] **Faglæreren lager sine egne emner og velger ingeniør** (migrering 10). Faglæreren kan ikke bytte faglærer eller endre andres emner. Ingeniøren som velges – og den som tas av – får et varsel med «OK, sett» (ikke når du velger deg selv). Ingeniøren får emnet med en gang (godkjenningen av nye emner, under, stopper ikke det).
- [x] «Kopier til neste skoleår» og vårpåminnelsen gjelder egne emner.
- [x] **Antall sett regnes ut av studenter og grupper** (migrering 11): antall studenter ÷ studenter per gruppe, rundet opp, + ekstra sett (`db.set_count` – eneste stedet det regnes). Antall sett lagres fortsatt, så bestilling, telling og historikk er uendret, men kan ikke settes direkte. Eksisterende gjennomføringer fikk studenter = sett og grupper på 1. Endrer antall sett seg, logges og varsles det sammen med feltet som ble endret.
- [x] **Visninger og deling** (migrering 19): `/hjul` og `/uker` viser bare hjulet eller «Neste 8 uker» (samme side med `data-view`; JS tegner bare den delen). «Del med studenter» på `/hjul` gir en personlig lenke `/hjul/<nøkkel>` uten innlogging (`users.share_token`, som kalenderen). `/hjul/<nøkkel>/data` sender bare navn, type, farge og bruksuker for brukerens egne prosjekter – ingen frister, bestillinger, BOM, emner eller personer. Ny lenke gjør den gamle ugyldig; deaktiverte brukere får 404.
- [x] **Kalender i Outlook** (migrering 17, `calendar_feed.py`): «Kalender» gir en personlig lenke (`/calendar/<nøkkel>.ics`) å abonnere på. Ingeniører får forberedelse, bestillingsfrist, bruksperiode og telling; faglærere bruksperioden. Hele dager, faste UID-er så endringer oppdaterer hendelsene, inneværende og neste skoleår. «Lag ny lenke» gjør den gamle ugyldig; deaktiverte brukere får 404. Lokalt kan bare Outlook på samme PC abonnere. På serveren virker det i klassisk Outlook på PC-er på NTNU-nettet/VPN, men ikke i Outlook på nett, mobil eller den nye Outlook – de henter via Microsofts servere, som ikke når VPS-en (se «Parkert / senere»).
- [x] **Koden er på engelsk** (migrering 16): navn, kommentarer, API-felt og -adresser, databasens tabeller, kolonner og koder, nøklene i lagrede data, testnavn og commit-meldinger. Ordliste i CLAUDE.md. Tekst brukerne ser er fortsatt norsk – også mappene i datamappen, Excel-malene, `innstillinger.env` og kommandolinjen i `manage.py`. Migreringen kjører med fremmednøkler av (brukere og prosjekter bygges om) og tar sikkerhetskopi først. «BOM sist sett»-filen gjøres om én gang (`bom-last-seen.json`).
- [x] **Sikkerhetskopi før oppgradering**: før databasen migreres, kopieres den til `Sikkerhetskopi-db/` (samme sted og navn som «Ta sikkerhetskopi nå» i administratorpanelet).
- [x] **Fire prosjekttyper** (`deadlines.TYPES`): semestersett, prosjekt, hackathon og lab. Typen gir navnene i hjulet og telleuka (sett og lab: siste bruksuke; prosjekt og hackathon: uka etter). Gamle «Hackathon / lab» ble Hackathon – lab-ene endres i tabellen. Ingen migrering: typen er fritekst i databasen og sjekkes i API-et.
- [x] **Antall studenter hører til emnet** (migrering 12) og er standard når et nytt prosjekt legges i emnet. Prosjektet har sitt eget tall og kan avvike; å endre emnet endrer ikke prosjekter som finnes. Avviker prosjektet, vises emnets tall under.
- Lager og BOM er fortsatt per prosjekt (felles lager på tvers av emner er parkert).
- [x] Mens faglæreren endrer, vises konsekvensen ved fristen: «Fristen er om 1 uke – ingeniøren varsles».
- [x] Fjerning er **myk**: prosjektet skjules, og ingeniøren kan gjenopprette eller bekrefte sletting.
- [x] Endre navn og notat i tabellen («Endre» i navnecellen; Enter lagrer, Escape avbryter).

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
- [x] HTTPS og tilgang bare fra universitetsnettet/VPN – Caddy med NTNUs sertifikat; VPS-en har bare en intern adresse (`deploy/VPS-oppsett.md`).

## Flytte til VPS – sjekkliste

Gjort i oktober 2026 – slik det faktisk ble satt opp (Caddy, sertifikat, brannmur, brukere) står i `deploy/VPS-oppsett.md`.

1. Installer Python 3.10 eller nyere på VPS-en.
2. Hent koden (`git clone`), og lag et Python-miljø: `python -m venv .venv` og `pip install -r requirements.txt`.
3. Lag en datamappe, for eksempel `/srv/arshjul-data`, og kopier over `arshjul.db`. Stopp årshjulet lokalt først, så databasen er ferdig lagret.
4. Monter BOM-mappen – NFS-området fra `kunde.it.ntnu.no` med automount (autofs), for eksempel på `/mnt/arshjul-bom`. Tjenestebrukeren må være med i BAS-gruppens GID for å kunne skrive.
5. Sett `ARSHJUL_MODUS=server`, `ARSHJUL_DATA`, `ARSHJUL_BOM=/mnt/arshjul-bom` og `ARSHJUL_SECRET_KEY` (lag med `python -c "import secrets; print(secrets.token_hex(32))"`) – i `innstillinger.env` (lesbar bare for tjenestebrukeren) eller i systemd-tjenesten. La `ARSHJUL_HOST=127.0.0.1`, så bare Caddy/nginx når appen.
6. Lag administratoren: `python manage.py ny-bruker <brukernavn> "<Navn>" ingenior --admin`. Resten av brukerne lages på nettsiden under «Brukere». (Tar du med `arshjul.db` fra PC-en, følger brukerne med.)
7. Kjør `python app.py` som en systemd-tjeneste, med Caddy foran for HTTPS, f.eks. `arshjul.ntnu.no { reverse_proxy 127.0.0.1:5057 }`.
8. Sett opp backup av datamappen. **Gjort:** en timer kjører `manage.py sikkerhetskopi` hver natt og beholder de 30 nyeste daglige kopiene i `Sikkerhetskopi-db/` (`deploy/VPS-oppsett.md`, punkt 8). En kopi utenfor VPS-en venter på IT.

## Parkert / senere


- **Instituttoversikt**: se alle emner samtidig for å finne kollisjoner (travle uker, delte rom og utstyr) og felles lager. Bevisst tatt bort for nå – i et stort institutt blir det rot. Administratoren ser allerede alt; en egen visning kan komme senere.

- **Tilgang utenfra** for kalenderen (Outlook på nett, mobil og den nye Outlook) og den delte lenken til hjulet (studenter uten VPN). VPS-en har bare en intern adresse; IT kan gi en ny VM i et mer åpent nett, men da må oppsettet flyttes dit (tilbudt 27. oktober 2026, takket nei til foreløpig – ikke en del av kjernen). Gjøres det: Caddy svarer utenfra bare på `/calendar/…`, `/hjul/<nøkkel>` og det de trenger av `/static/`, resten bare fra NTNU-nettet/VPN; be om at navnet `ies-arshjul.it.ntnu.no` flyttes med, så delte lenker virker videre; og NFS-eksporten må flyttes til den nye IP-en.
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

**Skrive «På lager» mens BOM-en er åpen i Excel på nettverksmappen – testet 23. oktober 2026** (kopi på hjemmeområdet, `bom.write_stock`):

| Hva | Resultat |
|---|---|
| Åpnet vanlig i Excel | nekter: «… er åpen i Excel» (Excel lager `~$`-filen med en gang) |
| Samme, men uten å se etter `~$`-filen | nettverksmappen nekter å lagre: «… er låst». Sikkerhetskopien er allerede tatt – ufarlig |
| Åpnet i **beskyttet visning** (slik `ms-excel:`-lenken åpner den) | ingen `~$`-fil, så årshjulet **skriver**. Ufarlig: «Aktiver redigering» leser filen på nytt (viste det nye tallet), og deretter nekter årshjulet |

**Gjenstår å teste:**
Venter på NFS-området fra IT. Med kopier av BOM-ene, når det er klart:
- At låsen fra Excel på en PC (SMB) stopper skriving fra VPS-en (NFS). `~$`-filen virker uansett protokoll; om lagringen i tillegg håndhever låsen på tvers av NFS og SMB, avhenger av lagringssystemet.
- Rettigheter begge veier: VPS-en skriver i BOM-er som ingeniøren har laget, og ingeniøren redigerer BOM-er og sikkerhetskopier som VPS-en har laget.
- Filnavn med æøå og tankestrek ser like ut over NFS og SMB.
- «Åpne i Excel» (`ms-excel:`-lenken) med `$` i sharenavnet.

**«Åpne i Excel» fra serveren – testet 23. oktober 2026** (nettleser og Excel på Windows, kopi av en BOM på hjemmeområdet):

| Lenke | Resultat |
|---|---|
| `ms-excel:ofe\|u\|file://server/mappe/test-bom.xlsx` (bare ASCII, mellomrom som `%20`) | åpnes, i beskyttet visning |
| samme med æøå eller tankestrek i stien (nettleseren gjør dem om til `%C3%B8` o.l.) | Excel finner ikke filen |
| æøå kodet som Windows-1252 (`%F8`) | Excel finner ikke filen |

Derfor, på serveren med `ARSHJUL_BOM_WINDOWS` satt: **«Kopier sti»** ved hver BOM (lim inn i Excel under Fil → Åpne, eller i Utforsker), og **«Åpne i Excel»** bare når hele stien er uten æøå og spesialtegn. «BOM-mappen» kopierer stien til mappen. Filene åpnes i beskyttet visning; legg fellesmappen inn som klarert plassering i Excel (Fil → Alternativer → Klareringssenter) for å slippe det. BOM-er fra «Lag BOM-mal» heter «… – BOM.xlsx» med tankestrek, så de får bare «Kopier sti».

I tillegg viser Office en advarsel **hver gang** filen åpnes fra `ms-excel:`-lenken («This content presents a potential security issue. Do you trust this content?», med `file:///\\…`-adressen). Lokalt kommer den ikke, for der åpner appen filen direkte. Kanskje forsvinner den hvis fellesmappen legges under «Lokalt intranett» i Internett-alternativer – ikke testet. Å slå av advarselen i registeret anbefales ikke.

**Hvorfor det ikke kan være som lokalt, selv med nettverksdisk:** Nettverksdisken gjør at serveren og PC-en ser *de samme filene*. Den avgjør ikke *hvem som starter Excel*. Lokalt kjører appen på PC-en og ber Windows åpne filen. På serveren går trykket til VPS-en, som ikke kan starte noe på PC-en – den kan bare sende en nettside tilbake. Og nettlesere nekter med vilje å åpne filer og programmer på PC-en (ellers kunne enhver nettside gjøre det), så en vanlig lenke til `\\felles…\BOM\fil.xlsx` blokkeres. Unntakene er lenker Office har godkjent (`ms-excel:`, med advarslene over) og et eget lite program på PC-en. SharePoint/Teams har en «Åpne i Excel» som virker rett, fordi filene ligger der – men vi valgte fellesmappen (se «Beslutninger»).

**Ikke bestemt ennå – hvordan åpne en BOM fra serveren:**

1. **Bare «Kopier sti»** (fjern `ms-excel:`-lenken). «Kopier sti», så Windows+R, Ctrl+V, Enter – åpner rett i Excel, uten advarsel og uten beskyttet visning. Enklest; ingenting å installere.
2. **`ms-excel:`-lenken, og BOM-filnavn uten æøå og tankestrek** (endre «Lag BOM-mal», gi nytt navn til de som finnes). Ett klikk, men advarselen og beskyttet visning hver gang.
3. **En liten hjelper på hver ingeniør-PC**: en egen lenketype (`arshjul-bom:`) som åpner filen direkte i Excel, som lokalt. Æøå går fint, ingen Office-advarsel (nettleseren spør første gang – «tillat alltid»). Installeres én gang per PC med et skript, uten administrator. Åpner bare `.xlsx` i BOM-mappen, så andre nettsider ikke kan misbruke den. Faglærerne trenger den ikke. «Kopier sti» blir stående som reserve.

Anbefaling: 3 hvis ett klikk er viktig (få ingeniører, samme opplevelse som før), ellers 1. Inntil videre: «Kopier sti» på alle, `ms-excel:`-lenken når stien er uten æøå.

**Bestillingslister på serveren:** «Bestillingsliste» lagrer HTML-filen i `Bestillingslister/` på VPS-en, der ingeniøren ikke kommer til. Bør lastes ned i nettleseren i stedet (eller lagres på fellesmappen).

## Åpne spørsmål (til IT)

- ~~Kan vi få en egen fellesmappe for tjenesten?~~ Ja – IT foreslo to alternativer (27. oktober 2026), og vi valgte unix-området (se «Beslutninger»). Svaret under ble sendt 27. oktober; venter på svar om rettigheter, låsing og tegnsett.
- Hva krever IT for en VPS (operativsystem, tilgang, backup, sikkerhetsgjennomgang)?
- ~~Tas det backup av BOM-området?~~ Ja, IT tar backup av lagringsområdene. Derfor skal også de nattlige databasekopiene kopieres dit.
- ~~Trengs det åpninger i brannmuren for at VPS-en skal nå lagringsområdet?~~ Nei.
- ~~Kan `/calendar/` nås utenfra?~~ Ikke fra VPS-en slik den står – det krever en ny VM i et mer åpent nett. Lagt til side (se «Parkert / senere»).
- Tas det backup av selve VM-en? Spurt i det andre svaret under.

**Svar til IT om lagringsområdet** (sendt 27. oktober 2026):

> Hei, og takk for grundig svar!
>
> Vi vil gjerne ha alternativ 2: et eget unix-område på kunde.it.ntnu.no, NFS-eksportert til ies-arshjul etter IP og med automount der, pluss det skjulte CIFS-sharet `\\kunde.it.ntnu.no\ies-arshjul$` for oss på Windows. Alle i gruppen kan ha samme lese- og skrivetilgang til hele området.
>
> Noen spørsmål og ønsker:
>
> 1. **Rettigheter:** Tjenesten på VPS-en skriver i Excel-filer som vi lager fra Windows, og vi må kunne redigere filer som tjenesten lager. Hvilken GID får BAS-gruppen, slik at vi kan legge tjenestebrukeren på VPS-en i den? Kan området settes opp slik at nye filer og mapper blir gruppeskrivbare (setgid på mappene, umask/ACL), uansett om de lages over NFS eller SMB? Hvilken security style får volumet?
> 2. **Låsing:** Respekteres en fil som er åpen i Excel over SMB (share-lås) også når noen skriver til den over NFS? Tjenesten sjekker selv Excels `~$`-fil, men vi vil gjerne vite om lagringen i tillegg hindrer at filen skrives fra VPS-en.
> 3. **Tegnsett:** Bruker volumet UTF-8, så filnavn med æøå vises likt over NFS og SMB?
> 4. **Backup:** Tas det backup eller snapshots av området, og kan vi hente tilbake eldre versjoner selv (f.eks. via «Tidligere versjoner» i Windows)?
> 5. **Størrelse:** Området skal bare inneholde Excel-filer og noen sikkerhetskopier, så noen få GB holder godt.
>
> Gruppen bør i første omgang bestå av: [navn/brukernavn].
>
> Mvh

**Svar til IT om tilgang utenfra og backup** (27. oktober 2026, etter IT-s oppfølging om punkt 3–5):

> Hei igjen, og takk!
>
> Meldingen min om lagringsområdet krysset nok din. Svarene på 3 og 4 passer fint. Siden det tas backup av lagringsområdet, kopierer vi også de nattlige sikkerhetskopiene av databasen dit.
>
> **Punkt 5:** Vi lar det vente. Tilgang utenfra var bare et ønske, ikke noe tjenesten trenger nå, så vi blir på ies-arshjul01. NFS-området kan altså eksporteres dit. Trenger vi å nå den utenfra senere, tar vi kontakt.
>
> Tas det forresten backup av selve VM-en også, eller bare av lagringsområdene?
>
> Mvh
