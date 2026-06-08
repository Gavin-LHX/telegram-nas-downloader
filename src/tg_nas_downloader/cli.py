from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from telethon import TelegramClient

from .config import ConfigError, load_config, load_env_file
from .db import StateStore
from .downloader import TelegramNasDownloader
from .network import parse_proxy_url, redact_proxy_url
from .xray import write_xray_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Telegram videos to a NAS folder.")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config YAML.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("auth", help="Log in and create/update the Telegram session file.")
    subparsers.add_parser("run", help="Scan optional recent history, then keep listening for new videos.")

    backfill = subparsers.add_parser("backfill", help="Download videos from recent chat history, then exit.")
    backfill.add_argument("--limit", type=int, default=None, help="Messages to scan per chat. Defaults to config.")

    subparsers.add_parser("list-chats", help="List recent dialogs and IDs for config.")

    xray_config = subparsers.add_parser("xray-config", help="Generate Xray config JSON from a VLESS link.")
    xray_config.add_argument("--vless-link", default=None, help="VLESS URI. Defaults to VLESS_LINK from .env.")
    xray_config.add_argument("--output", default="./data/xray-config.json", help="Output Xray config path.")
    xray_config.add_argument("--listen", default="0.0.0.0", help="Xray inbound listen address.")
    xray_config.add_argument("--socks-port", type=int, default=10808, help="Local SOCKS inbound port.")
    xray_config.add_argument("--http-port", type=int, default=10809, help="Local HTTP inbound port.")
    xray_config.add_argument("--loglevel", default="warning", help="Xray loglevel.")

    args = parser.parse_args()

    if args.command == "xray-config":
        run_xray_config_command(args, Path(args.config))
        return

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        parser.error(str(exc))

    configure_logging(config.logging.level)
    asyncio.run(run_command(args.command, Path(args.config), args, config))


async def run_command(command: str, config_path: Path, args: argparse.Namespace, config) -> None:
    config.telegram.session.parent.mkdir(parents=True, exist_ok=True)
    proxy = parse_proxy_url(config.network.proxy_url, rdns=config.network.proxy_rdns)
    if proxy:
        logging.getLogger(__name__).info("Using Telegram proxy: %s", redact_proxy_url(config.network.proxy_url))

    client = TelegramClient(
        str(config.telegram.session),
        config.telegram.api_id,
        config.telegram.api_hash,
        proxy=proxy,
    )

    async with client:
        if command == "auth":
            me = await client.get_me()
            print(f"Logged in as {me.first_name or ''} (@{me.username or 'no_username'}), session: {config.telegram.session}")
            return

        if command == "list-chats":
            await list_chats(client)
            return

        store = StateStore(config.state.db)
        downloader = TelegramNasDownloader(config, client, store)
        try:
            if command == "backfill":
                await downloader.scan_history(limit=args.limit)
                return
            if command == "run":
                await downloader.run_forever()
                return
            raise SystemExit(f"Unknown command: {command}")
        finally:
            await downloader.wait_for_background_tasks()
            store.close()


async def list_chats(client: TelegramClient) -> None:
    print("Recent dialogs:")
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        username = getattr(entity, "username", None)
        username_text = f"@{username}" if username else ""
        print(f"{dialog.id}\t{dialog.name}\t{username_text}")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def run_xray_config_command(args: argparse.Namespace, config_path: Path) -> None:
    load_env_file(Path(".env"))
    load_env_file(config_path.with_name(".env"))
    vless_link = args.vless_link or os.environ.get("VLESS_LINK")
    if not vless_link:
        raise SystemExit("Provide --vless-link or set VLESS_LINK in .env.")

    output = Path(args.output).resolve()
    try:
        write_xray_config(
            vless_link=vless_link,
            output=output,
            listen=args.listen,
            socks_port=args.socks_port,
            http_port=args.http_port,
            loglevel=args.loglevel,
        )
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Wrote Xray config: {output}")


if __name__ == "__main__":
    main()
