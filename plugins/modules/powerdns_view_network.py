#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright 2026 IT2Cloud GmbH
# Based on work by Thomas Krahn (@nosmoht), https://github.com/Nosmoht/ansible-module-powerdns
# Apache-2.0 (see LICENSE)

DOCUMENTATION = r'''
---
module: powerdns_view_network
short_description: Manage PowerDNS network-to-view mappings
version_added: "1.0.0"
description:
  - Manage mappings between CIDR networks and PowerDNS views.
  - Uses the PowerDNS HTTP API for create/update.
  - Uses optional C(pdnsutil) fallback for delete, because no documented HTTP DELETE endpoint exists.
author:
  - IT2Cloud GmbH
options:
  network:
    description:
      - Network in CIDR notation.
    type: str
    required: true
  view:
    description:
      - View name to assign to C(network).
      - Required for C(state=present).
    type: str
  state:
    type: str
    default: present
    choices: [present, absent]
  delete_via:
    description:
      - Mechanism used for C(state=absent).
    type: str
    default: auto
    choices: [auto, pdnsutil, fail]
  pdnsutil_path:
    type: str
    default: pdnsutil
  pdns_config_dir:
    type: str
  pdns_config_name:
    type: str
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
- name: Map network to view
  helvascale.helva_powerdns_ansible.powerdns_view_network:
    network: 192.168.0.0/16
    view: internal
    state: present
    pdns_api_key: "{{ vault_pdns_api_key }}"

- name: Remove network mapping (uses pdnsutil fallback)
  helvascale.helva_powerdns_ansible.powerdns_view_network:
    network: 192.168.0.0/16
    state: absent
    delete_via: auto
    pdns_api_key: "{{ vault_pdns_api_key }}"
'''

RETURN = r'''
mapping:
  description: Mapping status after operation.
  returned: always
  type: dict
changed:
  description: Whether changes were applied.
  returned: always
  type: bool
diff:
  description: Before/after mapping values.
  returned: when changed
  type: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible_collections.helvascale.helva_powerdns_ansible.plugins.module_utils.pdns_client import (
    PowerDNSClient,
    PowerDNSError,
    common_connection_argument_spec,
    pdnsutil_argument_spec,
    run_pdnsutil,
)
from ansible_collections.helvascale.helva_powerdns_ansible.plugins.module_utils.pdns_state import (
    normalize_network,
    normalize_view_name,
)


def _extract_network_mapping(payload, fallback_network):
    if not payload:
        return {"network": fallback_network, "view": None}

    if isinstance(payload, dict):
        if payload.get("network"):
            return {"network": payload["network"], "view": payload.get("view")}
        if payload.get("networks") and isinstance(payload["networks"], list):
            for item in payload["networks"]:
                if item.get("network") == fallback_network:
                    return {"network": item.get("network"), "view": item.get("view")}

    return {"network": fallback_network, "view": None}


def ensure_network(module, client):
    server = module.params["server"]
    state = module.params["state"]
    delete_via = module.params["delete_via"]

    network_cidr, network_ip, prefixlen = normalize_network(module.params["network"])

    current_payload = client.get_network(server, network_ip, prefixlen)
    current = _extract_network_mapping(current_payload, network_cidr)

    before = {
        "network": network_cidr,
        "view": current.get("view"),
    }

    if state == "present":
        desired_view = normalize_view_name(module.params["view"])
        after = {
            "network": network_cidr,
            "view": desired_view,
        }

        if before["view"] == desired_view:
            return False, {**after, "backend": "api"}, None

        diff = {"before": before, "after": after}
        if module.check_mode:
            return True, {**after, "backend": "api"}, diff

        client.set_network_view(server, network_ip, prefixlen, desired_view)
        final_payload = client.get_network(server, network_ip, prefixlen)
        final_mapping = _extract_network_mapping(final_payload, network_cidr)
        final_mapping["backend"] = "api"
        return True, final_mapping, diff

    if before["view"] is None:
        return False, {"network": network_cidr, "view": None, "backend": "none"}, None

    after = {
        "network": network_cidr,
        "view": None,
    }
    diff = {"before": before, "after": after}

    if module.check_mode:
        return True, {**after, "backend": "check_mode"}, diff

    if delete_via == "fail":
        module.fail_json(
            msg=(
                "Removing a network mapping is not available via a documented PowerDNS HTTP DELETE endpoint; "
                "set delete_via=auto or delete_via=pdnsutil"
            ),
            network=network_cidr,
        )

    if delete_via in ["auto", "pdnsutil"]:
        command_result = run_pdnsutil(module, ["network", "set", network_cidr])

        final_payload = client.get_network(server, network_ip, prefixlen)
        final_mapping = _extract_network_mapping(final_payload, network_cidr)
        if final_mapping.get("view") is not None:
            module.fail_json(
                msg="network mapping still exists after pdnsutil delete attempt",
                network=network_cidr,
                mapping=final_mapping,
                command=command_result,
            )

        return True, {"network": network_cidr, "view": None, "backend": "pdnsutil"}, diff

    module.fail_json(msg=f"unsupported delete_via value: {delete_via}")


def main():
    argument_spec = {
        "network": dict(type="str", required=True),
        "view": dict(type="str", required=False),
        "state": dict(type="str", default="present", choices=["present", "absent"]),
        "delete_via": dict(type="str", default="auto", choices=["auto", "pdnsutil", "fail"]),
    }
    argument_spec.update(common_connection_argument_spec())
    argument_spec.update(pdnsutil_argument_spec())

    module = AnsibleModule(
        argument_spec=argument_spec,
        required_if=[("state", "present", ["view"])],
        supports_check_mode=True,
    )

    client = PowerDNSClient(module)

    try:
        changed, mapping, diff = ensure_network(module, client)
    except PowerDNSError as exc:
        module.fail_json(
            msg="PowerDNS network-view operation failed",
            error=exc.message,
            status_code=exc.status_code,
            url=exc.url,
        )
    except ValueError as exc:
        module.fail_json(msg=str(exc))

    result = {
        "changed": changed,
        "mapping": mapping,
    }
    if diff:
        result["diff"] = diff

    module.exit_json(**result)


if __name__ == "__main__":
    main()
