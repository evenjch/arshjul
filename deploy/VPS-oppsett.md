# Årshjulet på VPS-en – slik er det satt opp

Oppsettet slik det ble gjort i oktober 2026. Kommandoene kjøres som root på VPS-en.

| | |
|---|---|
| Maskin | `ies-arshjul01`, adresse `ies-arshjul.it.ntnu.no` (intern 10.-adresse – nås bare fra NTNU-nettet/VPN) |
| System | Ubuntu 26.04, Python 3.14 |
| Kode | `/opt/arshjul` (git-klone), Python-miljø i `/opt/arshjul/arshjul/.venv` |
| Data | `/srv/arshjul-data` (`arshjul.db`, `Bestillingslister/`, `.cache/`, `Sikkerhetskopi-db/`), eid av `arshjul` |
| BOM-mappe | `/mnt/arshjul-bom` – foreløpig en tom lokal mappe, skal bli fellesmappen (se «Gjenstår») |
| Innstillinger | `/etc/arshjul.env` |
| Tjenester | `arshjul` (appen, waitress på `127.0.0.1:5057`), `caddy` (HTTPS foran), `arshjul-sertifikat.timer` |

```
nettleser ──https──▶ Caddy :443 (NTNU-sertifikatet) ──▶ waitress 127.0.0.1:5057 (brukeren arshjul)
```

## Daglig bruk

**Oppdatere til ny versjon**
```bash
cd /opt/arshjul && git pull
/opt/arshjul/arshjul/.venv/bin/pip install -r /opt/arshjul/arshjul/requirements.txt   # bare hvis requirements.txt er endret
systemctl restart arshjul
```
Databasen oppgraderes selv ved oppstart, med sikkerhetskopi i `/srv/arshjul-data/Sikkerhetskopi-db/`.

**Brukere** – vanligvis på nettsiden under «Brukere». Fra terminalen (første administrator, glemt passord, sperret ute):
```bash
arshjul-bruker liste
arshjul-bruker ny-bruker <brukernavn> "<Fullt navn>" ingenior --admin
arshjul-bruker passord <brukernavn>
arshjul-bruker aktiver <brukernavn>        # opphever sperre etter feil passord
```
Bruk **ikke** `python manage.py` direkte: uten innstillingene i `/etc/arshjul.env` går det mot en annen database.

**Feilsøking**
```bash
systemctl status arshjul caddy
journalctl -u arshjul -n 50 --no-pager      # appen
journalctl -u caddy -n 50 --no-pager        # HTTPS/proxy
curl -I http://127.0.0.1:5057/login         # svarer appen? (200)
curl -I --resolve ies-arshjul.it.ntnu.no:443:127.0.0.1 https://ies-arshjul.it.ntnu.no/login   # svarer Caddy? (200)
```
Etter en feil som stoppet tjenesten («Start request repeated too quickly»): rett feilen, så `systemctl reset-failed arshjul && systemctl restart arshjul`.

## Slik ble det satt opp

### 1. Pakker
NTNUs `pkgsync` avinstallerer pakker som ikke står i pakkelista – med purge, så konfigurasjonen i `/etc` forsvinner også (slik mistet vi `/etc/caddy` én gang). Lista er `/etc/pkgsync/required-packages-arshjul`:
```
python3.14-venv
caddy
git
```
Lista leses **ikke** av seg selv – etter hver endring kjøres `/local/admin/bin/do_pkgsync.sh` (ingen feilmeldinger = pakkene er godkjent). Først da er det trygt å installere.
(`curl`, `openssl`, `python3` og `keyutils` er med i NTNUs grunninstallasjon. `cifs-utils` kommer til med BOM-mappen.)

Se hva som er installert: `grep -h -A1 '^Commandline' /var/log/apt/history.log` og `apt-mark showmanual`.

### 2. Bruker, kode og data
```bash
useradd --system --home /opt/arshjul --shell /usr/sbin/nologin arshjul
git clone https://github.com/evenjch/arshjul.git /opt/arshjul
cd /opt/arshjul/arshjul && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
mkdir -p /srv/arshjul-data /mnt/arshjul-bom
chown -R arshjul: /srv/arshjul-data /mnt/arshjul-bom
install -m 755 /opt/arshjul/deploy/arshjul-bruker /usr/local/bin/
```
`/mnt/arshjul-bom` må finnes og eies av `arshjul`, ellers stopper appen med `PermissionError` ved oppstart.
`arshjul-bruker` ligger i `/usr/local/bin` fordi NTNUs PATH for root ikke har `/usr/local/sbin`.

### 3. Innstillinger – `/etc/arshjul.env` (chmod 600, eid av root)
```
ARSHJUL_MODUS=server
ARSHJUL_DATA=/srv/arshjul-data
ARSHJUL_BOM=/mnt/arshjul-bom
ARSHJUL_SECRET_KEY=<64 tegn fra: python3 -c "import secrets; print(secrets.token_hex(32))">
ARSHJUL_HOST=127.0.0.1
```
Byttes nøkkelen, blir alle logget ut.

### 4. Tjenesten – `/etc/systemd/system/arshjul.service`
```ini
[Unit]
Description=Årshjul for innkjøp
After=network-online.target remote-fs.target
RequiresMountsFor=/mnt/arshjul-bom

[Service]
User=arshjul
WorkingDirectory=/opt/arshjul/arshjul
EnvironmentFile=/etc/arshjul.env
ExecStart=/opt/arshjul/arshjul/.venv/bin/python app.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
```bash
systemctl daemon-reload && systemctl enable --now arshjul
```

### 5. Sertifikatet
NTNU legger sertifikatet på maskinen og fornyer det selv 30 dager før det går ut. Caddy merker ikke at filen endres, så et skript kopierer det til `/etc/caddy/certs/` (lesbart for Caddy) og laster Caddy på nytt når det er nytt. NTNUs sertifikatfil har mellomsertifikatene med (3 sertifikater i filen), så `SRC_CHAIN` er tom.

`/usr/local/sbin/arshjul-sertifikat` (chmod 755):
```sh
#!/bin/sh
# Kopierer NTNUs sertifikat til Caddy når det er nytt, og laster det inn.
set -eu
SRC_CRT=<NTNUs sertifikatfil>
SRC_KEY=<NTNUs nøkkelfil>
SRC_CHAIN=                            # mellomsertifikatene, bare hvis de ligger i en egen fil
DST=/etc/caddy/certs

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
awk 1 "$SRC_CRT" ${SRC_CHAIN:+"$SRC_CHAIN"} > "$TMP"    # sertifikat + mellomsertifikater i én fil

cmp -s "$TMP" "$DST/arshjul.crt" && cmp -s "$SRC_KEY" "$DST/arshjul.key" && exit 0

# Ikke bytt halvveis: sertifikat og nøkkel må høre sammen (ellers prøver vi igjen i morgen)
[ "$(openssl x509 -noout -pubkey -in "$TMP")" = "$(openssl pkey -pubout -in "$SRC_KEY")" ] \
    || { echo "Sertifikat og nøkkel passer ikke sammen ennå"; exit 1; }

install -o root -g caddy -m 640 "$TMP" "$DST/arshjul.crt"
install -o root -g caddy -m 640 "$SRC_KEY" "$DST/arshjul.key"
systemctl reload caddy
echo "Nytt sertifikat lastet inn: $(openssl x509 -noout -enddate -in "$DST/arshjul.crt")"
```

Kjøres daglig av en timer. `/etc/systemd/system/arshjul-sertifikat.service`:
```ini
[Unit]
Description=Kopier NTNU-sertifikatet til Caddy

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/arshjul-sertifikat
```
`/etc/systemd/system/arshjul-sertifikat.timer`:
```ini
[Unit]
Description=Se etter nytt sertifikat hver dag

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
```
```bash
mkdir -p /etc/caddy/certs
/usr/local/sbin/arshjul-sertifikat               # første gang
systemctl daemon-reload && systemctl enable --now arshjul-sertifikat.timer
```
Sjekk: `systemctl list-timers arshjul-sertifikat`, `journalctl -u arshjul-sertifikat`, og utløpsdatoen Caddy faktisk bruker:
`echo | openssl s_client -connect ies-arshjul.it.ntnu.no:443 2>/dev/null | openssl x509 -noout -enddate`.
Etter NTNUs første fornying bør datoen ha flyttet seg.

### 6. Caddy – `/etc/caddy/Caddyfile`
```
ies-arshjul.it.ntnu.no {
	tls /etc/caddy/certs/arshjul.crt /etc/caddy/certs/arshjul.key
	header Strict-Transport-Security "max-age=31536000"
	reverse_proxy 127.0.0.1:5057
}
```
```bash
caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy
```
Caddy sender `X-Forwarded-For/-Proto/-Host`, som appen stoler på i servermodus (riktig klientadresse og `Secure`-cookie). Det er ingen IP-regel i Caddy: maskinen har bare en intern adresse, så nettverket avgrenser tilgangen. (En regel med `remote_ip 129.241.0.0/16` stengte ute alle, også fra NTNU – brukerne kommer fra interne adresser.)

### 7. Brannmur
Etter NTNUs guide: to filer i `/etc/local/firewall.d/`, `ipv4-arshjul-webserver.conf` og `ipv6-arshjul-webserver.conf`, med:
```
-I INPUT -p tcp -m tcp --dport 80 -j ACCEPT
-I INPUT -p tcp -m tcp --dport 443 -j ACCEPT
```
Filene leses **ikke** av seg selv – kjør NTNUs skript for å oppdatere brannmuren (se guiden). Sjekk at reglene er aktive: `iptables -S INPUT | grep -E 'dport (80|443)'`. Port 5057 skal ikke åpnes.

## Gjenstår

- **BOM-fellesmappen** – venter på IT (egen fellesmappe, tjenestekonto, TCP 445 fra VPS-en). Plan: `cifs-utils` i pakkelista, innlogging i `/etc/arshjul-bom.cred` (chmod 600), og i `/etc/fstab`:
  ```
  //felles.ansatt.ntnu.no/<sti>  /mnt/arshjul-bom  cifs  credentials=/etc/arshjul-bom.cred,uid=arshjul,gid=arshjul,file_mode=0660,dir_mode=0770,iocharset=utf8,vers=3.0,_netdev,nofail  0  0
  ```
  Sjekk at `/mnt/arshjul-bom` er tom før montering (filer der blir skjult). NTNU bruker Heimdal Kerberos – velger IT Kerberos, følg deres oppskrift. Testene som gjenstår står i `VEIKART.md`.
- **Kalenderen i Outlook** – Outlook på nett/mobil henter feeden fra Microsofts servere, som ikke når VPS-en. Spurt IT om `/calendar/` kan åpnes utenfra.
- **Backup** av `/srv/arshjul-data`.
