"""G2 通用 deliver：复用 ArtifactManifest v1.1 的不可变提交与幂等门禁。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openbimagent.orchestrator.actor import ActorRef, ActorType
from openbimagent.orchestrator.artifacts import ArtifactStore, ImmutableArtifactError
from openbimagent.orchestrator.contracts import ArtifactManifest, ArtifactStatus

DELIVERY_AGENT_ID_PREFIX = "delivery"
DELIVERY_MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class DeliveryManifestResult:
    manifest: ArtifactManifest
    manifest_path: Path
    reused: bool


def commit_delivery_manifest(
    *,
    workdir: Path,
    artifacts: list[dict[str, Any]],
    idempotency_key: str,
    domain_gate_status: str,
    request_id: str,
    source_attempt_id: str,
    domain_gate_required: bool = True,
    lineage_id: str | None = None,
    attempt_number: int | None = None,
    resumed_from_request_id: str | None = None,
    generator: ActorRef | None = None,
) -> DeliveryManifestResult:
    """校验源工件并复制到不可变 delivery 目录；同键同语义复用、异义冲突。"""
    root = Path(workdir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"deliver workdir 不存在: {root}")
    allowed_status = "PASS" if domain_gate_required else "SKIPPED"
    if domain_gate_status != allowed_status:
        context = "已声明" if domain_gate_required else "未声明"
        raise ValueError(
            "Domain Gate 状态与交付上下文不一致，禁止完成交付: "
            f"required={domain_gate_required} ({context}) status={domain_gate_status}"
        )
    if not idempotency_key.strip():
        raise ValueError("deliver idempotency_key 不能为空")
    if not artifacts:
        raise ValueError("deliver 至少需要一个工件")

    normalized = [_normalize_artifact(root, item) for item in artifacts]
    normalized.sort(key=lambda item: item["relative_path"])
    semantic_payload = {
        "domain_gate_status": domain_gate_status,
        "source_attempt_id": source_attempt_id,
        "artifacts": [
            {
                key: item[key]
                for key in ("relative_path", "kind", "media_type", "sha256", "dependencies", "status")
            }
            for item in normalized
        ],
    }
    semantic_sha256 = hashlib.sha256(
        json.dumps(
            semantic_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    store = ArtifactStore(root / ".artifacts")
    delivery_agent_id = _delivery_agent_id(idempotency_key)
    manifest_path = store.run_dir(delivery_agent_id) / DELIVERY_MANIFEST_NAME
    if manifest_path.is_file():
        existing = store.load_manifest(manifest_path)
        if existing.idempotency_key != idempotency_key:
            raise ImmutableArtifactError("delivery Manifest 已存在，必须使用原 idempotency_key")
        if existing.semantic_sha256 != semantic_sha256:
            raise ImmutableArtifactError("同一 deliver idempotency_key 对应不同工件语义")
        verify_manifest_files(existing)
        return DeliveryManifestResult(existing, manifest_path, reused=True)

    effective_generator = generator or ActorRef(
        actor_id="agent:orchestrator",
        actor_type=ActorType.AGENT,
        display_name="openBIMAgent orchestrator",
    )
    records_list = []
    for item in normalized:
        destination = store.run_dir(delivery_agent_id) / _delivery_name(item["relative_path"])
        if destination.is_file():
            record = store.record_existing(
                destination,
                kind=item["kind"],
                expected_sha256=item["sha256"],
                media_type=item["media_type"],
                generator=effective_generator,
                source_attempt_id=source_attempt_id,
                dependencies=tuple(item["dependencies"]),
                status=ArtifactStatus(item["status"]),
            )
        else:
            record = store.commit_file(
                delivery_agent_id,
                item["source"],
                name=destination.name,
                kind=item["kind"],
                media_type=item["media_type"],
                generator=effective_generator,
                source_attempt_id=source_attempt_id,
                dependencies=tuple(item["dependencies"]),
                status=ArtifactStatus(item["status"]),
            )
        records_list.append(record)
    records = tuple(records_list)
    manifest, path = store.write_manifest(
        request_id=request_id,
        agent_id=delivery_agent_id,
        records=records,
        generator=effective_generator,
        lineage_id=lineage_id,
        attempt_number=attempt_number,
        resumed_from_request_id=resumed_from_request_id,
        status=ArtifactStatus.COMPLETED,
        idempotency_key=idempotency_key,
        semantic_sha256=semantic_sha256,
        domain_gate_status=domain_gate_status,
    )
    verify_manifest_files(manifest)
    return DeliveryManifestResult(manifest, path, reused=False)


def verify_manifest_files(manifest: ArtifactManifest) -> None:
    """重算 Manifest 所有本地工件的 size/hash；任何漂移失败关闭。"""
    for record in manifest.records:
        path = Path(record.path)
        if not path.is_file():
            raise ImmutableArtifactError(f"Manifest 工件不存在: {path}")
        content = path.read_bytes()
        if len(content) != record.size_bytes:
            raise ImmutableArtifactError(f"Manifest 工件 size 漂移: {path}")
        if hashlib.sha256(content).hexdigest() != record.sha256:
            raise ImmutableArtifactError(f"Manifest 工件 hash 漂移: {path}")


def _delivery_agent_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:24]
    return f"{DELIVERY_AGENT_ID_PREFIX}-{digest}"


def _delivery_name(relative_path: str) -> str:
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:12]
    return f"{digest}-{Path(relative_path).name}"


def _normalize_artifact(root: Path, item: dict[str, Any]) -> dict[str, Any]:
    allowed = {"path", "kind", "media_type", "sha256", "dependencies", "status"}
    unknown = set(item) - allowed
    if unknown:
        raise ValueError(f"deliver 工件包含未知字段: {sorted(unknown)}")
    declared = str(item.get("path") or "")
    if not declared:
        raise ValueError("deliver 工件 path 不能为空")
    relative = Path(declared)
    if relative.is_absolute():
        raise ValueError(f"deliver 工件 path 必须是 workdir 内相对路径: {declared!r}")
    source = (root / relative).resolve()
    try:
        portable = source.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"deliver 工件路径越界 workdir: {declared!r}") from exc
    if not source.is_file():
        raise FileNotFoundError(f"deliver 工件不存在或不是文件: {declared!r}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    declared_hash = item.get("sha256")
    if declared_hash is not None and declared_hash != digest:
        raise ValueError(f"deliver 工件 hash 与声明不一致: {declared!r}")
    status = str(item.get("status") or ArtifactStatus.COMPLETED.value)
    if status != ArtifactStatus.COMPLETED.value:
        raise ValueError(f"交付完成态只接受 completed 工件: {declared!r} status={status!r}")
    dependencies = tuple(str(value) for value in item.get("dependencies") or ())
    if len(dependencies) != len(set(dependencies)):
        raise ValueError(f"deliver 工件 dependencies 不能重复: {declared!r}")
    return {
        "source": source,
        "relative_path": portable,
        "kind": str(item.get("kind") or "output"),
        "media_type": str(item.get("media_type") or "application/octet-stream"),
        "sha256": digest,
        "dependencies": dependencies,
        "status": status,
    }


__all__ = [
    "DeliveryManifestResult",
    "commit_delivery_manifest",
    "verify_manifest_files",
]
