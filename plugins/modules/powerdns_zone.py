#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright 2026 IT2Cloud GmbH
# Based on work by Thomas Krahn (@nosmoht), https://github.com/Nosmoht/ansible-module-powerdns
# Apache-2.0 (see LICENSE)

DOCUMENTATION = r'''
---
module: powerdns_zone
short_description: Manage PowerDNS zones
version_added: "1.0.0"
description:
  - Create, update or delete PowerDNS zones using the Authoritative HTTP API.
author:
  - Thomas Krahn (@nosmoht)
  - IT2Cloud GmbH
options:
  name:
    description:
      - Zone name.
      - A trailing dot is added automatically for regular zones.
      - Zone variants keep PowerDNS variant form, e.g. C(example.org..internal).
    type: str
    required: true
  kind:
    description:
      - Zone kind.
    type: str
    default: master
    choices: [native, master, slave]
  nameservers:
    description:
      - Zone nameservers.
    type: list
    elements: str
  masters:
    description:
      - Master servers for slave zones.
    type: list
    elements: str
  state:
    description:
      - Desired zone state.
    type: str
    default: present
    choices: [present, absent]
  server:
    type: str
    default: localhost
  pdns_host:
    type: str
    default: 127.0.0.1
  pdns_port:
    type: int
    default: 8081
  pdns_prot:
    type: str
    default: http
    choices: [http, https]
  pdns_api_key:
    type: str
  pdns_api_username:
    type: str
  pdns_api_password:
    type: str
  strict_ssl_checking:
    type: bool
    default: true
  request_timeout:
    type: int
    default: 30
'''

EXAMPLES = r'''
- name: Ensure zone exists
  helvascale.helva_powerdns_ansible.powerdns_zone:
    name: example.internal
    kind: master
    nameservers:
      - ns1.example.internal.
      - ns2.example.internal.
    state: present
    pdns_host: powerdns.example.internal
    pdns_api_key: "{{ vault_pdns_api_key }}"

- name: Delete zone
  helvascale.helva_powerdns_ansible.powerdns_zone:
    name: old.example.internal.
    state: absent
    pdns_host: powerdns.example.internal
    pdns_api_key: "{{ vault_pdns_api_key }}"
'''

RETURN = r'''
zone:
  description: Zone payload after operation.
  returned: always
  type: dict
changed:
  description: Whether the module made changes.
  returned: always
  type: bool
diff:
  description: Before/after representation for managed fields.
  returned: when changed
  type: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.helvascale.helva_powerdns_ansible.plugins.module_utils.pdns_client import (
    PowerDNSClient,
    PowerDNSError,
    common_connection_argument_spec,
)
from ansible_collections.helvascale.helva_powerdns_ansible.plugins.module_utils.pdns_state import (
    ensure_trailing_dot,
    normalize_zone_or_variant_name,
)

MANAGED_FIELDS = ("kind", "nameservers", "masters")


def _normalize_list(values, trailing_dot=False):
    if values is None:
        return []

    cleaned = [value.strip() for value in values if value and value.strip()]
    if trailing_dot:
        cleaned = [ensure_trailing_dot(value) for value in cleaned]
    return sorted(cleaned)


def _managed_payload(module):
    payload = {
        "kind": module.params["kind"],
        "nameservers": _normalize_list(module.params.get("nameservers"), trailing_dot=True),
        "masters": _normalize_list(module.params.get("masters"), trailing_dot=False),
    }
    return payload


def _extract_managed(zone_payload):
    if not zone_payload:
        return {"kind": None, "nameservers": [], "masters": []}

    return {
        "kind": zone_payload.get("kind"),
        "nameservers": _normalize_list(zone_payload.get("nameservers"), trailing_dot=True),
        "masters": _normalize_list(zone_payload.get("masters"), trailing_dot=False),
    }


def ensure_zone(module, client):
    server = module.params["server"]
    zone_name = normalize_zone_or_variant_name(module.params["name"])
    state = module.params["state"]

    zone = client.get_zone(server, zone_name)
    before = _extract_managed(zone)

    if state == "absent":
        if not zone:
            return False, None, None

        diff = {"before": before, "after": {"kind": None, "nameservers": [], "masters": []}}
        if module.check_mode:
            return True, None, diff

        client.delete_zone(server, zone_name)
        return True, None, diff

    desired_fields = _managed_payload(module)

    if not zone:
        create_payload = {
            "name": zone_name,
            "kind": desired_fields["kind"],
            "nameservers": desired_fields["nameservers"],
            "masters": desired_fields["masters"],
        }

        diff = {
            "before": before,
            "after": desired_fields,
        }
        if module.check_mode:
            return True, create_payload, diff

        client.create_zone(server, create_payload)
        return True, client.get_zone(server, zone_name), diff

    patch_payload = {}
    for field in MANAGED_FIELDS:
        if desired_fields[field] != before[field]:
            patch_payload[field] = desired_fields[field]

    if not patch_payload:
        return False, zone, None

    diff = {"before": before, "after": desired_fields}
    if module.check_mode:
        simulated = dict(zone)
        simulated.update(patch_payload)
        return True, simulated, diff

    client.update_zone(server, zone_name, patch_payload)
    return True, client.get_zone(server, zone_name), diff


def main():
    argument_spec = {
        "name": dict(type="str", required=True),
        "kind": dict(type="str", default="master", choices=["native", "master", "slave"]),
        "nameservers": dict(type="list", elements="str", required=False),
        "masters": dict(type="list", elements="str", required=False),
        "state": dict(type="str", default="present", choices=["present", "absent"]),
    }
    argument_spec.update(common_connection_argument_spec())

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = PowerDNSClient(module)

    try:
        changed, zone, diff = ensure_zone(module, client)
    except PowerDNSError as exc:
        module.fail_json(
            msg="PowerDNS zone operation failed",
            error=exc.message,
            status_code=exc.status_code,
            url=exc.url,
        )
    except ValueError as exc:
        module.fail_json(msg=str(exc))

    result = {
        "changed": changed,
        "zone": zone,
    }
    if diff:
        result["diff"] = diff

    module.exit_json(**result)


if __name__ == "__main__":
    main()
