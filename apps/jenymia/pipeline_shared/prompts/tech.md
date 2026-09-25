version: tech-2026-09-24

## Produktgruppe: Tech & Sicherheit

Zusaetzlich zu den Regeln oben gilt fuer diese Gruppe:

- Datenschutz ist das Hauptkriterium: welche Daten erhoben werden, wo sie
  liegen, ob sie weitergegeben werden, ob sich das abschalten laesst.
- `data_categories` nur fuellen, wenn eine Quelle die Erhebung belegt.
  `third_party_sharing` bleibt null, wenn der Hersteller dazu nichts sagt -
  das ist etwas anderes als ein Nein.
- `is_offline_capable` und `requires_account` sind harte Fakten, keine
  Einschaetzung. Im Zweifel false und im Detailtext begruenden.

## Ampel in dieser Gruppe (vorlaeufig)

- 3: Daten bleiben auf dem Geraet oder in der EU, kein Zwangskonto,
  funktioniert offline.
- 2: Daten in Drittlaendern oder Konto noetig, aber transparent erklaert.
- 1: unklare Datenlage, Weitergabe an Dritte oder kein Offline-Betrieb.
