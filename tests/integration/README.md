# Integrationstests

Diese Tests sind optional und setzen eine erreichbare PowerDNS-Authoritative-Instanz voraus.

Benötigte Umgebungsvariablen:
- `PDNS_HOST`
- `PDNS_API_KEY`

Ausführung (Beispiel):

```bash
ansible-playbook tests/integration/test_views.yml -e pdns_host="$PDNS_HOST" -e pdns_api_key="$PDNS_API_KEY"
```
