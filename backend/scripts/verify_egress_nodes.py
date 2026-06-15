from __future__ import annotations

import sys

import httpx

from app.services.strategy_fanout import configured_egress_nodes


def main() -> int:
    nodes = configured_egress_nodes()
    if not nodes:
        print("No egress nodes are configured.")
        return 1

    failures = 0
    for node in nodes:
        expected_ip = node["public_ip"]
        try:
            with httpx.Client(proxy=node["proxy_url"], timeout=10) as client:
                response = client.get("https://api.ipify.org?format=json")
                response.raise_for_status()
                observed_ip = str(response.json().get("ip") or "").strip()
        except Exception as exc:
            failures += 1
            print(f"{expected_ip}: verification failed ({type(exc).__name__})")
            continue

        matches = observed_ip == expected_ip
        failures += int(not matches)
        print(f"{expected_ip}: observed={observed_ip} match={str(matches).lower()}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
