# Matrix Phase 0 — Operator-Checkliste (VM + Apache)

Die App-seitigen Artefakte (`docker-compose.yml`-Service `synapse`,
`synapse/homeserver.yaml.example`, `.env`-Keys, `settings.py`) sind im Repo.
Diese Schritte kann nur der Operator auf der VM bzw. dem Apache-Proxy-Host
ausführen — Netz, DNS, Firewall und Apache-Vhost liegen außerhalb dessen, was
aus dem Code heraus setzbar ist.

> Alle Kommandos sind einzeilig und direkt ins VM-Terminal einfügbar (kein `cd`,
> keine `\`-Zeilenfortsetzungen). Repo liegt auf der VM unter `/opt/fi-link`.

## Reihenfolge

### 1. Ist-Collation der App-DB prüfen (informativ, A3)
```
docker compose exec db psql -U fichtelink -d fichtelink -c "SELECT datname, datcollate, datctype, pg_encoding_to_char(encoding) AS encoding FROM pg_database WHERE datname='fichtelink';"
```
Erwartung auf `postgres:16-alpine`: vermutlich `C` / `UTF8`. Egal welches
Ergebnis — die `synapse`-DB bekommt unten ihre eigene Collation und ist davon
unabhängig.

### 2. Synapse-DB + User in der bestehenden Instanz anlegen (einmalig)
Passwort vorher festlegen und identisch in `.env` als `SYNAPSE_DB_PASSWORD`
eintragen. `TEMPLATE template0` ist Pflicht, um von der Cluster-Default-Collation
auf `C` abzuweichen.
```
docker compose exec db psql -U fichtelink -d postgres -c "CREATE ROLE synapse WITH LOGIN PASSWORD 'DEIN_SYNAPSE_DB_PASSWORT';"
docker compose exec db psql -U fichtelink -d postgres -c "CREATE DATABASE synapse WITH OWNER synapse ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0;"
```
Prüfen:
```
docker compose exec db psql -U fichtelink -d postgres -c "SELECT datname, datcollate, datctype FROM pg_database WHERE datname='synapse';"
```

### 3. `.env` füllen
- `MATRIX_SERVER_NAME=fichtelink.caos.cloud`
- `MATRIX_ADMIN_SHARED_SECRET=` → `python -c 'import secrets; print(secrets.token_urlsafe(48))'`
- `SYNAPSE_DB_PASSWORD=` → das aus Schritt 2
- `MATRIX_ENABLED=False` **vorerst lassen** (erst nach grünem Federation-Test auf True).

### 4. homeserver.yaml erzeugen + Signing-Key generieren
Vorlage kopieren und `__PLACEHOLDER__` ersetzen (DB-Passwort + Shared-Secret wie
in `.env`; `__MACAROON_SECRET_KEY__` und `__FORM_SECRET__` je mit einem eigenen
`token_urlsafe(48)`):
```
cp synapse/homeserver.yaml.example synapse/homeserver.yaml
```
Nur den fehlenden Signing-Key zur bestehenden Config erzeugen. **Nicht**
`docker compose run synapse generate` — dieser Modus baut eine komplette Config
aus `SYNAPSE_*`-Env-Variablen und überschreibt die handgepflegte yaml. Stattdessen
gezielt `--generate-keys` mit überschriebenem Entrypoint:
```
docker compose run --rm --entrypoint python synapse -m synapse.app.homeserver --config-path /data/homeserver.yaml --generate-keys
```
Das liest die vorhandene Config und legt nur den fehlenden Key am Pfad aus
`signing_key_path` an, ohne die yaml anzufassen.

Besitzrechte setzen: `--generate-keys` läuft als root, Synapse läuft im
`run`-Modus aber als unprivilegierter User `991:991` und kann eine root-eigene
0600-Datei nicht lesen (`Permission denied: …signing.key` → Crash-Schleife,
Port 8008 wird nie published). Daher das gesamte Daten-Verzeichnis übereignen:
```
sudo chown -R 991:991 synapse
```
Prüfen, dass der Key vorhanden ist:
```
ls -l synapse/fichtelink.caos.cloud.signing.key
```
Der Signing-Key darf NICHT neu erzeugt werden, sobald der Server einmal
föderiert hat (das ändert die Server-Identität und bricht Föderation + Räume).

### 5. Synapse starten
```
docker compose up -d --build synapse
```
Interner Health-Check (aus dem App-Container, nicht öffentlich):
```
docker compose exec web python manage.py matrix_healthcheck
```
Erwartung: Liste der unterstützten `/_matrix/client/versions`.

### 6. Apache-Vhost erweitern (Proxy-Host)
Im bestehenden `fichtelink.caos.cloud`-Vhost (TLS terminiert dort) ergänzen.
`VM_HOST:8008` = die VM-Adresse und der gemappte `SYNAPSE_PORT`, erreichbar nur
aus dem Proxy-Netz.

- Matrix-Client- + Föderationsverkehr an Synapse:
  - `ProxyPass /_matrix/        http://VM_HOST:8008/_matrix/`
  - `ProxyPass /_synapse/client/ http://VM_HOST:8008/_synapse/client/`
  - jeweils mit passendem `ProxyPassReverse`.
- `/_synapse/admin/` **NICHT** proxyen (bleibt VM-intern).
- `ProxyPreserveHost On` muss gesetzt sein (gilt schon für die App; Synapse
  braucht es genauso, sonst schlägt die Origin-/Föderationsprüfung still fehl).
- `.well-known`-Delegation auf `:443` (statische JSON-Antworten, am einfachsten
  als Alias-Dateien oder `<Location>` mit fester Antwort):
  - `GET /.well-known/matrix/server` → `{"m.server": "fichtelink.caos.cloud:443"}`
  - `GET /.well-known/matrix/client` → `{"m.homeserver": {"base_url": "https://fichtelink.caos.cloud"}}`
    (mit `Access-Control-Allow-Origin: *`)

Wichtig: `/_matrix/` muss **vor** dem `ProxyPass /` der App stehen, sonst fängt
die App die Matrix-Pfade ab.

### 7. Föderation testen (BEVOR echte Eltern starten — A2)
- https://federationtester.matrix.org/ gegen die Bare-Domain `fichtelink.caos.cloud`.
- Bekannte Stolperfalle: trotz korrekter `.well-known` auf 443 kann Föderation
  scheitern, bis `/_matrix/federation/*` sauber durchgereicht wird. Nicht
  annehmen, dass es läuft — grünes Testergebnis abwarten.

### 8. Scharfschalten
Erst nach grünem Federation-Test:
```
docker compose up -d --force-recreate web worker
```
(nach `MATRIX_ENABLED=True` in `.env` — `restart` liest `.env` NICHT neu, daher
`--force-recreate`.)

Dann Service-Account anlegen (Phase 1):
```
docker compose exec web python manage.py matrix_bootstrap_service_account
```

## Firewall / Port-Exposition
- Öffentlich nur `443/TCP` (Apache). `8008`/`8448` der VM **nicht** öffentlich.
- Achtung: Docker published Ports über eigene iptables-Regeln und **umgeht ufw** —
  eine ufw-`deny`-Regel schützt einen gemappten Container-Port also nicht
  zuverlässig. Die eigentliche Kontrolle ist die **Bind-Adresse**: `SYNAPSE_BIND_ADDR`
  in `.env` auf `127.0.0.1` (Proxy auf demselben Host) bzw. die private VM-IP
  (Proxy auf separatem Host) setzen — nie `0.0.0.0` auf einem öffentlich
  erreichbaren Host. Nach Änderung: `docker compose up -d --force-recreate synapse`,
  Kontrolle mit `sudo ss -ltnp | grep 8008` (Bind-Adresse statt `0.0.0.0`).

## Betrieb / Day-2
- Backup: die `synapse`-DB in den bestehenden `pg_dump`-Lauf aufnehmen
  (`-d synapse`), plus das `./synapse`-Volume (Signing-Key! Verlust = neue
  Server-Identität, bricht Föderation und bestehende Räume).
- At-rest-Verschlüsselung der DB/Volumes auf das Niveau der App-Daten bringen.
