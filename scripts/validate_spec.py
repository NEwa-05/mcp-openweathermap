"""Check a running weather MCP server against the 2026-07-28 spec features it uses.

Usage:
    uv run python scripts/validate_spec.py [http://localhost:8000/mcp]

Runs `server/discover`, `tools/list`, and `subscriptions/listen`. The listen check
only sees an event if the server was started with MCP_SPEC_DEBUG=1; the script then
triggers one itself through /debug/tools-list-changed.
"""

import asyncio
import sys
from urllib.parse import urlsplit

import httpx
from mcp import Client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/mcp"
ORIGIN = "{0.scheme}://{0.netloc}".format(urlsplit(URL))


def check(label: str, ok: bool, detail: object = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label} {detail}".rstrip())
    return ok


async def main() -> int:
    results: list[bool] = []
    async with Client(URL) as client:
        discover = client.session.discover_result
        results.append(check("server/discover answered", discover is not None))
        if discover:
            results.append(
                check(
                    "supports 2026-07-28",
                    "2026-07-28" in discover.supported_versions,
                    discover.supported_versions,
                )
            )
            results.append(
                check(
                    "serverInfo present",
                    bool(
                        discover.meta
                        and "io.modelcontextprotocol/serverInfo" in discover.meta
                    ),
                )
            )
            print(
                f"       capabilities: {discover.capabilities.model_dump(exclude_none=True)}"
            )

        tools = await client.list_tools()
        results.append(
            check(
                "tools/list resultType",
                tools.result_type == "complete",
                tools.result_type,
            )
        )
        results.append(
            check(
                "tools/list ttlMs + cacheScope",
                tools.ttl_ms is not None and tools.cache_scope is not None,
                (tools.ttl_ms, tools.cache_scope),
            )
        )

        async with client.listen(tools_list_changed=True) as sub:
            results.append(
                check(
                    "subscriptions/listen acknowledged", True, f"honored={sub.honored}"
                )
            )
            async with httpx.AsyncClient() as http:
                trigger = await http.post(f"{ORIGIN}/debug/tools-list-changed")
            if trigger.status_code == 200:
                try:
                    async with asyncio.timeout(5):
                        async for event in sub:
                            results.append(
                                check("listen delivered toolsListChanged", True, event)
                            )
                            break
                except TimeoutError:
                    results.append(
                        check("listen delivered toolsListChanged", False, "timed out")
                    )
            else:
                print(
                    "[SKIP] event delivery: start the server with MCP_SPEC_DEBUG=1 to test it"
                )

    return 0 if all(results) else 1


sys.exit(asyncio.run(main()))
