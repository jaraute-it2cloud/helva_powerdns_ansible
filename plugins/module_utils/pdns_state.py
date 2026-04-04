"""Pure state and validation helpers for PowerDNS modules."""

from __future__ import annotations

import ipaddress
import re
from collections import Counter
from collections.abc import Iterable

VIEW_NAME_PATTERN = re.compile(r"^(?![ .])[A-Za-z0-9._ -]+$")
ZONE_VARIANT_PATTERN = re.compile(r"^.*\.\.[a-z0-9_-]+$")


def ensure_trailing_dot(name: str) -> str:
    if name.endswith("."):
        return name
    return f"{name}."


def sanitize_record_content(record_type: str, content: list[str] | None) -> list[str]:
    if content is None:
        return []

    sanitized = list(content)

    if record_type == "AAAA":
        sanitized = [item.lower() for item in sanitized]

    if record_type == "TXT":
        quoted = []
        for item in sanitized:
            if item.startswith('"') and item.endswith('"'):
                quoted.append(item)
            else:
                quoted.append('"' + item.strip('"') + '"')
        sanitized = quoted

    return sanitized


def soa_without_serial(soa_content: str) -> str:
    parts = soa_content.split(" ")
    if len(parts) < 4:
        return soa_content
    return " ".join(parts[:2] + parts[3:])


def soa_serial(soa_content: str) -> str:
    parts = soa_content.split(" ")
    if len(parts) < 3:
        return ""
    return parts[2]


def matches_existing_content(record_type: str, content: str, existing_content: list[str]) -> bool:
    if record_type == "SOA" and soa_serial(content) == "0":
        normalized = soa_without_serial(content)
        return normalized in [soa_without_serial(item) for item in existing_content]
    return content in existing_content


def normalize_view_name(view_name: str) -> str:
    candidate = view_name.strip()
    if not candidate:
        raise ValueError("view name must not be empty")

    if not VIEW_NAME_PATTERN.match(candidate):
        raise ValueError(
            "invalid view name, allowed are letters, digits, space, dash, dot and underscore; "
            "must not start with a dot or a space"
        )
    return candidate


def normalize_zone_variant(zone_variant: str) -> str:
    candidate = zone_variant.strip()
    if not candidate:
        raise ValueError("zone variant must not be empty")

    if not ZONE_VARIANT_PATTERN.match(candidate):
        raise ValueError(
            "invalid zone variant, expected '<zone-with-trailing-dot>.<variant>' "
            "with lower-case variant; e.g. example.org..internal"
        )
    return candidate


def normalize_zone_variants(zone_variants: Iterable[str]) -> list[str]:
    normalized = [normalize_zone_variant(item) for item in zone_variants]

    zone_bases = [zone_base_from_variant(item) for item in normalized]
    duplicates = [zone for zone, count in Counter(zone_bases).items() if count > 1]
    if duplicates:
        raise ValueError(
            "zone variants contain more than one variant for the same base zone: "
            + ", ".join(sorted(duplicates))
        )

    return normalized


def zone_base_from_variant(zone_variant: str) -> str:
    if ".." not in zone_variant:
        return zone_variant
    base, _ = zone_variant.rsplit("..", 1)
    if base == "":
        return "."
    return ensure_trailing_dot(base)


def compute_view_change(
    existing_zone_variants: Iterable[str],
    desired_zone_variants: Iterable[str],
    mode: str,
) -> tuple[list[str], list[str]]:
    """Return tuple of (to_add, to_remove)."""

    existing = set(existing_zone_variants)
    desired = set(desired_zone_variants)

    if mode == "add":
        return sorted(desired - existing), []
    if mode == "replace":
        return sorted(desired - existing), sorted(existing - desired)
    if mode == "remove":
        return [], sorted(existing & desired)

    raise ValueError(f"unsupported mode '{mode}'")


def normalize_network(network: str) -> tuple[str, str, int]:
    try:
        parsed = ipaddress.ip_network(network, strict=False)
    except ValueError as exc:
        raise ValueError(f"invalid network '{network}': {exc}") from exc

    canonical = str(parsed)
    return canonical, str(parsed.network_address), int(parsed.prefixlen)
