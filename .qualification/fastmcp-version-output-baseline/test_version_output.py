"""Native public Client/server regression: exact-version output schemas."""
import asyncio
import pytest
from fastmcp import Client, FastMCP

CASES = ["same-types", "different-types", "compatible-schema", "highest-default", "highest-explicit", "metadata-pin"]

@pytest.mark.parametrize("mode", ["auto", "legacy"])
@pytest.mark.parametrize("case", CASES)
def test_pinned_tool_output(mode, case, record_property):
    async def run():
        server = FastMCP("version-output-baseline")
        @server.tool(name="same", version="1.0")
        def same_v1() -> int:
            return 7
        @server.tool(name="same", version="2.0")
        def same_v2() -> int:
            return 8
        @server.tool(name="value", version="1.0")
        def value_v1() -> int:
            return 7
        @server.tool(name="value", version="2.0")
        def value_v2() -> str:
            return "hello"
        @server.tool(name="shape", version="1.0")
        def shape_v1() -> int:
            return 7
        @server.tool(name="shape", version="2.0")
        def shape_v2() -> dict:
            return {"a": 1}
        async with Client(server, mode=mode) as client:
            record_property("requested_mode", mode)
            record_property("negotiated_protocol", client.protocol_version)
            assert client.protocol_version == ("2026-07-28" if mode == "auto" else "2025-11-25")
            if case == "same-types":
                result = await client.call_tool("same", version="1.0")
                assert result.data == 7 and type(result.data) is int
            elif case == "different-types":
                result = await client.call_tool("value", version="1.0")
                assert result.data == 7 and type(result.data) is int
            elif case == "compatible-schema":
                result = await client.call_tool("shape", version="1.0")
                assert result.data == 7 and type(result.data) is int
            elif case == "highest-default":
                result = await client.call_tool("value")
                assert result.data == "hello" and type(result.data) is str
            elif case == "highest-explicit":
                result = await client.call_tool("value", version="2.0")
                assert result.data == "hello" and type(result.data) is str
            else:
                result = await client.call_tool("value", meta={"fastmcp": {"version": "1.0"}})
                assert result.data == 7 and type(result.data) is int
    asyncio.run(run())
