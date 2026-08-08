"""M2 P7 远程 Playbook 供应链安全测试。"""

from __future__ import annotations

import hashlib
import json

import pytest

from openbimagent.server.remote_playbook import (
    M2RemotePlaybookError,
    M2RemotePlaybookRef,
    restrict_playbook_permissions,
    validate_remote_playbook_url,
    verify_playbook_content,
)


def test_valid_https_url_passes() -> None:
    ref = M2RemotePlaybookRef(
        url="https://playbooks.example.com/municipal/playbook.json",
        sha256="a" * 64,
        allowed_domains=("example.com",),
    )
    validate_remote_playbook_url(ref)


def test_http_url_rejected() -> None:
    ref = M2RemotePlaybookRef(
        url="http://playbooks.example.com/playbook.json",
        sha256="a" * 64,
    )
    with pytest.raises(M2RemotePlaybookError, match="必须使用 HTTPS"):
        validate_remote_playbook_url(ref)


def test_domain_not_in_whitelist() -> None:
    ref = M2RemotePlaybookRef(
        url="https://evil.com/playbook.json",
        sha256="a" * 64,
        allowed_domains=("example.com",),
    )
    with pytest.raises(M2RemotePlaybookError, match="不在授权白名单"):
        validate_remote_playbook_url(ref)


def test_content_sha256_verification() -> None:
    content = json.dumps({"name": "test"}).encode("utf-8")
    sha256 = hashlib.sha256(content).hexdigest()
    ref = M2RemotePlaybookRef(
        url="https://example.com/playbook.json",
        sha256=sha256,
    )
    result = verify_playbook_content(content, ref)
    assert result["name"] == "test"


def test_content_sha256_mismatch() -> None:
    content = b"some content"
    ref = M2RemotePlaybookRef(
        url="https://example.com/playbook.json",
        sha256="a" * 64,
    )
    with pytest.raises(M2RemotePlaybookError, match="SHA-256 不匹配"):
        verify_playbook_content(content, ref)


def test_restrict_illegal_agent_name() -> None:
    with pytest.raises(M2RemotePlaybookError, match="非法角色名"):
        restrict_playbook_permissions({"agents": {"../../evil": {}}})


def test_restrict_illegal_target() -> None:
    with pytest.raises(M2RemotePlaybookError, match="非法宿主目标"):
        restrict_playbook_permissions({"targets": ["blender", "unknown_host"]})


def test_restrict_valid_playbook_passes() -> None:
    result = restrict_playbook_permissions({
        "agents": {"modeler": {"model": "gemini-2.0"}},
        "targets": ["blender"],
    })
    assert result["targets"] == ["blender"]