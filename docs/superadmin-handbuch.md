# Fichtelink — Super-Admin-Handbuch

Dieses Handbuch beschreibt die Funktionen, die **dem Super-Admin der Installation
vorbehalten** sind. In der Regel betrifft das eine **einzige Person** je
Installation. Alle übrigen Funktionen — einschließlich der Admin-Aufgaben für
Elternvertreter bzw. Listen-Ersteller — stehen im
**[Benutzerhandbuch](benutzerhandbuch.md)**.

Der Super-Admin ist die oberste Instanz: Er richtet die strukturelle Spitze der
Installation ein (Vorlagen, oberste Listen, Aggregat-Aliase), führt den
Schuljahres-Übergang aus und ist letzte Stufe der Konto-Wiederherstellung.

---

## Inhalt

1. [Wer ist Super-Admin? Erstinbetriebnahme](#1-wer-ist-super-admin-erstinbetriebnahme)
2. [Die Django-Administration](#2-die-django-administration)
3. [Vorlagen (LISTTEMPLATE)](#3-vorlagen-listtemplate)
4. [Oberste Listen anlegen](#4-oberste-listen-anlegen)
5. [Aggregat-Aliase (`eltern@…`)](#5-aggregat-aliase-eltern)
6. [Schuljahres-Übergang (Rollover)](#6-schuljahres-übergang-rollover)
7. [Test-Mail senden](#7-test-mail-senden)
8. [Formulare anlegen und klonen](#8-formulare-anlegen-und-klonen)
9. [Konto-Wiederherstellung](#9-konto-wiederherstellung)
10. [Löschanfragen und Aufbewahrungsfristen](#10-löschanfragen-und-aufbewahrungsfristen)
11. [Sonderrechte gegenüber regulären Admins](#11-sonderrechte-gegenüber-regulären-admins)

---

## 1. Wer ist Super-Admin? Erstinbetriebnahme

Der erste Super-Admin wird einmalig bei der Installation per
Management-Kommando auf dem Server angelegt:

```bash
docker compose exec web python manage.py bootstrap_super_admin
```

Das Kommando fragt nach einer E-Mail-Adresse, legt eine Person + ein als
Super-Admin markiertes Konto an und versendet einen
**Passkey-Aktivierungs-Link**. Es gibt kein Passwort — die Aktivierung erfolgt
ausschließlich über den Link, identisch zum regulären Einladungsablauf.

Super-Admin-Funktionen erscheinen an den passenden Stellen automatisch, sobald
Sie als Super-Admin angemeldet sind (z. B. der Eintrag **„Schuljahres-Übergang"**
und **„Test-Mail senden"** im Admin-Menü einer Liste).

---

## 2. Die Django-Administration

Strukturelle Konfiguration läuft über die **Django-Administration** unter
**`/admin/`**. Dort verwalten Sie, was es im normalen Nutzer-UI bewusst *nicht*
gibt:

- **Vorlagen** (LISTTEMPLATE) inkl. Attribute und Rollen-Taxonomie,
- **Aggregat-Aliase** (`eltern@…`),
- **Formulare** (Aufbau aus Teilen, Slots, Bild-Assets),
- direkte Korrekturen an Datensätzen, wenn nötig.

> `/admin/` ist nur für Super-Admins gedacht. Reguläre Listen-Admins erledigen
> ihre Aufgaben vollständig über das normale UI.

---

## 3. Vorlagen (LISTTEMPLATE)

Vorlagen bestimmen, **welche Felder eine Liste hat** und **wie sie angezeigt
werden**. Sie sind **Super-Admin-only** und werden in der Django-Administration
gepflegt. Eine Vorlage legt fest:

- die **Attribute** (Text, E-Mail, Telefon, Zahl, Auswahl mit optionaler
  Nutzungsobergrenze, Checkbox, Personen-Beziehung),
- ob ein Feld **immer öffentlich** sein muss (`Must_be_public`),
- die **Rolle** eines Attributs: gehört es zum **Mitglied** (z. B. dem Kind) oder
  zur **Bezugsperson** (`applies_to_role`),
- den **Onboarding-Modus** (`member_subject_mode`): `self` (die registrierende
  Person ist selbst das Mitglied, z. B. VHS-Kurs) oder `via_associate` (sie
  meldet ein anderes Mitglied an, z. B. Schulklasse),
- die **Rollen-Taxonomie** für Beziehungen („Mutter von", „Vater von",
  „Erziehungsberechtigte von", …) — konfigurierbar **je Vorlage**,
- die **Anzeige-Voreinstellung**: welche Felder in welcher Zeile/Reihenfolge
  erscheinen (Mehr-Personen-Zeile bei Schulklassen).

> Wer Vorlagen ändert, ändert die Struktur **aller Listen**, die sie nutzen.
> Änderungen an Attributen produktiver Vorlagen mit Bedacht vornehmen.

---

## 4. Oberste Listen anlegen

**Oberste Listen** (ohne übergeordnete Liste) — z. B. `Elternbeirat`,
`Lehrerkollegium` — dürfen **nur Super-Admins** anlegen. Das Aufsetzen der
strukturellen Spitze einer Installation ist ein administrativer Akt, keine
Selbstbedienung.

Reguläre Nutzer können darunter **Unterlisten** erstellen (z. B. einzelne
Klassen) und werden deren Admin. Die Hierarchie ist ein **expliziter Baum** über
die „übergeordnete Liste":

```
Elternbeirat
├── Klasse 5a
│   ├── Klassenfeier-Planung
│   └── Umfrage: Pizza-Wahl
├── Klasse 5b
└── …
Lehrerkollegium   (eigene oberste Liste, nicht im Eltern-Baum)
```

Die Hierarchie steuert u. a. das **Senderecht**: Mitglieder einer
übergeordneten Liste dürfen automatisch an die Unterlisten senden (z. B. der
Elternbeirat an eine Klassenliste).

---

## 5. Aggregat-Aliase (`eltern@…`)

Ein **Aggregat-Alias** ist eine E-Mail-Adresse, die über **mehrere Listen
hinweg** nach Beziehungsrolle ausfächert — z. B. `eltern@<domain>` erreicht alle
Personen mit Rolle „Mutter", „Vater" oder „Erziehungsberechtigte" innerhalb
eines konfigurierten Teilbaums. Konfiguration ist **Super-Admin-only** (Django-
Administration).

Felder eines Alias:

- **`email_alias`** — z. B. `eltern@<domain>`
- **`title`** — Anzeigename
- **`scope_list_id`** — beschränkt auf den Teilbaum dieser Liste (optional)
- **`included_roles`** — die Beziehungsrollen, die einbezogen werden

Eigenschaften:

- **Keine Mitgliederliste**, nur eine Abfrage: Empfänger werden **zum Sendezeit­
  punkt** ermittelt (nicht zwischengespeichert), entlang der Beziehungsrollen
  und des Teilbaums, dedupliziert, beschränkt auf Personen mit hinterlegter
  E-Mail.
- **Senderecht** leitet sich aus dem bestehenden Senderecht-Graphen ab: Senden
  darf, wer auf **jeder** betroffenen Liste Senderecht hat (z. B. der Vorsitz
  des Elternbeirats). Andernfalls landet die Mail in der **Super-Admin-
  Freigabe** — Aggregat-Aliase haben keinen Listen-Admin, sie gehören dem
  Super-Admin.

---

## 6. Schuljahres-Übergang (Rollover)

Eine **Stapeloperation für Schulklassen**, üblicherweise in der letzten Woche
der Sommerferien. Aufruf über das Admin-Menü einer Liste →
**„Schuljahres-Übergang"** (oder `/lists/rollover/`).

Ablauf:

1. Fichtelink zeigt eine **Vorschau aller Klassenlisten** mit gesetztem
   Schulzweig (G8/G9) und der geplanten Aktion (hochstufen, `K1 → K2`,
   `K2 endet → archivieren`).
2. **Titel und E-Mail-Alias** der neuen Klasse sind editierbar — prüfen,
   anpassen.
3. **Ausführen.**

Standardregeln:

- `Na → (N+1)a` für `N ∈ {5, …, 9}` (beide Züge)
- **G8:** `10a..10d → K1` (Zusammenführung), `K1 → K2`, `K2 → archiviert`
- **G9:** `10a → 11a`, …, `11a..11d → K1`, `K1 → K2`, `K2 → archiviert`

Wichtig:

- Listen werden **nicht gelöscht** — sie behalten ihre Identität, nur Titel und
  Kohorten-Felder ändern sich. Nutzer und ihre Einträge bleiben verknüpft.
- Bei Zusammenführungen wird eine neue K1-Liste angelegt und alle Einträge der
  Quelllisten umgehängt; die Quelllisten werden **archiviert** (schreibgeschützt).
- Der **E-Mail-Alias rollt sofort mit** (`5a@…` → `6a@…`). **Keine
  Übergangsfrist:** ab Tag 1 funktioniert nur die neue Adresse, Mail an die alte
  bekommt eine Unzustellbarkeitsmeldung.

> **Die Aktion ist nicht automatisiert reversibel.** Vorher unbedingt ein
> **Backup** ziehen (siehe README → *Backups*).

Voraussetzung: Die teilnehmenden Klassen brauchen gepflegte **Kohorten-Daten**
(Jahrgang, Zug, G8/G9) — diese setzen die jeweiligen Listen-Admins über
**„Kohorten-Daten"** (siehe Benutzerhandbuch, Abschnitt 13). Ohne gesetzten
Schulzweig nimmt eine Liste nicht am Übergang teil.

---

## 7. Test-Mail senden

Über das Admin-Menü einer Liste → **„Test-Mail senden"** (`/lists/<id>/send-test/`)
versenden Sie eine Nachricht **direkt an die Benutzergruppe** der Liste — unter
Umgehung des regulären Inbound-/Freigabe-Pfads. Die Seite zeigt vorab die
**Empfängerliste** (nur Mitglieder mit hinterlegter E-Mail-Adresse). Nützlich,
um nach Einrichtung oder Konfigurationsänderung den Mailversand einer Liste zu
prüfen.

---

## 8. Formulare anlegen und klonen

Formulare (Helfer-/Spendenlisten für Aktionen) werden **ausschließlich in der
Django-Administration** aufgebaut — es gibt bewusst keinen Self-Service-Baukasten.
Die Endnutzer-Seite ist nur der Eintrage-Ablauf (siehe Benutzerhandbuch,
Abschnitt 9).

Ein Formular besteht aus **Teilen** (`FORM_PART`) in frei wählbarer Reihenfolge:

- **`html`** — statischer, vertrauenswürdig gerenderter HTML-Text mit
  Bild-Assets (Super-Admin-autorisiert),
- **`slots`** — Positionen mit Kapazität (z. B. „Aufbau Freitag, 2 Plätze"),
- **`contributions`** — offene Freitext-Beiträge (z. B. „Wer bringt was mit?").

Weitere Punkte:

- **Broadcast-Link:** Pro Formular gibt es **genau einen** Teilen-Link
  (`/forms/s/<token>/`). Er wird beim ersten Öffnen der Detailseite durch einen
  Formular-Admin automatisch erzeugt und dort zum Kopieren angezeigt. Ansehen
  braucht keine Anmeldung, Eintragen schon.
- **`is_open`:** Eintragungen schließen, ohne die Ansicht zu entfernen.
- **Klonen:** Die Admin-Aktion `Form.clone()` kopiert ein Formular für
  wiederkehrende Anlässe (jährliches Schulfest) — mit Teilen, Slots, Bild-Assets
  und Texten, aber **ohne** Eintragungen, Zugriffe und Teilen-Token; die
  Eintragung wird wieder geöffnet.

---

## 9. Konto-Wiederherstellung

**Der Normalfall läuft als Selbstbedienung, ohne Sie:** Ein ausgesperrter Nutzer
fordert über *„Passkey verloren? Zugang wiederherstellen"* auf der Anmeldeseite
selbst einen Einrichtungs-Link an seine hinterlegte E-Mail-Adresse an (siehe
Benutzerhandbuch, Abschnitt 12). Der Super-Admin wird dafür **nicht** mehr
benötigt.

Das Management-Kommando bleibt als **Fallback** für die Fälle, die die
Selbstbedienung nicht abdeckt — der Nutzer hat **auch keinen Zugriff mehr aufs
Postfach**, oder ein geteiltes Familienpostfach muss eindeutig zugeordnet werden:

```bash
docker compose exec web python manage.py reset_passkeys --email <user-email>
```

Das Kommando **löscht** die bestehenden Passkeys (anders als die Selbstbedienung,
die nur additiv einen neuen hinzufügt) und **gibt einen frischen Enrollment-Link
auf stdout aus**. Diesen Link reichen Sie dem Nutzer **out-of-band** weiter
(Telefon, persönlich, separate E-Mail). Daten, Mitgliedschaften und
Familienbeziehungen bleiben erhalten.

> Passt die E-Mail auf **mehrere** Konten (geteiltes Familienpostfach),
> verweigert das Kommando die Ausführung und listet die Kandidaten auf —
> erneut mit `--user-id` ausführen.

> **Sicherheits-Hinweis:** Die Selbstbedienungs-Wiederherstellung bedeutet, dass
> wer Zugriff auf das Postfach eines Nutzers hat, einen neuen Passkey einrichten
> kann („gekapertes Postfach = möglicher Zugriff"). Das ist eine bewusste
> Produktentscheidung zugunsten der Bedienbarkeit. Die Offenlegung
> personenbezogener Daten an andere bleibt davon unberührt — sie ist weiterhin
> pro Liste opt-in.

---

## 10. Löschanfragen und Aufbewahrungsfristen

### Was das System selbst löscht

Sie müssen nichts anstoßen — der `worker` räumt nachts automatisch auf:

| Daten | Gelöscht nach | Einstellung (`.env`) |
|---|---|---|
| Verarbeitete Mails im Sammelpostfach (IMAP) | 7 Tagen | `IMAP_EXPUNGE_GRACE_DAYS` |
| Mail-Kopien in der Datenbank (inkl. Anhänge) und Versand-Protokoll | 90 Tagen | `MAIL_METADATA_RETENTION_DAYS` |
| Erledigte Aufträge der Versand-Warteschlange (enthalten den Mail-Text) | 7 Tagen | `TASK_JOB_RETENTION_DAYS` |
| Abgelaufene oder eingelöste Einladungs-, Aktivierungs- und QR-Links | 30 Tagen nach Ablauf/Einlösung | `TOKEN_RETENTION_DAYS` |
| Abgelaufene Anmelde-Sitzungen | täglich | — |
| Container-Logs | rotierend, max. 3 × 10 MB je Dienst | `docker-compose.yml` |
| Datenbank-Backups | 14 Tagen | `KEEP_DAYS` in `scripts/backup.sh` |

**Nicht** automatisch gelöscht werden Konten, Listeneinträge (auch archivierte)
und Formular-Eintragungen — die bleiben, bis eine Löschanfrage kommt.

> Ein alter, gedruckter QR-Code führt nach dem Aufräumen auf „Seite nicht
> gefunden" statt auf „abgelaufen". Für neue Klassen einfach einen neuen
> QR-Code erzeugen.

Ob die Aufräum-Jobs laufen, sehen Sie in der Datenbank:

```bash
docker compose exec db psql -U fichtelink fichtelink -c "select task_name, max(defer_timestamp) from procrastinate_periodic_defers group by 1 order by 1;"
```

### Eine Person löschen (Löschanfrage nach DSGVO)

Eine Löschfunktion in der Oberfläche gibt es nicht; auch die
Django-Administration verweigert das Löschen einer Person mit Einträgen. Bittet
jemand um Löschung, verwenden Sie das Management-Kommando:

```bash
docker compose exec web python manage.py delete_person --email <adresse>
```

Ohne `--yes` ist das ein **Trockenlauf**: Das Kommando zeigt nur an, was es
löschen würde. Prüfen Sie die Ausgabe und führen Sie es dann mit `--yes` erneut
aus. Gelöscht werden:

- die Person, ihr Konto (Passkeys, Mitgliedschaften, Admin-Rechte,
  Formular-Eintragungen) und alle ihre Listeneinträge, auch archivierte;
- **Kinder, die nur diese Person betreut** — sonst blieben Einträge zurück, die
  niemand mehr pflegen kann — sowie ein zweiter Elternteil ohne eigenes Konto,
  der nur für diese Kinder miterfasst wurde;
- Mail-Kopien, Versand-Protokoll und Einladungen an die E-Mail-Adresse der
  Person.

Was die Person **für andere** angelegt hat (verschickte Einladungen, angelegte
Formulare, Sendeberechtigungen, beantragte Umzüge), bleibt erhalten und wird auf
einen Super-Admin übertragen — standardmäßig den ersten, sonst mit
`--reassign-to <user-id>` einen bestimmten. Ein Formular dieser Person
verwalten danach Sie.

Sonderfälle:

- **Geteiltes Familienpostfach:** Passt die E-Mail auf mehrere Personen,
  listet das Kommando sie auf — erneut mit `--person-id <id>` ausführen. Mail-
  Daten zu dieser Adresse bleiben dann stehen, weil der andere Elternteil sie
  noch nutzt; sie laufen regulär nach 90 Tagen ab.
- **Kind behalten:** Mit `--keep-children` bleiben die Kinder erhalten, z. B.
  wenn der andere Elternteil das Kind demnächst übernehmen soll.
- **Einziger Admin einer Liste:** Das Kommando warnt, wenn eine Liste dadurch
  ihren letzten Admin verliert. Bestimmen Sie vorher einen Nachfolger (Admin
  hinzufügen bzw. Übergabe).

Nach dem Löschen stehen Restdaten nur noch an Stellen, die von selbst ablaufen:
Warteschlange (7 Tage), Logs (Rotation) und Backups (14 Tage). Das können Sie
der Person so mitteilen.

---

## 11. Sonderrechte gegenüber regulären Admins

Als Super-Admin haben Sie über die normalen Listen-Admin-Rechte hinaus:

- **Globale Sicht:** Sie sehen **Namen und E-Mail-Adressen immer**, unabhängig
  von der Sichtbarkeits-Matrix — nötig für Moderation und Wiederherstellung.
- **Umzüge und Austritte ohne Zustimmung:** Sie können Personen ohne die sonst
  nötige Zustimmung der Zielliste verschieben oder entfernen.
- **Verwaltung von Vorlagen, obersten Listen und Aggregat-Aliasen** (siehe oben).

> **Eine Grenze bleibt auch für den Super-Admin selten nötig zu überschreiten:**
> Die Sichtbarkeits-Einstellungen einer Person gehören der Person. Greifen Sie
> nur korrigierend ein, wenn es betrieblich unumgänglich ist.
