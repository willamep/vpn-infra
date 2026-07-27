#!/usr/bin/env python3
"""Generate an Ansible inventory for manually provisioned VPN nodes."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_ROOT / "ansible" / "inventory" / "hosts.yml"
HOSTNAME_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
SSH_USER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*\$?$")


def valid_host(value: str) -> bool:
    """Return whether value is an IPv4/IPv6 address or a valid DNS hostname."""
    candidate = value.strip()
    if not candidate:
        return False

    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        pass

    hostname = candidate[:-1] if candidate.endswith(".") else candidate
    return (
        len(hostname) <= 253
        and bool(hostname)
        and all(HOSTNAME_LABEL.fullmatch(label) for label in hostname.split("."))
    )


def valid_user(value: str) -> bool:
    """Return whether value looks like an SSH account name."""
    return bool(SSH_USER.fullmatch(value.strip()))


def prompt_value(label: str, validator, default: str | None = None) -> str:
    """Prompt until a value accepted by validator is entered."""
    while True:
        suffix = f" [{default}]" if default is not None else ""
        try:
            value = input(f"{label}{suffix}: ").strip()
        except EOFError:
            raise SystemExit("\nInput ended before all required values were provided.")

        if not value and default is not None:
            value = default
        if validator(value):
            return value
        print(f"Invalid value: {value!r}. Please try again.", file=sys.stderr)


def resolve_value(
    parser: argparse.ArgumentParser,
    value: str | None,
    option: str,
    label: str,
    validator,
    default: str | None = None,
) -> str:
    """Validate a CLI value or ask for it when running interactively."""
    if value is not None:
        value = value.strip()
        if not validator(value):
            parser.error(f"invalid value for {option}: {value!r}")
        return value

    if sys.stdin.isatty():
        return prompt_value(label, validator, default)
    if default is not None:
        return default

    parser.error(f"{option} is required when standard input is not interactive")


def yaml_string(value: str) -> str:
    """Encode a string safely using YAML's JSON-compatible quoted form."""
    return json.dumps(value, ensure_ascii=False)


def render_inventory(entry_host: str, entry_user: str, exit_host: str, exit_user: str) -> str:
    """Render the inventory without requiring a third-party YAML package."""
    return (
        "all:\n"
        "  children:\n"
        "    vpn_nodes:\n"
        "      hosts:\n"
        "        vpn-entry:\n"
        f"          ansible_host: {yaml_string(entry_host)}\n"
        f"          ansible_user: {yaml_string(entry_user)}\n"
        "\n"
        "        vpn-exit:\n"
        f"          ansible_host: {yaml_string(exit_host)}\n"
        f"          ansible_user: {yaml_string(exit_user)}\n"
    )


def confirm_overwrite(path: Path) -> bool:
    """Ask before replacing an existing inventory in interactive mode."""
    try:
        answer = input(f"{path} already exists. Overwrite it? [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def write_inventory(path: Path, content: str) -> None:
    """Atomically write an inventory with the same permissions as Terraform output."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_path = Path(temporary_file.name)

        temporary_path.chmod(0o640)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate ansible/inventory/hosts.yml for vpn-entry and vpn-exit "
            "nodes provisioned without Terraform. Missing values are prompted "
            "for when run in a terminal."
        )
    )
    parser.add_argument("--entry-host", help="IPv4, IPv6, or hostname of vpn-entry")
    parser.add_argument("--entry-user", help="SSH user for vpn-entry (default: ansible)")
    parser.add_argument("--exit-host", help="IPv4, IPv6, or hostname of vpn-exit")
    parser.add_argument("--exit-user", help="SSH user for vpn-exit (default: ansible)")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"output file (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument("-f", "--force", action="store_true", help="overwrite an existing file")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    entry_host = resolve_value(
        parser, args.entry_host, "--entry-host", "vpn-entry address", valid_host
    )
    entry_user = resolve_value(
        parser,
        args.entry_user,
        "--entry-user",
        "vpn-entry SSH user",
        valid_user,
        default="ansible",
    )
    exit_host = resolve_value(
        parser, args.exit_host, "--exit-host", "vpn-exit address", valid_host
    )
    exit_user = resolve_value(
        parser,
        args.exit_user,
        "--exit-user",
        "vpn-exit SSH user",
        valid_user,
        default="ansible",
    )

    output = args.output.expanduser().resolve()
    if output.exists() and not args.force:
        if not sys.stdin.isatty():
            parser.error(f"{output} already exists; use --force to overwrite it")
        if not confirm_overwrite(output):
            print("Inventory was not changed.")
            return 1

    inventory = render_inventory(entry_host, entry_user, exit_host, exit_user)
    try:
        write_inventory(output, inventory)
    except OSError as error:
        parser.exit(1, f"Failed to write {output}: {error}\n")

    print(f"Inventory written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
