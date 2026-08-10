"""Shared address / SSH host extraction for inventory CIs."""

from __future__ import annotations

import ipaddress
import re
from typing import Any


class AddressExtractor:
    """Cohesive helpers for IPv4/IPv6 and SSH target host parsing."""

    _IPV6_BRACKETS = re.compile(r"^\[([^\]]+)\]$")

    @staticmethod
    def normalize_ip(value: str) -> str | None:
        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError:
            return None

    @classmethod
    def is_ipv4(cls, value: str) -> bool:
        try:
            ipaddress.IPv4Address(value)
            return True
        except ValueError:
            return False

    @classmethod
    def is_ipv6(cls, value: str) -> bool:
        try:
            ipaddress.IPv6Address(value)
            return True
        except ValueError:
            return False

    @classmethod
    def from_cidr_or_addr(cls, value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        token = value.strip()
        if token.startswith("[") and "]" in token:
            token = token[1 : token.index("]")]
        token = token.split("%", 1)[0].split("/", 1)[0].strip()
        return cls.normalize_ip(token)

    @classmethod
    def ipv4_from_cidr_or_addr(cls, value: Any) -> str | None:
        ip = cls.from_cidr_or_addr(value)
        return ip if ip and cls.is_ipv4(ip) else None

    @classmethod
    def ipv6_from_cidr_or_addr(cls, value: Any) -> str | None:
        ip = cls.from_cidr_or_addr(value)
        return ip if ip and cls.is_ipv6(ip) else None

    @classmethod
    def ssh_host(cls, ssh: Any) -> str | None:
        """Extract host from `user@host`, `user@host -p 2222`, or bare host."""
        if not isinstance(ssh, str) or not ssh.strip():
            return None
        token = ssh.strip().split()[0]
        if "@" in token:
            token = token.rsplit("@", 1)[-1]
        m = cls._IPV6_BRACKETS.match(token)
        if m:
            token = m.group(1)
        return token or None
