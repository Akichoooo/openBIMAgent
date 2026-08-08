# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Commands

```bash
# Install dependencies
uv sync

# Run full pipeline
uv run python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md
uv run python -m openbimagent run --playbook domain_packs/single_asset_hero/playbook.md --no-blender --no-hitl --yes

# Subagent Runtime (terminal 1) + IPC controls (terminal 2)
uv run python -m openbimagent runtime-serve --sessions-dir out/sessions --artifacts-dir out/subagents
uv run python -m openbimagent control-write approve <approval_id> --actor-id human:operator --idempotency-key k

# Operator Console (browser-based)
uv run python -m openbimagent operator-console --sessions-dir out/sessions --actor-id human:operator

# Session management
uv run python -m openbimagent sessions
uv run python -m openbimagent tree <session_id> <event_id>
uv run python -m openbimagent export <session_id> [--format jsonl|bimbench]

# Test — run a single file
uv run pytest tests/test_cli.py -q -x
# Test — run matching pattern
uv run pytest tests/ -k "test_compile" -q
# Test — all tests
uv run pytest tests/ -q
# Lint
uv run ruff check src/
```

## Architecture at a Glance

**Agent Core (Python 3.11+, uv)** + **two typed MCP servers** (`blender-mcp`, `vectorworks-mcp`) + **Domain Packs** (vertical packs).

### Pipeline lifecycle

```
Playbook → Clarify(slots) → Planner(Scene Graph IR + PLAN/TODO.md)
  → Schema Gate → Orchestrator(≤4 concurrent subagents)
  → Vision Loop(SCAD quick-check → Blender render-check)
  → Domain Gate(rule evidence) → Deliver(IFC/IDS/ArtifactManifest)
```

### Key files

| Path | Role |
|---|---|
| `src/openbimagent/cli.py` | CLI entry: run / sessions / tree / export / control / runtime-serve / operator-console |
| `src/openbimagent/assembly/pipeline.py` | Pipeline orchestrator (run_pipeline) |
| `src/openbimagent/assembly/batch_executor.py` | Batch execution with approval gates |
| `src/openbimagent/assembly/blender_plan.py` / `vectorworks_plan.py` | Typed host execution plans |
| `src/openbimagent/assembly/semantic_snapshot.py` | Dual-host semantic comparison |
| `src/openbimagent/utility/` | CompiledUtilityIR, solvers (straight-gravity, network, hydraulic, route), rules & rule evidence |
| `src/openbimagent/orchestrator/` | Subagent Runtime, IPC, control plane, approvals, state |
| `src/openbimagent/deliver/` | IFC/IDS delivery, ArtifactManifest, deliver gate |
| `src/openbimagent/vision/` | VLMCritic, SCAD/render loops, rubric, HTML report |
| `src/openbimagent/session/` | Session JSONL store, event schema, checkpoint/resume |
| `src/openbimagent/providers/` | Multi-provider LLM registry (GLM, Gemini, agentrouter, faucet) |
| `src/openbimagent/server/` | M2 SSE + OpenAPI service layer |
| `mcp_servers/blender_mcp/` | Blender MCP server (fork of ahujasid/blender-mcp) |
| `mcp_servers/vectorworks_mcp/` | Vectorworks MCP server (self-developed, file IPC) |
| `domain_packs/{name}/` | Vertical packs: `municipal_utility`, `edo_cyberpunk_district`, `single_asset_hero` |
| `agents/` | 10 subagent role definitions (orchestrator, planner, modeler, critic, etc.) |
| `schemas/` | JSON Schema for every protocol artifact (compiled IR, plans, snapshots, manifests, IPC) |
| `config/models.toml` | Provider/model definitions with API key env vars |
| `docs/architecture/` | Architecture docs, milestone contracts, decisions |

### Milestone structure

- **M0**: Asset assembly pipeline, Blender MCP integration, HITL, session store
- **M1 (G1–G7)**: Dual-host typed delivery — compiled IR → typed plans → semantic comparison → IFC/IDS → RuleEvidence → ArtifactManifest → checkpoint/resume → real host acceptance
- **M1.5 (T1–T7)**: Municipal utility domain — network topology solver, route solver, hydraulic solver, rule evidence, domain gate, offline E2E benchmark
- **M2**: SSE + OpenAPI service layer, pagination, idempotency, payload privacy, authentication

### Key design constraints

- **C2**: LLM outputs semantics (Scene Graph IR), solvers output coordinates (CompiledUtilityIR)
- **C5**: Deliver gate only accepts artifact-manifest-committed outputs
- **Artifact-as-protocol**: Models hand off via versioned, schematized files only
- **Session JSONL tree**: Every event logged; checkpoint/resume via `/tree` fork
- **No LangGraph/CrewAI/AutoGen**: Minimal kernel with ≤8 tools, state externalized to files
- **Domain Pack verticality**: One base + vertical packs; `municipal_utility` is the thesis mainline

### State tracking

The only live status document is `docs/architecture/PROJECT_HANDOFF_STATUS.md`. Always read it at session start for current gate, test evidence, and next action. Do not rely on stale commit messages or historical test numbers.

### Profiles

Set `OPENBIMAGENT_PROFILE` in `.env` to switch provider profiles. The registry reads from `config/models.toml`. Without `.env` the pipeline runs deterministic templates + MockCritic (offline smoke).