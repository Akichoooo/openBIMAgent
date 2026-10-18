"""IDS 1.0 XSD must compile offline from pinned local W3C dependencies."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys

import pytest


def test_w3c_xsd_snapshots_match_pinned_hashes() -> None:
    from openbimagent.deliver import ifc_ids

    for url, (path, expected_sha256) in ifc_ids.W3C_XSD_FILES.items():
        assert path.is_file(), url
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256, url


def test_tampered_w3c_dependency_is_rejected(tmp_path, monkeypatch) -> None:
    from openbimagent.deliver import ifc_ids

    tampered = tmp_path / "XMLSchema.xsd"
    tampered.write_bytes(b"<schema/>")
    monkeypatch.setitem(
        ifc_ids.W3C_XSD_FILES,
        "http://www.w3.org/2001/XMLSchema.xsd",
        (tampered, "0" * 64),
    )
    ifc_ids._ids_xml_schema.cache_clear()
    try:
        with pytest.raises(ValueError, match="摘要漂移"):
            ifc_ids._ids_xml_schema()
    finally:
        ifc_ids._ids_xml_schema.cache_clear()


def test_ids_xsd_compiles_in_fresh_process_with_broken_network() -> None:
    env = {
        **os.environ,
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "http_proxy": "http://127.0.0.1:9",
        "https_proxy": "http://127.0.0.1:9",
    }
    code = (
        "from openbimagent.deliver.ifc_ids import _ids_xml_schema\n"
        "_ids_xml_schema()\n"
        "print('compiled-offline')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "compiled-offline" in result.stdout
