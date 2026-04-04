# helvascale.helva_powerdns_ansible

Ansible Collection zur Verwaltung von PowerDNS Authoritative über die HTTP API, erweitert um Views, Zone-Varianten und Network-to-View-Mappings.

## Herkunft und Attribution

Dieses Projekt ist eine Weiterentwicklung von:
- Ursprungs-Repository: <https://github.com/Nosmoht/ansible-module-powerdns>
- Ursprünglicher Autor: Thomas Krahn (@nosmoht)

Copyright 2026 Helvascale GmbH

Lizenz: Apache-2.0 (siehe `LICENSE` und `NOTICE`).

## Projektziel

Produktionsreife Verwaltung von:
- Zonen
- Records
- Views
- Networks (Zuordnung Network -> View)

mit idempotentem Verhalten, `check_mode` und diff-fähiger Ergebnisdarstellung.

## Unterstützte Module

- `helvascale.helva_powerdns_ansible.powerdns_zone`
- `helvascale.helva_powerdns_ansible.powerdns_record`
- `helvascale.helva_powerdns_ansible.powerdns_view`
- `helvascale.helva_powerdns_ansible.powerdns_view_network`

## Voraussetzungen

- Ansible Core `>= 2.14`
- Python 3.9+
- PowerDNS Authoritative mit aktivierter HTTP API
- Für Views:
1. PowerDNS Authoritative `>= 5.0.0`
2. Views sind laut PowerDNS-Dokumentation experimentell
3. `views` muss in PowerDNS aktiviert sein
4. Zone-Cache muss aktiv sein (`zone-cache-refresh-interval` > 0)
5. Laut offizieller Doku derzeit sinnvoll mit LMDB-Backend

## Installation

Build/Install als Collection (lokal):

```bash
ansible-galaxy collection build
ansible-galaxy collection install ./helvascale-helva_powerdns_ansible-1.0.2.tar.gz
```

Direkt aus Git mit explizitem Tag (Update erzwingen):

```bash
ansible-galaxy collection install --force git+https://github.com/jaraute-it2cloud/helva_powerdns_ansible.git,v1.0.2
```

Nutzung im Playbook:

```yaml
collections:
  - helvascale.helva_powerdns_ansible
```

## Gemeinsame Verbindungsparameter

Alle Module unterstützen:
- `pdns_host`
- `pdns_port`
- `pdns_prot` (`http`/`https`)
- `strict_ssl_checking` (TLS-Validierung standardmäßig aktiv)
- API-Key (`pdns_api_key`) oder Basic Auth (`pdns_api_username`/`pdns_api_password`)

## Beispiele

Siehe `examples/`:
- `view_create.yml`
- `view_delete.yml`
- `view_replace_zone_variants.yml`
- `network_assign_view.yml`
- `split_dns_internal_external.yml`
- `split_dns_different_ttl.yml`

## Architektur

- `plugins/module_utils/pdns_client.py`:
  - zentraler PowerDNS HTTP-Client
  - konsistente Fehlerbehandlung
  - optionale `pdnsutil`-Ausführung für API-Lücken
- `plugins/module_utils/pdns_state.py`:
  - Validierung und Zustandsberechnung (View- und Network-Logik)
- `plugins/modules/*`:
  - Ansible-Module mit idempotenter Soll/Ist-Abgleichslogik

## API-/Mechanismus-Abdeckung

HTTP API (primär):
- Zonen: `/servers/{server}/zones`
- Suche/Records: `/servers/{server}/search-data`
- Views: `/servers/{server}/views`
- Networks: `/servers/{server}/networks`

`pdnsutil` (gezielt, optional):
- Entfernen eines Network-Mappings (`state: absent` in `powerdns_view_network`) kann über `pdnsutil network set <cidr>` erfolgen, da die HTTP API keinen dokumentierten DELETE-Endpunkt für Networks bereitstellt.

## Einschränkungen / bekannte Grenzen

- Das explizite Anlegen einer komplett leeren View ist weder über die dokumentierte HTTP API noch robust über einen dedizierten Endpunkt möglich. Views entstehen effektiv durch Zuordnung von Zone-Varianten.
- `powerdns_view state=absent` entfernt alle aktuell eingetragenen Zone-Varianten der View (idempotent).
- Zone-Varianten werden gemäß PowerDNS-Logik erwartet (z. B. `example.org..internal`).
- Bei Variant-Zonen darf kein zusätzlicher abschließender Punkt verwendet werden (korrekt: `example.org..internal`, nicht `example.org..internal.`).

## Qualitätssicherung

- Unit-Tests: `tests/unit/`
- Linting: `ruff`

Lokal ausführen:

```bash
python3 -m unittest discover -s tests/unit -p 'test_*.py'
ruff check .
```

Optional (wenn `ansible-test` verfügbar ist):

```bash
ansible-test sanity
```

## Sicherheitshinweise

- Keine Secrets in Beispielen oder Tests.
- TLS-Verifikation ist standardmäßig aktiv (`strict_ssl_checking: true`).
- Bei Deaktivierung der TLS-Prüfung steigt das Risiko von MITM-Angriffen.
