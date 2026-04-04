# -*- coding: utf-8 -*-

"""HTTP and optional pdnsutil helpers shared by PowerDNS modules."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode

from ansible.module_utils.common.text.converters import to_native, to_text
from ansible.module_utils.urls import open_url


class PowerDNSError(Exception):
    def __init__(self, message, status_code=None, url=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.url = url


def common_connection_argument_spec():
    return {
        "server": dict(type="str", default="localhost"),
        "pdns_host": dict(type="str", default="127.0.0.1"),
        "pdns_port": dict(type="int", default=8081),
        "pdns_prot": dict(type="str", default="http", choices=["http", "https"]),
        "pdns_api_key": dict(type="str", no_log=True),
        "pdns_api_username": dict(type="str"),
        "pdns_api_password": dict(type="str", no_log=True),
        "strict_ssl_checking": dict(type="bool", default=True),
        "request_timeout": dict(type="int", default=30),
    }


def pdnsutil_argument_spec():
    return {
        "pdnsutil_path": dict(type="str", default="pdnsutil"),
        "pdns_config_dir": dict(type="str", required=False),
        "pdns_config_name": dict(type="str", required=False),
    }


class PowerDNSClient:
    def __init__(self, module):
        self.module = module
        self.base_url = "{prot}://{host}:{port}/api/v1".format(
            prot=module.params["pdns_prot"],
            host=module.params["pdns_host"],
            port=module.params["pdns_port"],
        )
        self.api_key = module.params.get("pdns_api_key")
        self.api_username = module.params.get("pdns_api_username")
        self.api_password = module.params.get("pdns_api_password")
        self.validate_certs = module.params["strict_ssl_checking"]
        self.timeout = module.params["request_timeout"]

    @staticmethod
    def _quote_path_part(value):
        return quote(str(value), safe="")

    def _url(self, *parts, **query):
        encoded = "/".join(self._quote_path_part(part) for part in parts)
        url = f"{self.base_url}/{encoded}"
        if query:
            query = {k: v for k, v in query.items() if v is not None}
            if query:
                url = f"{url}?{urlencode(query)}"
        return url

    @staticmethod
    def _extract_error_message(body):
        if not body:
            return "No error message returned by PowerDNS"

        try:
            parsed = json.loads(body)
        except Exception:
            return body

        if isinstance(parsed, dict):
            if parsed.get("error"):
                return str(parsed["error"])
            if parsed.get("errors"):
                return str(parsed["errors"])
        return str(parsed)

    def _headers(self, json_payload=False):
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if json_payload:
            headers["Content-Type"] = "application/json"
        return headers

    def request(self, method, path_parts, payload=None, expected_codes=None, absent_codes=None, query=None):
        expected_codes = set(expected_codes or [200])
        absent_codes = set(absent_codes or [])
        query = query or {}
        url = self._url(*path_parts, **query)

        body = None
        if payload is not None:
            body = json.dumps(payload)

        try:
            response = open_url(
                url,
                method=method,
                headers=self._headers(json_payload=payload is not None),
                data=body,
                validate_certs=self.validate_certs,
                url_username=self.api_username,
                url_password=self.api_password,
                force_basic_auth=bool(self.api_username and self.api_password),
                timeout=self.timeout,
            )
            status_code = response.getcode()
            raw = response.read()
            text = to_text(raw, errors="surrogate_or_strict") if raw else ""
        except HTTPError as exc:
            status_code = exc.code
            raw = exc.read()
            text = to_text(raw, errors="surrogate_or_strict") if raw else ""
            if status_code in absent_codes:
                return None
            raise PowerDNSError(
                message=self._extract_error_message(text),
                status_code=status_code,
                url=url,
            ) from exc
        except URLError as exc:
            raise PowerDNSError(message=f"Connection error: {to_native(exc)}", url=url) from exc
        except Exception as exc:
            raise PowerDNSError(message=f"Unexpected request error: {to_native(exc)}", url=url) from exc

        if status_code in absent_codes:
            return None

        if status_code not in expected_codes:
            raise PowerDNSError(
                message=self._extract_error_message(text),
                status_code=status_code,
                url=url,
            )

        if not text:
            return {}

        try:
            return json.loads(text)
        except Exception:
            return {"raw": text}

    def get_zone(self, server, name):
        return self.request(
            "GET",
            ["servers", server, "zones", name],
            expected_codes=[200],
            absent_codes=[404, 422],
        )

    def create_zone(self, server, payload):
        return self.request("POST", ["servers", server, "zones"], payload=payload, expected_codes=[201, 204])

    def update_zone(self, server, name, payload):
        return self.request(
            "PATCH",
            ["servers", server, "zones", name],
            payload=payload,
            expected_codes=[200, 204],
        )

    def delete_zone(self, server, name):
        return self.request(
            "DELETE",
            ["servers", server, "zones", name],
            expected_codes=[200, 204],
            absent_codes=[404, 422],
        )

    def search_records(self, server, query):
        return self.request(
            "GET",
            ["servers", server, "search-data"],
            expected_codes=[200],
            query={"q": query},
        )

    def patch_rrsets(self, server, zone, rrsets):
        return self.request(
            "PATCH",
            ["servers", server, "zones", zone],
            payload={"rrsets": rrsets},
            expected_codes=[200, 204],
        )

    def list_views(self, server):
        payload = self.request("GET", ["servers", server, "views"], expected_codes=[200])
        if isinstance(payload, dict) and "views" in payload:
            return payload["views"]
        if isinstance(payload, list):
            return payload
        return []

    def get_view(self, server, view_name):
        return self.request(
            "GET",
            ["servers", server, "views", view_name],
            expected_codes=[200],
            absent_codes=[404],
        )

    def add_zone_to_view(self, server, view_name, zone_variant):
        return self.request(
            "POST",
            ["servers", server, "views", view_name],
            payload={"name": zone_variant},
            expected_codes=[200, 201, 204],
        )

    def remove_zone_from_view(self, server, view_name, zone_variant):
        return self.request(
            "DELETE",
            ["servers", server, "views", view_name, zone_variant],
            expected_codes=[200, 204],
            absent_codes=[404],
        )

    def list_networks(self, server):
        payload = self.request("GET", ["servers", server, "networks"], expected_codes=[200])
        if isinstance(payload, dict) and "networks" in payload:
            return payload["networks"]
        if isinstance(payload, list):
            return payload
        return []

    def get_network(self, server, ip, prefixlen):
        return self.request(
            "GET",
            ["servers", server, "networks", ip, prefixlen],
            expected_codes=[200],
            absent_codes=[404],
        )

    def set_network_view(self, server, ip, prefixlen, view_name):
        return self.request(
            "PUT",
            ["servers", server, "networks", ip, prefixlen],
            payload={"view": view_name},
            expected_codes=[200, 204],
        )


def extract_view_zone_variants(view_payload):
    if not view_payload:
        return []

    zones = view_payload.get("zones", [])
    extracted = []
    for zone in zones:
        if isinstance(zone, str):
            extracted.append(zone)
        elif isinstance(zone, dict):
            if zone.get("name"):
                extracted.append(zone["name"])
    return sorted(set(extracted))


def run_pdnsutil(module, pdnsutil_args):
    cmd = [module.params["pdnsutil_path"]]
    if module.params.get("pdns_config_dir"):
        cmd.extend(["--config-dir", module.params["pdns_config_dir"]])
    if module.params.get("pdns_config_name"):
        cmd.extend(["--config-name", module.params["pdns_config_name"]])

    cmd.extend(pdnsutil_args)

    rc, out, err = module.run_command(cmd)
    if rc != 0:
        module.fail_json(
            msg="pdnsutil command failed",
            rc=rc,
            command=" ".join(cmd),
            stdout=out,
            stderr=err,
        )

    return {"rc": rc, "stdout": out, "stderr": err, "command": " ".join(cmd)}
