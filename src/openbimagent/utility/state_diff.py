"""Derive S0/S1/S2 revisions and structured candidate-state differences.

The municipal workflow history is append-only. This module reconstructs the
three states that matter for experience extraction without trusting a
client-supplied summary:

* S0: the human draft before the bounded auto-refinement loop;
* S1: the model output after deterministic + visual refinement;
* S2: the human-confirmed final state of the same requirement revision.

Revision grouping follows the workflow semantics: the requirement revision
increments only on a requirement change, so human corrections/preferences in
the same requirement revision stay together.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any


_IDENTITY_FIELDS = {"node_id", "segment_id"}
_HUMAN_SOURCES = {"correction", "preference", "requirement_change"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _entry_requirement_revisions(history: list[dict]) -> list[int]:
    """Assign each history entry its effective requirement revision.

    New history entries persist ``requirement_revision`` directly. Legacy
    entries do not, so replay the workflow rule: a ``save`` with source
    ``requirement_change`` increments the revision after the first save.
    """
    revisions: list[int] = []
    current = 1
    seen_save = False
    for entry in history:
        if not isinstance(entry, dict):
            revisions.append(current)
            continue
        explicit = entry.get("requirement_revision")
        if isinstance(explicit, int) and not isinstance(explicit, bool):
            current = explicit
        elif entry.get("action") == "save" and entry.get("source") == "requirement_change" and seen_save:
            current += 1
        if entry.get("action") == "save":
            seen_save = True
        revisions.append(current)
    return revisions


def _candidate_entries(history: list[dict], indices: list[int]) -> list[tuple[int, dict, dict]]:
    entries: list[tuple[int, dict, dict]] = []
    for index in indices:
        entry = history[index]
        candidate = entry.get("candidate") if isinstance(entry, dict) else None
        if isinstance(candidate, dict):
            entries.append((index, entry, candidate))
    return entries


def _select_s0(entries: list[tuple[int, dict, dict]]) -> tuple[int, dict, dict] | None:
    for item in entries:
        if item[1].get("state_role") == "S0" or item[1].get("action") == "auto_loop_start":
            return item
    for item in entries:
        if item[1].get("action") == "save":
            return item
    return entries[0] if entries else None


def _select_s1(entries: list[tuple[int, dict, dict]]) -> tuple[int, dict, dict] | None:
    for item in entries:
        if item[1].get("state_role") == "S1" or item[1].get("action") == "auto_loop_complete":
            return item
    repairs = [item for item in entries if item[1].get("action") == "repair"]
    if repairs:
        return repairs[-1]
    return entries[0] if entries else None


def _select_s2(
    entries: list[tuple[int, dict, dict]],
    history: list[dict],
    indices: list[int],
) -> tuple[int, dict, dict] | None:
    confirm_indices = [index for index in indices
                       if isinstance(history[index], dict) and history[index].get("action") == "confirm"]
    if confirm_indices:
        confirmed = history[confirm_indices[-1]]
        digest = confirmed.get("candidate_sha256")
        matches = [item for item in entries if _sha256(item[2]) == digest]
        if matches:
            return matches[-1]
        before = [item for item in entries if item[0] <= confirm_indices[-1]]
        if before:
            return before[-1]
    human_saves = [item for item in entries
                   if item[1].get("action") == "save" and item[1].get("source") in _HUMAN_SOURCES]
    if human_saves:
        return human_saves[-1]
    return entries[-1] if entries else None


def _human_sources(
    history: list[dict],
    indices: list[int],
    s1_index: int,
    s2_index: int,
) -> list[str]:
    sources: list[str] = []
    for index in indices:
        if not (s1_index < index <= s2_index):
            continue
        entry = history[index]
        if not isinstance(entry, dict) or entry.get("action") != "save":
            continue
        source = entry.get("source")
        if source in _HUMAN_SOURCES and source not in sources:
            sources.append(source)
    return sources


def derive_revision_three_states(
    history: list[dict],
    target_revision: int | None = None,
) -> dict:
    """Return the S0/S1/S2 candidates for one requirement revision.

    ``target_revision`` selects a requirement revision. For compatibility it
    also accepts a workflow revision when no requirement group matches.
    """
    if not isinstance(history, list):
        raise TypeError("history must be a list")
    effective = _entry_requirement_revisions(history)
    all_indices = [index for index, entry in enumerate(history) if isinstance(entry, dict)]
    if not all_indices:
        return {
            "requirement_revision": target_revision,
            "S0": None, "S1": None, "S2": None,
            "sha256": {"S0": None, "S1": None, "S2": None},
            "indices": {"S0": None, "S1": None, "S2": None},
            "auto_retry_rounds": 0,
            "degradation_rollback": False,
            "human_sources": [],
        }

    if target_revision is None:
        selected = max(effective[index] for index in all_indices)
        indices = [index for index in all_indices if effective[index] == selected]
    else:
        indices = [index for index in all_indices if effective[index] == target_revision]
        if not indices:
            # Accept a workflow revision as a fallback selector.
            indices = [index for index in all_indices if history[index].get("revision") == target_revision]
            if not indices:
                raise ValueError(f"no history entries for revision {target_revision}")
        selected = effective[indices[-1]]

    entries = _candidate_entries(history, indices)
    s0 = _select_s0(entries)
    s1 = _select_s1(entries)
    s2 = _select_s2(entries, history, indices)

    auto_rounds = 0
    rollback = False
    for index in indices:
        entry = history[index]
        if not isinstance(entry, dict):
            continue
        if entry.get("action") == "auto_loop_complete":
            auto_rounds = max(auto_rounds, int(entry.get("auto_retry_rounds") or 0))
            rollback = rollback or bool(entry.get("degradation_rollback"))
        elif entry.get("action") == "auto_loop_rollback":
            rollback = True
    if auto_rounds == 0:
        auto_rounds = sum(
            1 for index in indices
            if isinstance(history[index], dict)
            and history[index].get("action") == "repair"
            and history[index].get("round") is not None
        )

    def role(item: tuple[int, dict, dict] | None) -> dict | None:
        return deepcopy(item[2]) if item is not None else None

    return {
        "requirement_revision": selected,
        "S0": role(s0),
        "S1": role(s1),
        "S2": role(s2),
        "sha256": {
            "S0": _sha256(s0[2]) if s0 is not None else None,
            "S1": _sha256(s1[2]) if s1 is not None else None,
            "S2": _sha256(s2[2]) if s2 is not None else None,
        },
        "indices": {
            "S0": s0[0] if s0 is not None else None,
            "S1": s1[0] if s1 is not None else None,
            "S2": s2[0] if s2 is not None else None,
        },
        "auto_retry_rounds": auto_rounds,
        "degradation_rollback": rollback,
        "human_sources": _human_sources(
            history,
            indices,
            s1[0] if s1 is not None else -1,
            s2[0] if s2 is not None else -1,
        ),
    }


def _index_items(candidate: dict, group: str, id_field: str) -> list[tuple[str, int, dict]]:
    items = candidate.get(group)
    if not isinstance(items, list):
        return []
    indexed: list[tuple[str, int, dict]] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        object_id = item.get(id_field)
        if not isinstance(object_id, str) or not object_id:
            object_id = f"#{index}"
        occurrence = seen.get(object_id, 0)
        seen[object_id] = occurrence + 1
        key = object_id if occurrence == 0 else f"{object_id}#{occurrence}"
        indexed.append((key, index, item))
    return indexed


def _value_delta(old: Any, new: Any) -> float | int | None:
    if isinstance(old, bool) or isinstance(new, bool):
        return None
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        delta = new - old
        if isinstance(delta, float) and (math.isnan(delta) or math.isinf(delta)):
            return None
        return delta
    return None


def compute_candidate_state_diff(cand_a: dict, cand_b: dict) -> list[dict]:
    """Return a deterministic, structured diff from candidate A to candidate B."""
    if not isinstance(cand_a, dict) or not isinstance(cand_b, dict):
        raise TypeError("candidates must be dictionaries")
    diffs: list[dict] = []
    for group, id_field, entity_type in (
        ("nodes", "node_id", "node"),
        ("segments", "segment_id", "segment"),
    ):
        before = {key: (index, item) for key, index, item in _index_items(cand_a, group, id_field)}
        after = {key: (index, item) for key, index, item in _index_items(cand_b, group, id_field)}
        for key in sorted(set(before) | set(after)):
            old_entry = before.get(key)
            new_entry = after.get(key)
            object_id = (new_entry or old_entry)[1].get(id_field) or key
            if old_entry is None:
                path_index = new_entry[0]
                for field in sorted(new_entry[1]):
                    if field in _IDENTITY_FIELDS:
                        continue
                    diffs.append({
                        "entity_type": entity_type, "object_id": object_id,
                        "path": f"/{group}/{path_index}/{field}", "field": field,
                        "old_value": None, "new_value": deepcopy(new_entry[1][field]), "delta": None,
                    })
                continue
            if new_entry is None:
                path_index = old_entry[0]
                for field in sorted(old_entry[1]):
                    if field in _IDENTITY_FIELDS:
                        continue
                    diffs.append({
                        "entity_type": entity_type, "object_id": object_id,
                        "path": f"/{group}/{path_index}/{field}", "field": field,
                        "old_value": deepcopy(old_entry[1][field]), "new_value": None, "delta": None,
                    })
                continue
            path_index = new_entry[0]
            for field in sorted(set(old_entry[1]) | set(new_entry[1])):
                if field in _IDENTITY_FIELDS:
                    continue
                old_value = old_entry[1].get(field)
                new_value = new_entry[1].get(field)
                if _canonical_json(old_value) == _canonical_json(new_value):
                    continue
                diffs.append({
                    "entity_type": entity_type, "object_id": object_id,
                    "path": f"/{group}/{path_index}/{field}", "field": field,
                    "old_value": deepcopy(old_value), "new_value": deepcopy(new_value),
                    "delta": _value_delta(old_value, new_value),
                })
    return diffs
