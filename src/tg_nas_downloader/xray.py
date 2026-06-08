from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .config import ConfigError


def write_xray_config(
    *,
    vless_link: str,
    output: Path,
    listen: str = "0.0.0.0",
    socks_port: int = 10808,
    http_port: int = 10809,
    loglevel: str = "warning",
) -> None:
    config = build_xray_config(
        vless_link=vless_link,
        listen=listen,
        socks_port=socks_port,
        http_port=http_port,
        loglevel=loglevel,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass


def build_xray_config(
    *,
    vless_link: str,
    listen: str = "0.0.0.0",
    socks_port: int = 10808,
    http_port: int = 10809,
    loglevel: str = "warning",
) -> dict[str, Any]:
    outbound = build_vless_outbound(vless_link)
    return {
        "log": {"loglevel": loglevel},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": listen,
                "port": socks_port,
                "protocol": "socks",
                "settings": {
                    "auth": "noauth",
                    "udp": False,
                },
            },
            {
                "tag": "http-in",
                "listen": listen,
                "port": http_port,
                "protocol": "http",
                "settings": {},
            },
        ],
        "outbounds": [
            outbound,
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
    }


def build_vless_outbound(vless_link: str) -> dict[str, Any]:
    parsed = urlsplit(vless_link.strip())
    if parsed.scheme.lower() != "vless":
        raise ConfigError("VLESS link must start with vless://")
    if not parsed.username:
        raise ConfigError("VLESS link must include the UUID before '@'.")
    if not parsed.hostname:
        raise ConfigError("VLESS link must include a host.")
    if not parsed.port:
        raise ConfigError("VLESS link must include a port.")

    query = _query(parsed.query)
    network = _first(query, "type", "tcp").lower()
    security = _first(query, "security", "none").lower()
    encryption = _first(query, "encryption", "none")
    flow = _first(query, "flow", "")

    user: dict[str, Any] = {"id": unquote(parsed.username), "encryption": encryption}
    if flow:
        user["flow"] = flow

    outbound: dict[str, Any] = {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": parsed.hostname,
                    "port": int(parsed.port),
                    "users": [user],
                }
            ]
        },
        "streamSettings": {
            "network": network,
            "security": security,
        },
    }

    stream = outbound["streamSettings"]
    _add_transport_settings(stream, network, query)
    _add_security_settings(stream, security, parsed.hostname, query)
    return outbound


def _add_transport_settings(stream: dict[str, Any], network: str, query: dict[str, list[str]]) -> None:
    if network == "ws":
        settings: dict[str, Any] = {}
        path = _first(query, "path", "/")
        host = _first(query, "host", "")
        if path:
            settings["path"] = path
        if host:
            settings["headers"] = {"Host": host}
        stream["wsSettings"] = settings
    elif network == "grpc":
        settings = {}
        service_name = _first(query, "serviceName", "")
        mode = _first(query, "mode", "")
        if service_name:
            settings["serviceName"] = service_name
        if mode == "multi":
            settings["multiMode"] = True
        stream["grpcSettings"] = settings
    elif network in {"httpupgrade", "splithttp", "xhttp"}:
        settings = {}
        path = _first(query, "path", "")
        host = _first(query, "host", "")
        if path:
            settings["path"] = path
        if host:
            settings["host"] = host
        key = "xhttpSettings" if network in {"splithttp", "xhttp"} else "httpupgradeSettings"
        stream[key] = settings
    elif network == "tcp":
        header_type = _first(query, "headerType", "")
        if header_type:
            stream["tcpSettings"] = {"header": {"type": header_type}}


def _add_security_settings(
    stream: dict[str, Any],
    security: str,
    address: str,
    query: dict[str, list[str]],
) -> None:
    if security == "tls":
        settings: dict[str, Any] = {
            "serverName": _first(query, "sni", _first(query, "serverName", address)),
        }
        alpn = _csv(_first(query, "alpn", ""))
        if alpn:
            settings["alpn"] = alpn
        fingerprint = _first(query, "fp", "")
        if fingerprint:
            settings["fingerprint"] = fingerprint
        if _truthy(_first(query, "allowInsecure", "")):
            settings["allowInsecure"] = True
        stream["tlsSettings"] = settings
    elif security == "reality":
        settings = {
            "serverName": _first(query, "sni", _first(query, "serverName", address)),
        }
        mapping = {
            "fp": "fingerprint",
            "pbk": "publicKey",
            "sid": "shortId",
            "spx": "spiderX",
        }
        for source, target in mapping.items():
            value = _first(query, source, "")
            if value:
                settings[target] = value
        stream["realitySettings"] = settings


def _query(raw: str) -> dict[str, list[str]]:
    return {
        key: [unquote(item) for item in values]
        for key, values in parse_qs(raw, keep_blank_values=True).items()
    }


def _first(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    if not values:
        return default
    return values[0]


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _truthy(value: str) -> bool:
    return value.lower() in {"1", "true", "yes", "on"}
