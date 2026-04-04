#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright 2026 Helvascale GmbH
# Based on work by Thomas Krahn (@nosmoht), https://github.com/Nosmoht/ansible-module-powerdns
# Apache-2.0 (see LICENSE)

DOCUMENTATION = r'''
---
module: powerdns_record
short_description: Manage PowerDNS records
version_added: "1.0.0"
description:
  - Create, update or delete PowerDNS records using the Authoritative HTTP API.
author:
  - Thomas Krahn (@nosmoht)
  - Helvascale GmbH
options:
  name:
    description:
      - Record name.
      - If no zone suffix is present, C(zone) is appended.
    type: str
    required: true
  zone:
    description:
      - Zone name where the record lives.
      - Zone variants use PowerDNS variant form without final dot, e.g. C(example.org..internal).
    type: str
    required: true
  type:
    description:
      - Record type.
    type: str
    required: true
    choices: [A, AAAA, CNAME, MX, PTR, SOA, SRV, TXT, LUA, NS, SSHFP]
  content:
    description:
      - Record content list.
      - Required for C(state=present).
      - Required for C(state=absent) with C(exclusive=false).
    type: list
    elements: str
  ttl:
    type: int
    default: 86400
  disabled:
    type: bool
    default: false
  exclusive:
    description:
      - If true, non-specified records in the RRSet are removed.
    type: bool
    default: true
  set_ptr:
    description:
      - Let PowerDNS auto-maintain PTR records for A/AAAA.
    type: bool
    default: false
  state:
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
- name: Ensure A record
  helvascale.helva_powerdns_ansible.powerdns_record:
    name: host01.example.internal
    zone: example.internal
    type: A
    content:
      - 192.0.2.10
    ttl: 3600
    state: present
    pdns_api_key: "{{ vault_pdns_api_key }}"

- name: Remove single A record from RRSet
  helvascale.helva_powerdns_ansible.powerdns_record:
    name: host01.example.internal
    zone: example.internal
    type: A
    content:
      - 192.0.2.10
    exclusive: false
    state: absent
    pdns_api_key: "{{ vault_pdns_api_key }}"
'''

RETURN = r'''
record:
  description: Record RRSet after operation.
  returned: always
  type: dict
changed:
  description: Whether changes were applied.
  returned: always
  type: bool
diff:
  description: Before/after RRSet comparison.
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
    matches_existing_content,
    normalize_zone_or_variant_name,
    sanitize_record_content,
)


def _canonical_record_name(name, zone):
    canonical_zone = normalize_zone_or_variant_name(zone)

    if ".." in canonical_zone:
        canonical_name = name.strip().rstrip(".")
        if not canonical_name.endswith(canonical_zone):
            canonical_name = f"{canonical_name}.{canonical_zone}"
    else:
        canonical_name = ensure_trailing_dot(name.strip())
        if not canonical_name.endswith(canonical_zone):
            canonical_name = ensure_trailing_dot(f"{canonical_name.rstrip('.')}.{canonical_zone.rstrip('.')}")

    return canonical_name, canonical_zone


def _extract_rrset(client, server, zone, name, record_type):
    results = client.search_records(server, name)

    if not isinstance(results, list):
        return {"name": name, "type": record_type, "ttl": None, "records": []}

    rrset = {"name": name, "type": record_type, "ttl": None, "records": []}
    for entry in results:
        if entry.get("object_type") != "record":
            continue
        if entry.get("type") != record_type:
            continue
        if entry.get("name") != name:
            continue
        if entry.get("zone") != zone:
            continue

        if rrset["ttl"] is None:
            rrset["ttl"] = entry.get("ttl")

        rrset["records"].append(
            {
                "content": entry.get("content"),
                "disabled": bool(entry.get("disabled", False)),
            }
        )

    rrset["records"] = sorted(rrset["records"], key=lambda item: item.get("content", ""))
    return rrset


def _existing_contents(rrset):
    return [entry.get("content") for entry in rrset.get("records", []) if entry.get("content") is not None]


def _build_replace_rrset(name, record_type, ttl, disabled, set_ptr, content):
    records = []
    for item in content:
        record = {
            "content": item,
            "disabled": disabled,
        }
        if set_ptr and record_type in ["A", "AAAA"]:
            record["set-ptr"] = True
        records.append(record)

    return {
        "name": name,
        "type": record_type,
        "ttl": ttl,
        "changetype": "REPLACE",
        "records": records,
    }


def _build_delete_rrset(name, record_type):
    return {
        "name": name,
        "type": record_type,
        "changetype": "DELETE",
        "records": [],
    }


def _validate_required_inputs(module):
    state = module.params["state"]
    content = module.params.get("content")
    exclusive = module.params["exclusive"]

    if state == "present" and not content:
        module.fail_json(msg="content is required for state=present")

    if state == "absent" and not exclusive and not content:
        module.fail_json(msg="content is required for state=absent with exclusive=false")


def ensure_record(module, client):
    _validate_required_inputs(module)

    server = module.params["server"]
    record_type = module.params["type"]
    ttl = module.params["ttl"]
    disabled = module.params["disabled"]
    set_ptr = module.params["set_ptr"]
    state = module.params["state"]
    exclusive = module.params["exclusive"]

    name, zone = _canonical_record_name(module.params["name"], module.params["zone"])
    desired_content = sanitize_record_content(record_type, module.params.get("content"))

    current = _extract_rrset(client, server, zone, name, record_type)
    existing_content = _existing_contents(current)

    before = {
        "name": current["name"],
        "type": current["type"],
        "ttl": current.get("ttl"),
        "records": existing_content,
    }

    if state == "present":
        if not existing_content:
            after = {
                "name": name,
                "type": record_type,
                "ttl": ttl,
                "records": sorted(desired_content),
            }
            diff = {"before": before, "after": after}

            if module.check_mode:
                return True, after, diff

            rrset = _build_replace_rrset(name, record_type, ttl, disabled, set_ptr, desired_content)
            client.patch_rrsets(server, zone, [rrset])
            return True, _extract_rrset(client, server, zone, name, record_type), diff

        record_content = []
        for item in desired_content:
            if not matches_existing_content(record_type, item, existing_content) or current.get("ttl") != ttl:
                record_content.append(item)

        if exclusive:
            final_content = list(desired_content)
        else:
            final_content = existing_content + [item for item in record_content if item not in existing_content]

        final_content = sorted(set(final_content))
        unchanged = sorted(existing_content) == final_content and current.get("ttl") == ttl
        if unchanged:
            return False, current, None

        after = {
            "name": name,
            "type": record_type,
            "ttl": ttl,
            "records": final_content,
        }
        diff = {"before": before, "after": after}

        if module.check_mode:
            return True, after, diff

        rrset = _build_replace_rrset(name, record_type, ttl, disabled, set_ptr, final_content)
        client.patch_rrsets(server, zone, [rrset])
        return True, _extract_rrset(client, server, zone, name, record_type), diff

    if not existing_content:
        return False, current, None

    if exclusive:
        after = {"name": name, "type": record_type, "ttl": None, "records": []}
        diff = {"before": before, "after": after}
        if module.check_mode:
            return True, after, diff

        client.patch_rrsets(server, zone, [_build_delete_rrset(name, record_type)])
        return True, after, diff

    desired_remove = set(desired_content)
    final_content = [item for item in existing_content if item not in desired_remove]

    if len(final_content) == len(existing_content):
        return False, current, None

    final_content = sorted(set(final_content))
    after = {
        "name": name,
        "type": record_type,
        "ttl": ttl,
        "records": final_content,
    }
    diff = {"before": before, "after": after}

    if module.check_mode:
        return True, after, diff

    if final_content:
        rrset = _build_replace_rrset(name, record_type, ttl, disabled, set_ptr, final_content)
        client.patch_rrsets(server, zone, [rrset])
        return True, _extract_rrset(client, server, zone, name, record_type), diff

    client.patch_rrsets(server, zone, [_build_delete_rrset(name, record_type)])
    return True, {"name": name, "type": record_type, "ttl": None, "records": []}, diff


def main():
    argument_spec = {
        "name": dict(type="str", required=True),
        "zone": dict(type="str", required=True),
        "type": dict(
            type="str",
            required=True,
            choices=["A", "AAAA", "CNAME", "MX", "PTR", "SOA", "SRV", "TXT", "LUA", "NS", "SSHFP"],
        ),
        "content": dict(type="list", elements="str", required=False),
        "ttl": dict(type="int", default=86400),
        "disabled": dict(type="bool", default=False),
        "exclusive": dict(type="bool", default=True),
        "set_ptr": dict(type="bool", default=False),
        "state": dict(type="str", default="present", choices=["present", "absent"]),
    }
    argument_spec.update(common_connection_argument_spec())

    module = AnsibleModule(
        argument_spec=argument_spec,
        supports_check_mode=True,
    )

    client = PowerDNSClient(module)

    try:
        changed, record, diff = ensure_record(module, client)
    except PowerDNSError as exc:
        module.fail_json(
            msg="PowerDNS record operation failed",
            error=exc.message,
            status_code=exc.status_code,
            url=exc.url,
        )
    except ValueError as exc:
        module.fail_json(msg=str(exc))

    result = {
        "changed": changed,
        "record": record,
    }
    if diff:
        result["diff"] = diff

    module.exit_json(**result)


if __name__ == "__main__":
    main()
