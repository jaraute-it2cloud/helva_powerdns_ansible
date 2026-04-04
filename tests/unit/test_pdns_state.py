import importlib.util
import pathlib
import unittest


def _load_pdns_state_module():
    root = pathlib.Path(__file__).resolve().parents[2]
    module_path = root / "plugins" / "module_utils" / "pdns_state.py"
    spec = importlib.util.spec_from_file_location("pdns_state", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pdns_state = _load_pdns_state_module()


class TestPowerDNSStateHelpers(unittest.TestCase):
    def test_normalize_network_ipv4(self):
        network, ip, prefix = pdns_state.normalize_network("192.168.1.123/24")
        self.assertEqual(network, "192.168.1.0/24")
        self.assertEqual(ip, "192.168.1.0")
        self.assertEqual(prefix, 24)

    def test_normalize_network_ipv6(self):
        network, ip, prefix = pdns_state.normalize_network("2001:db8::1/64")
        self.assertEqual(network, "2001:db8::/64")
        self.assertEqual(ip, "2001:db8::")
        self.assertEqual(prefix, 64)

    def test_normalize_view_name_valid(self):
        self.assertEqual(pdns_state.normalize_view_name("trusted-view_1"), "trusted-view_1")

    def test_normalize_view_name_invalid(self):
        with self.assertRaises(ValueError):
            pdns_state.normalize_view_name(".invalid")

    def test_zone_variant_validation(self):
        variants = pdns_state.normalize_zone_variants(["example.org..internal", "example.net..trusted"])
        self.assertEqual(sorted(variants), ["example.net..trusted", "example.org..internal"])

    def test_zone_variant_rejects_duplicate_zone_base(self):
        with self.assertRaises(ValueError):
            pdns_state.normalize_zone_variants(
                ["example.org..internal", "example.org..external"]
            )

    def test_compute_view_change_replace(self):
        to_add, to_remove = pdns_state.compute_view_change(
            existing_zone_variants=["example.org..internal", "example.net..trusted"],
            desired_zone_variants=["example.org..trusted"],
            mode="replace",
        )
        self.assertEqual(to_add, ["example.org..trusted"])
        self.assertEqual(to_remove, ["example.net..trusted", "example.org..internal"])

    def test_sanitize_record_content_txt_and_aaaa(self):
        txt = pdns_state.sanitize_record_content("TXT", ["hello", '"already"'])
        self.assertEqual(txt, ['"hello"', '"already"'])

        aaaa = pdns_state.sanitize_record_content("AAAA", ["2001:DB8::1"])
        self.assertEqual(aaaa, ["2001:db8::1"])


if __name__ == "__main__":
    unittest.main()
