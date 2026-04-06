#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright 2026 IT2Cloud GmbH
# Based on work by Thomas Krahn (@nosmoht), https://github.com/Nosmoht/ansible-module-powerdns
# Apache-2.0 (see LICENSE)

DOCUMENTATION = r'''
---
module: powerdns_view
short_description: Manage PowerDNS views and zone-variant assignments
version_added: "1.0.0"
description:
  - Manage PowerDNS Views with add/replace/remove semantics for zone variants.
  - Uses the PowerDNS Authoritative HTTP API.
author:
  - IT2Cloud GmbH
options:
  name:
    description:
      - View name.
    type: str
    required: true
  zone_variants:
    description:
      - List of zone variants in PowerDNS format, e.g. C(example.org..internal).
      - For C(state=present), behavior depends on C(mode).
    type: list
    elements: str
    default: []
  mode:
    description:
      - How C(zone_variants) is applied with C(state=present).
    type: str
    default: replace
    choices: [add, replace, remove]
  state:
    description:
      - C(absent) removes all zone variants from the view.
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
notes:
  - Views were added in PowerDNS Authoritative 5.0.0 and are experimental.
  - Views require explicit PowerDNS configuration and zone cache support.
'''

EXAMPLES = r'''
- name: Add one variant to a view
  helvascale.helva_powerdns_ansible.powerdns_view:
    name: internal
    mode: add
    zone_variants:
      - example.org..internal
    state: present
    pdns_api_key: "{{ vault_pdns_api_key }}"

- name: Replace all variants of a view
  helvascale.helva_powerdns_ansible.powerdns_view:
    name: trusted
    mode: replace
    zone_variants:
      - example.org..trusted
      - example.net..trusted
    state: present
    pdns_api_key: "{{ vault_pdns_api_key }}"

- name: Delete a view by removing all variant assignments
  helvascale.helva_powerdns_ansible.powerdns_view:
    name: internal
    state: absent
    pdns_api_key: "{{ vault_pdns_api_key }}"
'''

RETURN = r'''
view:
  description: View payload after operation.
  returned: always
  type: dict
changed:
  description: Whether changes were applied.
  returned: always
  type: bool
diff:
  description: Before/after zone variant lists.
  returned: when changed
  type: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.helvascale.helva_powerdns_ansible.plugins.module_utils.pdns_client import (
    PowerDNSClient,
    PowerDNSError,
    common_connection_argument_spec,
    extract_view_zone_variants,
)
from ansible_collections.helvascale.helva_powerdns_ansible.plugins.module_utils.pdns_state import (
    compute_view_change,
    normalize_view_name,
    normalize_zone_variants,
)


def _build_view_result(view_name, zone_variants):
    return {
        "name": view_name,
        "zone_variants": sorted(zone_variants),
    }


def ensure_view(module, client):
    server = module.params["server"]
    view_name = normalize_view_name(module.params["name"])
    state = module.params["state"]
    mode = module.params["mode"]

    desired_zone_variants = normalize_zone_variants(module.params.get("zone_variants", []))

    view_payload = client.get_view(server, view_name)
    existing = extract_view_zone_variants(view_payload)

    before = _build_view_result(view_name, existing)

    if state == "absent":
        to_add = []
        to_remove = list(existing)
        target = []
    else:
        if mode in ["add", "replace"] and not desired_zone_variants and not existing:
            raise ValueError(
                "cannot create an empty view via the HTTP API; provide at least one zone variant"
            )

        to_add, to_remove = compute_view_change(existing, desired_zone_variants, mode)

        if mode == "add":
            target = sorted(set(existing).union(set(desired_zone_variants)))
        elif mode == "replace":
            target = sorted(set(desired_zone_variants))
        else:  # remove
            target = sorted(set(existing).difference(set(desired_zone_variants)))

    changed = bool(to_add or to_remove)
    if not changed:
        return False, before, None

    after = _build_view_result(view_name, target)
    diff = {"before": before, "after": after}

    if module.check_mode:
        return True, after, diff

    for zone_variant in to_remove:
        client.remove_zone_from_view(server, view_name, zone_variant)

    for zone_variant in to_add:
        client.add_zone_to_view(server, view_name, zone_variant)

    final_payload = client.get_view(server, view_name)
    final_zone_variants = extract_view_zone_variants(final_payload)

    return True, _build_view_result(view_name, final_zone_variants), diff


def main():
    argument_spec = {
        "name": dict(type="str", required=True),
        "zone_variants": dict(type="list", elements="str", default=[]),
        "mode": dict(type="str", default="replace", choices=["add", "replace", "remove"]),
        "state": dict(type="str", default="present", choices=["present", "absent"]),
    }
    argument_spec.update(common_connection_argument_spec())

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = PowerDNSClient(module)

    try:
        changed, view, diff = ensure_view(module, client)
    except PowerDNSError as exc:
        module.fail_json(
            msg="PowerDNS view operation failed",
            error=exc.message,
            status_code=exc.status_code,
            url=exc.url,
        )
    except ValueError as exc:
        module.fail_json(msg=str(exc))

    result = {
        "changed": changed,
        "view": view,
    }
    if diff:
        result["diff"] = diff

    module.exit_json(**result)


if __name__ == "__main__":
    main()
