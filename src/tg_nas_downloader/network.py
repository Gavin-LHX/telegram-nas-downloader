from __future__ import annotations

from urllib.parse import unquote, urlsplit

from .config import ConfigError


SUPPORTED_PROXY_SCHEMES = {"socks5", "socks4", "http"}


def parse_proxy_url(proxy_url: str | None, *, rdns: bool = True) -> dict[str, object] | None:
    if not proxy_url:
        return None

    parsed = urlsplit(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        supported = ", ".join(sorted(SUPPORTED_PROXY_SCHEMES))
        raise ConfigError(f"Unsupported network.proxy_url scheme '{parsed.scheme}'. Use one of: {supported}.")
    if not parsed.hostname:
        raise ConfigError("network.proxy_url must include a host.")
    if not parsed.port:
        raise ConfigError("network.proxy_url must include a port.")

    return {
        "proxy_type": scheme,
        "addr": parsed.hostname,
        "port": int(parsed.port),
        "rdns": rdns,
        "username": unquote(parsed.username) if parsed.username else None,
        "password": unquote(parsed.password) if parsed.password else None,
    }


def redact_proxy_url(proxy_url: str | None) -> str:
    if not proxy_url:
        return ""
    parsed = urlsplit(proxy_url)
    userinfo = ""
    if parsed.username:
        userinfo = f"{parsed.username}:***@"
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{userinfo}{host}{port}"
