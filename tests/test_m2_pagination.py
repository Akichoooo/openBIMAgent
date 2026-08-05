"""M2 pre-G7 只读列表分页与透明 cursor 失败关闭测试。"""

from __future__ import annotations

import pytest

from openbimagent.schema_gate.gate import validate_artifact
from openbimagent.server.contracts import M2ErrorCode
from openbimagent.server.pagination import (
    M2_PAGE_LIMIT_DEFAULT,
    M2_PAGE_LIMIT_MAX,
    M2_PAGINATION_CURSOR_AUTHENTICATED,
    M2_PAGINATION_POLICY_VERSION,
    M2PaginationError,
    decode_m2_page_cursor,
    paginate_m2_items,
)


def _items() -> tuple[dict[str, object], ...]:
    return tuple({"session_id": f"session-{index}", "rank": index} for index in range(5))


def test_page_cursor_round_trip_is_versioned_bounded_and_schema_valid() -> None:
    first = paginate_m2_items(_items(), resource="sessions", scope={}, limit=2)
    assert M2_PAGINATION_POLICY_VERSION == "0.1"
    assert M2_PAGE_LIMIT_DEFAULT == 50
    assert M2_PAGE_LIMIT_MAX == 100
    assert M2_PAGINATION_CURSOR_AUTHENTICATED is False
    assert [item["session_id"] for item in first.items] == ["session-0", "session-1"]
    assert first.count == 2
    assert first.has_more is True
    assert first.next_cursor is not None
    assert len(first.next_cursor) <= 1024

    cursor = decode_m2_page_cursor(first.next_cursor)
    assert cursor.resource == "sessions"
    assert cursor.offset == 2
    assert validate_artifact("m2_page_cursor", cursor.model_dump(mode="json")) == []
    with pytest.raises(M2PaginationError):
        decode_m2_page_cursor(first.next_cursor + "=")

    second = paginate_m2_items(_items(), resource="sessions", scope={}, limit=2, cursor=first.next_cursor)
    assert [item["session_id"] for item in second.items] == ["session-2", "session-3"]
    final = paginate_m2_items(_items(), resource="sessions", scope={}, limit=2, cursor=second.next_cursor)
    assert [item["session_id"] for item in final.items] == ["session-4"]
    assert final.has_more is False
    assert final.next_cursor is None


def test_cursor_corruption_cross_scope_and_cross_resource_reuse_fail_closed() -> None:
    first = paginate_m2_items(_items(), resource="sessions", scope={"owner": "local"}, limit=2)
    assert first.next_cursor is not None
    replacement = "A" if first.next_cursor[-1] != "A" else "B"
    corrupted = first.next_cursor[:-1] + replacement

    with pytest.raises(M2PaginationError) as corrupt_error:
        paginate_m2_items(_items(), resource="sessions", scope={"owner": "local"}, cursor=corrupted)
    assert corrupt_error.value.code is M2ErrorCode.INVALID_REQUEST

    with pytest.raises(M2PaginationError) as scope_error:
        paginate_m2_items(_items(), resource="sessions", scope={"owner": "other"}, cursor=first.next_cursor)
    assert scope_error.value.code is M2ErrorCode.INVALID_REQUEST

    with pytest.raises(M2PaginationError) as resource_error:
        paginate_m2_items(_items(), resource="attempts", scope={"owner": "local"}, cursor=first.next_cursor)
    assert resource_error.value.code is M2ErrorCode.INVALID_REQUEST


def test_cursor_snapshot_drift_expires_instead_of_skipping_or_duplicating_items() -> None:
    first = paginate_m2_items(_items(), resource="sessions", scope={}, limit=2)
    drifted = (*_items(), {"session_id": "session-new", "rank": 5})
    with pytest.raises(M2PaginationError) as error:
        paginate_m2_items(drifted, resource="sessions", scope={}, limit=2, cursor=first.next_cursor)
    assert error.value.code is M2ErrorCode.REPLAY_CURSOR_EXPIRED


@pytest.mark.parametrize("limit", [True, 0, -1, 101, 1000])
def test_page_limit_outside_explicit_budget_is_rejected(limit: int) -> None:
    with pytest.raises(M2PaginationError) as error:
        paginate_m2_items(_items(), resource="sessions", scope={}, limit=limit)
    assert error.value.code is M2ErrorCode.INVALID_REQUEST
