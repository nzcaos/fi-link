## Anforderungen:
- E-Mail-Verteiler, bei dem sich die User selbst registieren können
- E-Mail-Gruppen, die sich an existierenden Gruppen orientieren, z.B. die Eltern der Schulklassen einer Schule
- Kontrolle, wer an die E-Mail-Gruppen E-Mails versenden darf, um Spam zu vermeiden
- Möglichkeit für die User, Kontaktdaten (z.B. Adresse, Telefonnummer) in Kontaktlisten der Gruppen (z. B. die Eltern einer Schulklasse) einzutragen und auf diese Weise innerhalb der Klasse auszutauschen
- Jeder Anwender soll selbst kontrollieren können, welche Infos (z. B. Adresse oder Telefonnummer) innerhalb der Gruppe oder öffentlich einsehbar sein soll oder nicht (Datenschutz)
- Möglichkeit zum Erzeugen von weiteren Listen (über die Klassenverbünde hinaus), z. B. Helferlisten für Schulfeste, bei denen sich jeder eintragen kann oder Abstimmungen innerhalb einer Klasse über z.B. Reiseziele eines Ausflugs oder zur Terminfindung
- Jede Gruppe (z.B. Eltern einer Schulklasse) soll eine eigene E-Mail-Adresse haben können, an die die Mitglieder eine E-Mail senden können, damit alle Mitglieder innerhalb der Gruppe die Mail erhalten können, ohne jedoch die eigene E-Mail-Adresse offenlegen zu müssen.
- Wenn jemand, der nicht selbst in der Gruppe ist, eine Mail an die Gruppe senden möchte, soll die Mail abgelehnt werden mit der Begründung, dass der Absender nicht berechtigt ist, Mails an die Gruppe zu senden.
- Wenn ein Mitglied der Gruppe eine Mail an die Gruppe sendet, soll das Mitglied zunächst eine Rückfrage-Mail bekommen mit einem Freigabe-Link, auf den er klicken muss, bevor die Mail wirklich an die Gruppe weitergeleitet wird. Auf diese Weise soll verhindert werden, dass Fremde mit gefälschter Absender-Adresse Mails an die Gruppe versenden können.
- Wenn jemand aus der Gruppe eine Mail an die Gruppe sendet, er seine eigene E-Mail-Adresse aber nicht offenlegen möchte, soll in der Mail an die Gruppenmitglieder die Absender-Adresse durch eine automatisch generierte Mail-Adresse ersetzt werden, damit man zwar eine Antwort auf die Mail schicken kann, man aber nicht auf die echte Mail-Adresse des Absenders schließen kann. Der Mail-Server soll dann die Mail an die automatisch generierte Adresse weiterleiten an den ursprünglichen Absender.
## Komponenten
### Listen
- Es kann beliebig viele Listen geben.
- Jeder User kann eine Liste erstellen und ist damit automatisch Administrator der Liste.
- Jede Liste hat (mindestens) einen oder mehrere Administratoren.
- Jede Liste ist entweder privat oder öffentlich. 
	- **Öffentlich sichtbar** bedeutet, dass jeder User die Liste sehen kann. 
	- **Offentlich bearbeitbar** bedeutet, dass jeder User einen Eintrag in der Liste machen kann.
	- **Privat** bedeutet, dass nur eingeladene User die Liste sehen und Einträge machen können.
- Der Administrator einer privaten Liste kann andere User **einladen**. Dazu kann er sich einen Link generieren lassen, den er z.B. als QR-Code bei einer Veranstaltung (z.B. Elternabend) austeilen oder per E-Mail versenden kann.
- Klickt ein User auf einen Einladungs-Link wird er in die Benutzergruppe der Liste aufgenommen und kann ggfs. einen Eintrag in die Liste machen, wenn er das möchte.
- Nach der Anmeldung am System erscheinen in der Navigationsleiste alle Listen, auf die der User Zugriff hat: alle öffentlichen Listen sowie die privaten, zu denen er eine Einladung erhalten und angenommen hat.
- Jeder User kann nur einen Eintrag in jede Liste machen. 
- Ein Eintrag "gehört" dem User, der ihn erstellt hat: nur er kann den Eintrag bearbeiten. 
- Alle anderen können den Eintrag nur sehen, sofern der Ersteller des Eintrags, dies freigegeben hat. Kein anderer User kann einen Eintrag eines anderen Users verändern.
- Der Ersteller eines Eintrags kann für den Eintrag als solchem sowie für jedes Feld der Liste entscheiden, wer den Inhalt des Feldes sehen kann: zunächst sind die Felder in der Regel auf "nicht einsehbar" gestellt, der User kann die Sichtbarkeit des Feldes für andere User aber freigeben.
- Die Sichtbarkeit des Feldes kann pro Benutzergruppe eingestellt werden.
- **Benutzergruppen** ergeben sich automatisch durch Listen: Alle User, die in eine Liste eingeladen wurden und die Einladung akzeptiert haben, gehören zur User-Gruppe der Liste, unabhängig davon, ob der User einen Eintrag gemacht hat oder nicht. Jeder User ist automatisch Mitglied in allen öffentlichen Listen.
- Über die Administratoren der Listen ergibt sich eine Hierarchie der Listen. Zu jeder Liste eines Administrators sind alle Listen, denen der Administrator angehört, mögliche *übergeordnete* Listen. 
	  Beispiel: Frau X erstellt eine Liste "Kuchen für Ausflug". Frau X ist selbst in der Liste "Eltern der Klasse 9c" und kann deshalb die Sichtbarkeit der eigenen Liste "Kuchen für Ausflug" auf die Mitglieder der Liste "Eltern der Klasse 9c" vererben.
- Ein User ist also Mitglied allen Benutzergruppen, die sich aus allen Listen ergibt, in die er eingeladen wurde.
### Listen-Vorlagen
- Wenn ein User eine neue Liste anlegt, kann er aus einer Menge von Vorlagen wählen.
- Die Vorlage definiert, welche Felder eine Liste enthält, welche Wertemenge die Felder haben, ob die Felder Pflichtfelder sind und ob die Felder von den Usern als "privat" markiert werden können oder öffentlich sein müssen.
- Vorlagen können nur vom Super-Admin angelegt werden.
- Es gibt folgende Feldtypen:
	- Text (z.B. Stadt, Adresse, welcher Kuchen, ...)
	- Mail-Aresse
	- Telefonnummer
	- Zahl (z.B. PLZ)
	- Auswahlfeld (Wertemenge kann vom Admin der Liste beim Erstellen der Liste vorgegeben werden), optional mit max. Anzahl von Verwendung
	- Checkbox
	- Beziehung zu anderem User (Mutter/Vater von Schulkind)
- Beispiele für Listen:
	- Kontaktliste einer Klasse: Vorname, Nachname, Adresse, PLZ, Ort, Festnetz-Tel, Mobil-Tel, Mail-Adresse
	- Helfer-Liste bei Schulfest: Auswahlfeld mit zu verteilenden Aufgaben, Textfeld für mitzubringenden Kuchen
	- Abstimmung: Auswahlfeld mit möglichen Antworten (Ausflugszielen, Terminen, ...)
### Mailingliste
- Ruft per IDLE neue E-Mails an den Mail-Server ab
- Es wird nur eine einzige E-Mail-Adresse innerhalb des Domains benötigt ("catch all").
- Jede Liste hat auch eine E-Mail-Adresse, durch das Senden einer E-Mail an die E-Mail-Adresse einer Liste wird die Mail automatisch an alle Mitglieder der Liste weitergeleitet.
- Eine Speicherung der E-Mail findet nicht statt. Es wird lediglich die Entgegennahme sowie die Weiterleitung der E-Mail protokolliert.
- Der Administrator einer Liste kann festlegen, wer E-Mails an die Liste senden darf: Üblicherweise dürfen das nur die User, die eingeladen wurden und die Einladung akzeptiert haben. Der Admin kann festlegen, dass auch Mitglieder übergeordneter Listen Mails an die Liste senden dürfen.
	  Beispiel: In der Regel sind die Elternvertreter einer Klasse die Administratoren der Liste der jeweiligen Klasse. Die Elternvertreter sind aber gleichzeitig Mitglied in der Liste "Elternbeirat". Sie können dann festlegen, dass entweder alle Elternvertreter oder die Administratoren der Liste "Elternbeirat", also die Vorsitzenden des Elternbeirats auch E-Mails an die Klassenliste senden dürfen. Damit bekommen die Vorsitzenden des Elternbeirats die Möglichkeit, E-Mails an alle Eltern zu senden.
- Wenn eine Mail an eine Liste gesendet wird und der Absender ist Mitglied der Liste, wird ihm zunächst eine Mail mit einem Freigabelink geschickt. Er muss die Weiterleitung an die anderen Mitglieder dann zunächst freigeben, bevor die Mail wirklich weitergeleitet wird. Damit soll verhindert werden, dass jemand sich als Mitglied einer Liste ausgibt.
- Wenn eine Mail an eine Liste gesendet wird, der Absender aber nicht Mitglied der Liste ist, geht der Freigabelink an den Admin der Liste. Er kann dann entscheiden, ob die Mail weitergeleitet werden soll oder nicht.
- Wenn der Absender einer Mail an die Liste Mitglied der Liste ist, das Mitglied der Liste aber der Veröffentlichung seiner Mail-Adresse nicht zugestimmt hat, wird die Absenderadresse der Mail vor der Weiterleitung durch den Listen-Server duch eine anonymisierte Mail-Adresse des Listenservers ersetzt.
- Antwortet ein Empfänger auf eine solche Mail mit ersetzter Absender-Adresse, dann leitet der Listenserver die Antwort an den ursprünglichen Sender der ersten Mail weiter.


# Strukturen
### USER
- User_ID
- Username
- E-Mail

### LIST
- List_ID
- Titel
- E-Mail
- -> Template-ID

### LIST_ADMIN
- List_Admin_ID
- -> List_ID
- -> User_ID

### LIST_ACCESS
- List_Access_ID
- -> List_ID
- -> User_ID

### LISTTEMPLATE
Vorlage für eine Liste, enthält diverse LIST_ATTRIBUT
- List_Template_ID
- Titel

### LIST_ATTRIBUT 
- List_Attribut_ID
- -> List_Template-ID
- Titel
- Order in List
- Typ
- Must_be_public: wenn TRUE, kann nicht gesperrt werden

### LIST_ATRIBUT_VALUE
- List_Attribut_Value_ID
- -> List_Attribut_ID
- Value

### LIST_RECORD
- List_Record_ID
- -> List_ID
- -> List_Attribut_ID
- owner -> User_ID

### LIST_RECORD_VALUE
- List_Value_ID
- -> List_Record_ID
- -> List_Attribut_ID
- Value

### LIST_RECORD_ACCESS
- List_Record_Access_ID
- List_Value_ID -> LIST_RECORD_VALUE.List_Value_ID
- Audience -> List_ID: diese Zugriffsberechtigung gilt für die User, die Zugriff auf diese Liste haben; 0 bedeutet: öffentlich

### FORM
- Form_ID
- Title

### FORM_PART
- Form_Part_ID
- Form -> Form_ID
- Order in Form
- Title
- BODY: Text, HTML-Vorlage für einen Teil eines Formulars
- List -> List-ID: der dynamische Teil des Formulars: eine Liste

### FORM_PART_ASSET
Bilder oder Grafiken, die in FORM_PART.BODY verwendet werden.
- Mime-Type
- Title
- -> Form_Part_ID
- Data

### FORM_ACCESS
- Form_Access_ID
- -> Form_ID
- -> User_ID
