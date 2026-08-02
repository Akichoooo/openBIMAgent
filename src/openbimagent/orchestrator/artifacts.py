"""Subagent Runtime v1 的不可变工件存储。

每次运行使用唯一 agent_id 目录；文件先写临时文件再原子 rename，已存在目标拒绝覆盖。
manifest 记录绝对路径、字节数和 SHA-256，父代理只拿 manifest/路径，不读取 child 过程。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from openbimagent.orchestrator.actor import ActorRef
from openbimagent.orchestrator.contracts import (
    ArtifactManifest,
    ArtifactRecord,
    ArtifactStatus,
)
from openbimagent.schema_gate.gate import gate_or_fix
from openbimagent.session.schema import uuid7

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class ImmutableArtifactError(RuntimeError):
    """尝试覆盖不可变工件或提交无效工件时抛出。"""


class ArtifactStore:
    """单个 Runtime 共享的工件根目录。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, agent_id: str) -> Path:
        path = self.root / _safe_component(agent_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def commit_text(self, agent_id: str, *, name: str, kind: str, content: str) -> ArtifactRecord:
        return self.commit_bytes(agent_id, name=name, kind=kind, content=content.encode("utf-8"))

    def commit_file(
        self,
        agent_id: str,
        source: Path,
        *,
        name: str | None = None,
        kind: str = "output",
        media_type: str | None = None,
        generator: ActorRef | None = None,
        source_attempt_id: str | None = None,
        dependencies: tuple[str, ...] = (),
        status: ArtifactStatus = ArtifactStatus.COMPLETED,
    ) -> ArtifactRecord:
        source = Path(source).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"子代理工件不存在: {source}")
        return self.commit_bytes(
            agent_id,
            name=name or source.name,
            kind=kind,
            content=source.read_bytes(),
            media_type=media_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            generator=generator,
            source_attempt_id=source_attempt_id,
            dependencies=dependencies,
            status=status,
        )

    def commit_bytes(
        self,
        agent_id: str,
        *,
        name: str,
        kind: str,
        content: bytes,
        media_type: str | None = None,
        generator: ActorRef | None = None,
        source_attempt_id: str | None = None,
        dependencies: tuple[str, ...] = (),
        status: ArtifactStatus = ArtifactStatus.COMPLETED,
    ) -> ArtifactRecord:
        run_dir = self.run_dir(agent_id)
        safe_name = _safe_component(name)
        destination = run_dir / safe_name
        _atomic_create(destination, content)
        return ArtifactRecord(
            artifact_id=str(uuid7()),
            kind=kind,
            path=str(destination),
            relative_path=destination.relative_to(self.root).as_posix(),
            media_type=media_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            immutable=True,
            generator=generator,
            source_attempt_id=source_attempt_id,
            dependencies=dependencies,
            status=status,
        )

    def record_existing(
        self,
        path: Path,
        *,
        kind: str,
        expected_sha256: str | None = None,
        media_type: str | None = None,
        generator: ActorRef | None = None,
        source_attempt_id: str | None = None,
        dependencies: tuple[str, ...] = (),
        status: ArtifactStatus = ArtifactStatus.COMPLETED,
    ) -> ArtifactRecord:
        """为已原子发布但尚未写入 Manifest 的文件重建记录（恢复崩溃窗口）。"""
        source = Path(path).resolve()
        if not source.is_file() or self.root not in source.parents:
            raise ImmutableArtifactError(f"恢复工件不在受控目录或不存在: {source}")
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ImmutableArtifactError(f"恢复工件 hash 与源语义不一致: {source}")
        return ArtifactRecord(
            artifact_id=str(uuid7()),
            kind=kind,
            path=str(source),
            relative_path=source.relative_to(self.root).as_posix(),
            media_type=media_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            sha256=digest,
            size_bytes=len(content),
            immutable=True,
            generator=generator,
            source_attempt_id=source_attempt_id,
            dependencies=dependencies,
            status=status,
        )

    def load_manifest(self, path: Path) -> ArtifactManifest:
        """加载并校验既有不可变 Manifest。"""
        source = Path(path).resolve()
        if not source.is_file() or self.root not in source.parents:
            raise ImmutableArtifactError(f"Manifest 不在受控目录或不存在: {source}")
        data = json.loads(source.read_text(encoding="utf-8"))
        gate_or_fix("artifact_manifest", data)
        return ArtifactManifest.model_validate(data)

    def write_manifest(
        self,
        *,
        request_id: str,
        agent_id: str,
        records: tuple[ArtifactRecord, ...],
        name: str = "manifest.json",
        generator: ActorRef | None = None,
        lineage_id: str | None = None,
        attempt_number: int | None = None,
        resumed_from_request_id: str | None = None,
        status: ArtifactStatus = ArtifactStatus.COMPLETED,
        idempotency_key: str | None = None,
        semantic_sha256: str | None = None,
        domain_gate_status: str | None = None,
    ) -> tuple[ArtifactManifest, Path]:
        manifest = ArtifactManifest(
            request_id=request_id,
            agent_id=agent_id,
            created_at=datetime.now(timezone.utc),
            records=records,
            generator=generator,
            lineage_id=lineage_id,
            attempt_number=attempt_number,
            resumed_from_request_id=resumed_from_request_id,
            status=status,
            idempotency_key=idempotency_key,
            semantic_sha256=semantic_sha256,
            domain_gate_status=domain_gate_status,
        )
        data = manifest.model_dump(mode="json")
        gate_or_fix("artifact_manifest", data)
        path = self.run_dir(agent_id) / _safe_component(name)
        encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        _atomic_create(path, encoded)
        return manifest, path


def _safe_component(value: str) -> str:
    cleaned = _SAFE_NAME.sub("_", Path(value).name).strip("._")
    if not cleaned:
        raise ImmutableArtifactError(f"无效工件名: {value!r}")
    return cleaned[:180]


def _atomic_create(path: Path, content: bytes) -> None:
    """在同目录原子创建 path；目标存在时失败，不允许覆盖不可变工件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ImmutableArtifactError(f"不可变工件已存在，拒绝覆盖: {path}")
    temp = path.with_name(f".{path.name}.{uuid7()}.tmp")
    published = False
    try:
        with temp.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
            published = True
        except FileExistsError as exc:
            raise ImmutableArtifactError(f"不可变工件已存在，拒绝覆盖: {path}") from exc
        except OSError as exc:
            raise ImmutableArtifactError(f"不可变工件原子发布失败: {path}: {exc}") from exc
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                # 目标已通过 hard-link 原子发布后，Windows 沙箱/安全软件可能暂时拒绝清理临时链接。
                # 清理失败不能把已经成功、可校验的不可变工件改判为提交失败。
                if not published:
                    raise


__all__ = ["ArtifactStore", "ImmutableArtifactError"]
