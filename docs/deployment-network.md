# Netzwerk- & Deployment-Skizze (Docker)

> Ausgangs-/Beispielwerte. `example.com` durch eure Schul-Domain ersetzen.
> Dies ist eine **Skizze zur Orientierung**, kein fertig getestetes Setup.
> Vor Produktivbetrieb: Federation-Tester laufen lassen, Secrets extern halten,
> Zertifikate/Backups einrichten.

## Topologie

```
Internet
   │  443/TCP (nur dieser Port offen)
   ▼
[ Reverse-Proxy (Caddy/nginx/Traefik) ]
   │            │                  │
   │ client     │ federation       │ .well-known
   ▼            ▼                  ▼
synapse:8008  synapse:8008      statische JSON-Antworten
   ▲
   │ intern (Docker-Netz, nicht öffentlich)
[ unser Python-Server ] ──► http://synapse:8008  (Client- + Admin-API)
[ PostgreSQL ] ◄── Synapse
```

Öffentlich offen: **nur 443/TCP**. `8008` und `8448` bleiben im internen Netz.

---

## docker-compose.yml (Skizze)

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: synapse
      POSTGRES_PASSWORD: ${SYNAPSE_DB_PASSWORD}   # aus .env / Secret
      POSTGRES_DB: synapse
      # Locale-Vorgabe von Synapse:
      POSTGRES_INITDB_ARGS: "--encoding=UTF8 --locale=C"
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks: [internal]

  synapse:
    image: matrixdotorg/synapse:latest
    depends_on: [postgres]
    environment:
      SYNAPSE_SERVER_NAME: example.com
      SYNAPSE_REPORT_STATS: "no"
    volumes:
      - ./synapse:/data
    networks: [internal]
    # KEIN ports:-Mapping → 8008/8448 nicht öffentlich

  app:
    build: ./app                       # unser bestehender Python-Server
    depends_on: [synapse]
    environment:
      MATRIX_BASE_URL: http://synapse:8008
      MATRIX_ADMIN_SHARED_SECRET: ${MATRIX_ADMIN_SHARED_SECRET}
    networks: [internal]

  proxy:
    image: caddy:2
    depends_on: [synapse, app]
    ports:
      - "443:443"                      # einziger öffentlicher Port
      - "80:80"                        # nur für ACME/Zertifikate
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddydata:/data
    networks: [internal]

networks:
  internal:

volumes:
  pgdata:
  caddydata:
```

---

## Caddyfile (Skizze – Delegation über 443)

```
# Föderations- und Client-Delegation via .well-known
example.com {
    handle /.well-known/matrix/server {
        header Content-Type application/json
        respond `{"m.server": "example.com:443"}`
    }
    handle /.well-known/matrix/client {
        header Content-Type application/json
        header Access-Control-Allow-Origin *
        respond `{"m.homeserver": {"base_url": "https://example.com"}}`
    }
    # Matrix Client- + Föderations-API an Synapse
    handle /_matrix/* {
        reverse_proxy synapse:8008
    }
    handle /_synapse/client/* {
        reverse_proxy synapse:8008
    }
    # Optional: unser Web-Frontend (PassKey) unter / ausliefern
    handle {
        reverse_proxy app:8000
    }
}
```

Hinweis: Die Admin-API `/_synapse/admin/*` wird hier bewusst **nicht** nach
außen geroutet. Sie ist nur intern über `http://synapse:8008` erreichbar –
unser Python-Server nutzt sie direkt im Docker-Netz.

---

## homeserver.yaml – relevante Auszüge (Skizze)

```yaml
server_name: "example.com"

listeners:
  - port: 8008
    type: http
    x_forwarded: true          # hinter Reverse-Proxy
    resources:
      - names: [client, federation]
        compress: false

database:
  name: psycopg2
  args:
    user: synapse
    password: "${SYNAPSE_DB_PASSWORD}"
    database: synapse
    host: postgres
    cp_min: 5
    cp_max: 10

# Föderation AN, aber Klassenräume durch invite-only + Power-Level geschützt
# (siehe docs/matrix-architecture.md §2/§3)

# Öffentliche Registrierung AUS – Konten nur via Admin-API
enable_registration: false
registration_shared_secret: "${MATRIX_ADMIN_SHARED_SECRET}"

# Datenschutz / Pseudonymität (Defense-in-Depth, §4)
require_auth_for_profile_requests: true
limit_profile_requests_to_users_who_share_rooms: true
include_profile_data_on_invite: false
```

---

## Checkliste Inbetriebnahme

1. `server_name` endgültig festlegen (bestimmt `@user:server_name`, unveränderlich).
2. DNS: A/AAAA-Record auf den Host; `https://example.com` muss den Proxy erreichen.
3. TLS-Zertifikat (Caddy macht ACME automatisch; sonst Let's Encrypt einrichten).
4. `.well-known/matrix/server` und `.../client` liefern korrektes JSON aus.
5. **Federation-Tester** gegen die Bare-Domain laufen lassen (nicht den Subhost).
6. Admin-Shared-Secret und DB-Passwort aus `.env`/Secret-Store, nie im Repo.
7. PostgreSQL-Backups + „at rest"-Verschlüsselung auf euer Niveau bringen (§5/§10).
8. Erst nach grünem Federation-Test: Test-Konto via Admin-API anlegen, Element
   verbinden, Klassenraum anlegen, Power-Level prüfen.
```
