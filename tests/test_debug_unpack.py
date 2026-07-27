"""调试 _unpack_mcp_result 在 pytest 中的行为。"""

import json


def test_debug_unpack_mcp_result():
    from test_blender_client import _FakeCallToolResult
    from openbimagent.mcp_clients.blender import _unpack_mcp_result

    r = _FakeCallToolResult(
        structured_content={"result": '{"pong": true, "blender_version": "5.2.0 LTS"}'}
    )
    sc = r.structured_content
    inner = sc.get("result")
    print(f"\ninner repr = {inner!r}")
    print(f"inner type = {type(inner)}")
    try:
        parsed = json.loads(inner)
        print(f"parsed = {parsed}")
        print(f"is dict = {isinstance(parsed, dict)}")
    except json.JSONDecodeError as e:
        print(f"JSONDecodeError: {e}")

    out = _unpack_mcp_result(r, "ping")
    print(f"out = {out}")
    assert out.get("pong") is True, f"expected pong=True, got {out}"
