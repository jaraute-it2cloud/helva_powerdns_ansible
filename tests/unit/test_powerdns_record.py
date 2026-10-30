"""Exercise the real record reconciler with exact-zone API response fixtures."""

import copy
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
MODULE_UTILS = "ansible_collections.helvascale.helva_powerdns_ansible.plugins.module_utils"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load this checkout's production helpers, not any installed collection.
pdns_client = load_module("pdns_client", "plugins/module_utils/pdns_client.py")
pdns_state = load_module("pdns_state", "plugins/module_utils/pdns_state.py")
with patch.dict(sys.modules, {
    MODULE_UTILS + ".pdns_client": pdns_client,
    MODULE_UTILS + ".pdns_state": pdns_state,
}):
    record = load_module("powerdns_record", "plugins/modules/powerdns_record.py")

INTERNAL = "example.test..internal"
EXTERNAL = "example.test..external"
NAME = "service.example.test."


def rrset(record_type="AAAA", contents=None, ttl=120, name=NAME, disabled=False):
    if contents is None:
        contents = ["2001:db8::10"]
    return {
        "name": name, "type": record_type, "ttl": ttl,
        "records": [{"content": content, "disabled": disabled} for content in contents],
    }


class Module:
    def __init__(self, check_mode=False, **params):
        self.check_mode = check_mode
        self.params = {
            "server": "localhost", "zone": INTERNAL, "name": "service", "type": "AAAA",
            "content": ["2001:db8::10"], "ttl": 120, "disabled": False,
            "set_ptr": False, "state": "present", "exclusive": True,
            **params,
        }

    def fail_json(self, **kwargs):
        raise AssertionError(kwargs["msg"])


class ZoneClient:
    def __init__(self, zones=None):
        self.zones = copy.deepcopy(zones if zones is not None else {
            INTERNAL: {"rrsets": [rrset("A", ["192.0.2.10"]), rrset()]},
            EXTERNAL: {"rrsets": [rrset("A", ["198.51.100.10"]), rrset(contents=["2001:db8:1::10"])]},
        })
        self.reads = []
        self.writes = []

    def search_records(self, server, query):
        raise AssertionError("Record reconciliation must not depend on search-data")

    def get_zone(self, server, zone):
        self.reads.append((server, zone))
        return copy.deepcopy(self.zones.get(zone))

    def patch_rrsets(self, server, zone, rrsets):
        self.writes.append((server, zone, copy.deepcopy(rrsets)))
        for change in rrsets:
            existing = self.zones[zone]["rrsets"]
            existing[:] = [item for item in existing if (item["name"], item["type"]) != (change["name"], change["type"])]
            if change["changetype"] == "REPLACE":
                updated = copy.deepcopy(change)
                updated.pop("changetype")
                existing.append(updated)


class TestRecordReconciliation(unittest.TestCase):
    def test_existing_rrsets_are_unchanged_in_both_views(self):
        for zone, content in [(INTERNAL, "2001:db8::10"), (EXTERNAL, "2001:db8:1::10")]:
            for check_mode in (False, True):
                with self.subTest(zone=zone, check_mode=check_mode):
                    client = ZoneClient()
                    changed, current, diff = record.ensure_record(Module(check_mode=check_mode, zone=zone, content=[content]), client)
                    self.assertFalse(changed)
                    self.assertIsNone(diff)
                    self.assertEqual(current["records"], [{"content": content, "disabled": False}])
                    self.assertEqual(client.reads, [("localhost", zone)])
                    self.assertEqual(client.writes, [])

    def test_regular_zone_and_canonical_name_remain_supported(self):
        client = ZoneClient({"example.test.": {"rrsets": [rrset()]}})
        changed, _, _ = record.ensure_record(Module(zone="example.test", name=NAME), client)
        self.assertFalse(changed)
        self.assertEqual(client.reads, [("localhost", "example.test.")])

    def test_record_and_type_matching_are_exact(self):
        client = ZoneClient({INTERNAL: {"rrsets": [
            rrset("A", ["192.0.2.10"]), rrset(name="other.example.test."),
        ]}})
        changed, _, diff = record.ensure_record(Module(check_mode=True), client)
        self.assertTrue(changed)
        self.assertEqual(diff["before"]["records"], [])
        self.assertEqual(client.writes, [])

    def test_create_then_idempotence_preserves_a_and_other_view(self):
        client = ZoneClient()
        client.zones[INTERNAL]["rrsets"] = [rrset("A", ["192.0.2.10"])]
        before = copy.deepcopy(client.zones)
        changed, current, diff = record.ensure_record(Module(), client)
        self.assertTrue(changed)
        self.assertEqual(current, rrset())
        self.assertEqual(diff["before"]["records"], [])
        self.assertEqual(client.zones[INTERNAL]["rrsets"][0], before[INTERNAL]["rrsets"][0])
        self.assertEqual(client.zones[EXTERNAL], before[EXTERNAL])
        self.assertEqual(client.writes[0][1], INTERNAL)
        self.assertEqual(client.writes[0][2][0]["name"], NAME)
        changed, _, diff = record.ensure_record(Module(), client)
        self.assertFalse(changed)
        self.assertIsNone(diff)
        self.assertEqual(len(client.writes), 1)

    def test_real_content_and_ttl_changes_have_complete_before_diff(self):
        for desired in ({"content": ["2001:db8::20"]}, {"ttl": 240}):
            for check_mode in (False, True):
                with self.subTest(desired=desired, check_mode=check_mode):
                    client = ZoneClient()
                    before = copy.deepcopy(client.zones)
                    changed, _, diff = record.ensure_record(Module(check_mode=check_mode, **desired), client)
                    self.assertTrue(changed)
                    self.assertEqual(diff["before"], {"name": NAME, "type": "AAAA", "ttl": 120, "records": ["2001:db8::10"]})
                    self.assertEqual(client.zones[EXTERNAL], before[EXTERNAL])
                    self.assertEqual(client.zones[INTERNAL]["rrsets"][0], before[INTERNAL]["rrsets"][0])
                    self.assertEqual(len(client.writes), 0 if check_mode else 1)
                    if check_mode:
                        self.assertEqual(client.zones, before)
                    else:
                        self.assertFalse(record.ensure_record(Module(**desired), client)[0])

    def test_delete_then_idempotence_preserves_a_and_other_view(self):
        client = ZoneClient()
        before = copy.deepcopy(client.zones)
        changed, current, diff = record.ensure_record(Module(state="absent", content=None), client)
        self.assertTrue(changed)
        self.assertEqual(current["records"], [])
        self.assertEqual(diff["before"]["records"], ["2001:db8::10"])
        self.assertEqual(client.zones[INTERNAL]["rrsets"], [before[INTERNAL]["rrsets"][0]])
        self.assertEqual(client.zones[EXTERNAL], before[EXTERNAL])
        self.assertEqual(client.writes[0], ("localhost", INTERNAL, [{"name": NAME, "type": "AAAA", "changetype": "DELETE", "records": []}]))
        self.assertFalse(record.ensure_record(Module(state="absent", content=None), client)[0])
        self.assertEqual(len(client.writes), 1)

    def test_check_mode_create_and_delete_never_write(self):
        for state in ("present", "absent"):
            with self.subTest(state=state):
                client = ZoneClient()
                if state == "present":
                    client.zones[INTERNAL]["rrsets"] = [rrset("A", ["192.0.2.10"])]
                before = copy.deepcopy(client.zones)
                changed, _, _ = record.ensure_record(Module(check_mode=True, state=state), client)
                self.assertTrue(changed)
                self.assertEqual(client.zones, before)
                self.assertEqual(client.writes, [])

    def test_nonexclusive_delete_preserves_other_aaaa_values(self):
        client = ZoneClient({INTERNAL: {"rrsets": [rrset(contents=["2001:db8::10", "2001:db8::20"])]}})
        changed, current, _ = record.ensure_record(Module(state="absent", exclusive=False), client)
        self.assertTrue(changed)
        self.assertEqual(current["records"], [{"content": "2001:db8::20", "disabled": False}])

    def test_existing_record_types_remain_idempotent(self):
        fixtures = {
            "A": ["192.0.2.10"], "CNAME": ["target.example.test."],
            "MX": ["10 mail.example.test."], "TXT": ['"example text"'],
        }
        for record_type, contents in fixtures.items():
            with self.subTest(record_type=record_type):
                client = ZoneClient({INTERNAL: {"rrsets": [rrset(record_type, contents)]}})
                changed, _, _ = record.ensure_record(Module(type=record_type, content=contents), client)
                self.assertFalse(changed)
                self.assertEqual(client.writes, [])

    def test_record_metadata_is_preserved_and_sorted(self):
        client = ZoneClient({INTERNAL: {"rrsets": [rrset(contents=["2001:db8::20", "2001:db8::10"], disabled=True)]}})
        current = record._extract_rrset(client, "localhost", INTERNAL, NAME, "AAAA")
        self.assertEqual(current["records"], [
            {"content": "2001:db8::10", "disabled": True},
            {"content": "2001:db8::20", "disabled": True},
        ])

    def test_absent_zone_allows_check_mode_record_creation(self):
        client = ZoneClient({})
        changed, _, diff = record.ensure_record(Module(check_mode=True), client)
        self.assertTrue(changed)
        self.assertEqual(diff["before"]["records"], [])
        self.assertEqual(diff["after"]["records"], ["2001:db8::10"])
        self.assertEqual(client.writes, [])

    def test_absent_zone_deletion_is_idempotent(self):
        client = ZoneClient({})
        changed, _, diff = record.ensure_record(Module(state="absent", content=None), client)
        self.assertFalse(changed)
        self.assertIsNone(diff)
        self.assertEqual(client.writes, [])

    def test_malformed_zone_data_never_becomes_an_empty_baseline(self):
        invalid = [[], {}, {"rrsets": None}, {"rrsets": {}}, {"rrsets": [None]},
                   {"rrsets": [{}]}, {"rrsets": [rrset(), rrset()]}]
        for field, value in [("ttl", None), ("ttl", True), ("ttl", -1), ("records", None),
                             ("records", [None]), ("records", [{"content": 1}]),
                             ("records", [{"content": "2001:db8::10", "disabled": "false"}])]:
            bad = rrset()
            bad[field] = value
            invalid.append({"rrsets": [bad]})
        for payload in invalid:
            for state in ("present", "absent"):
                with self.subTest(payload=payload, state=state):
                    client = ZoneClient({INTERNAL: payload})
                    with self.assertRaises(pdns_client.PowerDNSError):
                        record.ensure_record(Module(state=state), client)
                    self.assertEqual(client.writes, [])

    def test_api_errors_propagate_without_writes(self):
        client = ZoneClient()
        with patch.object(client, "get_zone", side_effect=pdns_client.PowerDNSError("request failed", status_code=503)):
            with self.assertRaises(pdns_client.PowerDNSError):
                record.ensure_record(Module(), client)
        self.assertEqual(client.writes, [])


class TestZoneHTTPReadback(unittest.TestCase):
    def client(self):
        return pdns_client.PowerDNSClient(Module(
            pdns_prot="https", pdns_host="pdns.example.test", pdns_port=443,
            strict_ssl_checking=True, request_timeout=5,
        ))

    def test_http_200_null_and_malformed_responses_never_mean_absence(self):
        for body in (b"null", b"[]", b"false", b"0", b'"zone"', b"not json", b""):
            for state in ("present", "absent"):
                for check_mode in (False, True):
                    with self.subTest(body=body, state=state, check_mode=check_mode):
                        response = Mock()
                        response.getcode.return_value = 200
                        response.read.return_value = body
                        client = self.client()
                        with patch.object(pdns_client, "open_url", return_value=response), patch.object(client, "patch_rrsets") as write:
                            with self.assertRaises(pdns_client.PowerDNSError):
                                record.ensure_record(Module(state=state, check_mode=check_mode), client)
                            write.assert_not_called()

    def test_http_404_and_422_preserve_absent_zone_semantics(self):
        for status in (404, 422):
            for state in ("present", "absent"):
                with self.subTest(status=status, state=state):
                    error = HTTPError("https://pdns.example.test/", status, "absent", {}, io.BytesIO(b'{"error":"zone absent"}'))
                    client = self.client()
                    with patch.object(pdns_client, "open_url", side_effect=error), patch.object(client, "patch_rrsets") as write:
                        changed, _, diff = record.ensure_record(Module(state=state, check_mode=True), client)
                        self.assertEqual(changed, state == "present")
                        if state == "present":
                            self.assertEqual(diff["before"]["records"], [])
                        else:
                            self.assertIsNone(diff)
                        write.assert_not_called()

    def test_http_200_exact_zone_response_is_idempotent(self):
        response = Mock()
        response.getcode.return_value = 200
        response.read.return_value = json.dumps({"rrsets": [rrset()]}).encode()
        client = self.client()
        with patch.object(pdns_client, "open_url", return_value=response) as request, patch.object(client, "patch_rrsets") as write:
            changed, _, diff = record.ensure_record(Module(), client)
            self.assertFalse(changed)
            self.assertIsNone(diff)
            self.assertEqual(request.call_args.args[0], "https://pdns.example.test:443/api/v1/servers/localhost/zones/example.test..internal")
            self.assertEqual(request.call_args.kwargs["method"], "GET")
            write.assert_not_called()

    def test_other_endpoints_retain_existing_response_contract(self):
        client = self.client()
        for body, expected in ((b"null", None), (b"", {}), (b"not json", {"raw": "not json"})):
            with self.subTest(body=body):
                response = Mock()
                response.getcode.return_value = 200
                response.read.return_value = body
                with patch.object(pdns_client, "open_url", return_value=response):
                    self.assertEqual(client.request("GET", ["other-endpoint"]), expected)


if __name__ == "__main__":
    unittest.main()
