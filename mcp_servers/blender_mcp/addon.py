# OPENBIMAGENT fork of ahujasid/blender-mcp addon.py
# UPSTREAM: https://github.com/ahujasid/blender-mcp @ da4e16d2069ce5154eaa2535bf995e843caf5c73 (v1.6.0)
# Pristine baseline: ./vendor/addon.py
# Every fork modification is marked with an "OPENBIMAGENT (<item>):" comment.
# Items: (a) telemetry hard-off, (b) headless allowed, (c) snapshot+AST allowlist,
#        (d) tool slimming + batch_render/camera_turntable/camera_path_render,
#        (e) ping/health, (f) non-black screenshot assertion,
#        (g) editable-scope lock, (h) describe_capabilities,
#        (5.2) Blender 5.2 compat (engine enum probe, gpu.init guard).
#
# Removed upstream code (item a/d): Polyhaven / Sketchfab / Hyper3D-Rodin /
# Hunyuan3D integrations, all API-key preferences and operators, the `requests`
# dependency (not bundled with Blender's embedded Python 3.13 -> the upstream
# addon does not even import on a factory Blender install).

import ast
import bpy
import bmesh  # noqa: F401  (exported into the execute_code sandbox namespace)
import mathutils
import json
import math  # noqa: F401  (exported into the execute_code sandbox namespace)
import threading
import socket
import time
import queue
import tempfile
import traceback
import os
import io
import hashlib
import importlib.util
from pathlib import Path
from datetime import datetime
from contextlib import redirect_stdout

# OPENBIMAGENT M1: Blender --python does not guarantee that this script's
# directory is on sys.path. Load the adjacent self-contained adapter by its
# absolute file path instead of relying on ambient module-search state.
_TYPED_PLAN_PATH = Path(__file__).resolve().with_name("typed_plan.py")
_TYPED_PLAN_SPEC = importlib.util.spec_from_file_location(
    "openbimagent_blender_typed_plan",
    _TYPED_PLAN_PATH,
)
if _TYPED_PLAN_SPEC is None or _TYPED_PLAN_SPEC.loader is None:
    raise ImportError(f"cannot load typed Blender adapter: {_TYPED_PLAN_PATH}")
_TYPED_PLAN_MODULE = importlib.util.module_from_spec(_TYPED_PLAN_SPEC)
_TYPED_PLAN_SPEC.loader.exec_module(_TYPED_PLAN_MODULE)
execute_typed_plan = _TYPED_PLAN_MODULE.execute_typed_plan

bl_info = {
    "name": "Blender MCP (openBIMAgent fork)",
    "author": "BlenderMCP / openBIMAgent",
    "version": (1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "openBIMAgent fork of blender-mcp: headless-capable, sandboxed, scope-locked",
    "category": "Interface",
}

# ---------------------------------------------------------------------------
# OPENBIMAGENT: fork identity / capability metadata (item h)
# ---------------------------------------------------------------------------
FORK_VERSION = "1.0.0-m0"
UPSTREAM_REPO = "https://github.com/ahujasid/blender-mcp"
UPSTREAM_COMMIT = "da4e16d2069ce5154eaa2535bf995e843caf5c73"
UPSTREAM_VERSION = "1.6.0"

# OPENBIMAGENT (a): telemetry hard-off. No env var, no preference, no consent
# dialog can re-enable it; this constant is the single source of truth.
TELEMETRY_ENABLED = False

# OPENBIMAGENT (f): mean-luminance threshold under which a screenshot/render is
# considered black (0..1 display-referred). Spike baseline: 0.282 for a lit scene.
BLACK_THRESHOLD = 0.01

# OPENBIMAGENT (g): objects whose name starts with this prefix are created by
# the server itself (temp cameras/lights) and are exempt from the scope lock.
TEMP_PREFIX = "__OBMCP_"

# OPENBIMAGENT (c): AST allowlist for execute_code.
ALLOWED_IMPORT_ROOTS = {"bpy", "bmesh", "mathutils", "math"}
BANNED_BUILTIN_NAMES = {
    "open", "exec", "eval", "__import__", "compile", "globals", "locals",
    "vars", "dir", "getattr", "setattr", "delattr", "breakpoint", "exit",
    "quit", "input", "memoryview", "help",
}

# OPENBIMAGENT (5.2): render-engine preference order. 5.2 removed
# BLENDER_EEVEE_NEXT again; we probe the enum instead of hardcoding.
ENGINE_PREFERENCE = ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH", "CYCLES")

KNOWN_ISSUES = [
    "Blender 5.2: render-engine enum is ('BLENDER_EEVEE','BLENDER_WORKBENCH','CYCLES'); "
    "BLENDER_EEVEE_NEXT (4.x name) does NOT exist and raises TypeError if assigned. "
    "This fork probes bpy.types.RenderSettings.bl_rna instead of hardcoding.",
    "Blender 5.2: gpu.types.GPUOffScreen requires gpu.init() first in background mode "
    "(SystemError otherwise); 4.x has no gpu.init. Fork guards with hasattr(gpu,'init').",
    "Blender background mode has no View3D region, so draw_view3d / viewport capture "
    "can never work headless; get_viewport_screenshot falls back to "
    "bpy.ops.render.render(write_still=True) (method='render_fallback').",
    "Blender 5.2 embeds Python 3.13 (4.x: 3.11); vendored binary deps must be rebuilt. "
    "The fork drops the `requests` dependency entirely.",
    "execute_code sandbox: AST allowlist (imports limited to bpy/bmesh/mathutils/math; "
    "open/exec/eval/__import__/getattr/dunder access banned). bpy.ops.wm.* file "
    "operations remain reachable by design (snapshot/rollback need them).",
    "Scope lock compares per-object fingerprints (transform, mesh vertex hash, "
    "modifiers, materials, visibility). Geometry-node side effects that only change "
    "evaluated (not base) mesh data are not fingerprinted.",
    "First EEVEE render in a session compiles shaders and can take >15s; client "
    "timeouts must be generous (server default 180s, env OPENBIMAGENT_BLENDER_TIMEOUT).",
]

TOOL_MANIFEST = [
    {"name": "ping", "since": "fork", "desc": "Health check: pong + Blender/fork versions."},
    {"name": "describe_capabilities", "since": "fork", "desc": "Server/Blender versions, tool list, limits, known issues."},
    {"name": "get_scene_info", "since": "upstream", "desc": "Scene name, object count, first 10 objects."},
    {"name": "get_object_info", "since": "upstream", "desc": "Transform, visibility, materials, mesh stats, AABB of one object."},
    {"name": "get_viewport_screenshot", "since": "upstream+fork(f)", "desc": "Viewport capture (GUI) or render fallback (headless); non-black asserted."},
    {"name": "execute_code", "since": "upstream+fork(c,g)", "desc": "Run Python in Blender. AST-allowlisted, auto-snapshot, scope-locked with rollback."},
    {"name": "execute_plan", "since": "fork(m1)", "desc": "Execute an approved typed municipal plan with controlled save and receipts."},
    {"name": "batch_render", "since": "fork", "desc": "Render one still per named camera."},
    {"name": "camera_turntable", "since": "fork", "desc": "Orbit a temp camera around a target and render N frames."},
    {"name": "camera_path_render", "since": "fork", "desc": "Move a temp camera through waypoints and render each frame."},
    {"name": "set_editable_scope", "since": "fork", "desc": "Whitelist object names / collections that execute_code may touch."},
    {"name": "get_editable_scope", "since": "fork", "desc": "Return the current scope-lock configuration."},
    {"name": "restore_snapshot", "since": "fork", "desc": "Reload a pre-execution .blend snapshot (default: latest)."},
    {"name": "get_telemetry_consent", "since": "fork(a)", "desc": "Always consent=False (telemetry hard-disabled)."},
]


def log(msg):
    """OPENBIMAGENT: flushed printing so headless stdout is usable as a log."""
    print(f"[BlenderMCP-fork] {msg}", flush=True)


# ---------------------------------------------------------------------------
# OPENBIMAGENT (c): AST allowlist
# ---------------------------------------------------------------------------
class SandboxViolation(Exception):
    pass


def validate_code_ast(code: str) -> list:
    """Return a list of violation strings; empty list means the code is allowed.

    Rules:
    - imports limited to roots in ALLOWED_IMPORT_ROOTS (no os/sys/subprocess/
      socket/requests/urllib/shutil/pathlib/io/importlib/ctypes/...);
    - no relative imports;
    - banned builtins (open/exec/eval/__import__/compile/getattr/...);
    - no dunder attribute or name access (blocks __class__/__subclasses__ escapes);
    - no `while True` guard-rail: not enforced (renders are legitimately long).
    """
    violations = []
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        return [f"SyntaxError: {e}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    violations.append(
                        f"import of module '{alias.name}' is not allowed "
                        f"(allowed roots: {sorted(ALLOWED_IMPORT_ROOTS)})")
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                violations.append("relative imports are not allowed")
            elif node.module is None:
                violations.append("import-from without module is not allowed")
            else:
                root = node.module.split(".")[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    violations.append(
                        f"import from module '{node.module}' is not allowed "
                        f"(allowed roots: {sorted(ALLOWED_IMPORT_ROOTS)})")
        elif isinstance(node, ast.Name):
            if node.id in BANNED_BUILTIN_NAMES:
                violations.append(f"use of banned builtin/name '{node.id}'")
            elif node.id.startswith("__") and node.id.endswith("__"):
                violations.append(f"dunder name access '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__"):
                violations.append(f"dunder attribute access '.{node.attr}' is not allowed")
    # de-duplicate while preserving order
    seen = set()
    uniq = []
    for v in violations:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


# ---------------------------------------------------------------------------
# OPENBIMAGENT (5.2/f): render helpers
# ---------------------------------------------------------------------------
def probe_render_engines() -> list:
    """Return the list of legal render-engine identifiers for this Blender build.

    OPENBIMAGENT (5.2): the bl_rna enum_items of a dynamic enum under-report
    addon-registered engines (background factory startup shows only
    BLENDER_EEVEE). Assignment probing is definitive: a TypeError means the
    engine id is not legal on this build (this is how the spike discovered
    that 5.2 dropped BLENDER_EEVEE_NEXT again).
    """
    candidates = ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH", "CYCLES"]
    try:
        scene = bpy.context.scene
        if scene is None:
            raise RuntimeError("no scene")
        orig = scene.render.engine
        legal = []
        for cand in candidates:
            try:
                scene.render.engine = cand
                legal.append(cand)
            except TypeError:
                pass
        try:
            scene.render.engine = orig
        except TypeError:
            pass
        return legal
    except Exception as e:
        log(f"engine probe failed ({e}); assuming EEVEE-only")
        return ["BLENDER_EEVEE"]


def pick_render_engine(preferred: str = None) -> str:
    """Pick a legal engine: explicit preference if valid, else ENGINE_PREFERENCE order."""
    legal = probe_render_engines()
    if preferred:
        if preferred in legal:
            return preferred
        raise ValueError(f"render engine '{preferred}' not legal on this build; legal: {legal}")
    for cand in ENGINE_PREFERENCE:
        if cand in legal:
            return cand
    return legal[0]


def gpu_init_guard() -> bool:
    """OPENBIMAGENT (5.2): call gpu.init() when the build provides it (5.x)."""
    try:
        import gpu
        if hasattr(gpu, "init"):
            gpu.init()
        return True
    except Exception as e:
        log(f"gpu.init guard failed: {e}")
        return False


def image_file_brightness(filepath: str) -> float:
    """Mean display-referred luminance (0..1) of an image file, via Blender's loader."""
    import numpy as np
    img = bpy.data.images.load(filepath, check_existing=False)
    try:
        w, h = img.size
        if w == 0 or h == 0:
            return 0.0
        arr = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(arr)
        rgba = arr.reshape(-1, 4)
        lum = (0.2126 * rgba[:, 0] + 0.7152 * rgba[:, 1] + 0.0722 * rgba[:, 2]).mean()
        return float(lum)
    finally:
        bpy.data.images.remove(img)


def _scene_bbox():
    """World-space bbox over all visible mesh objects; None if scene has no meshes."""
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    found = False
    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH' or obj.name.startswith(TEMP_PREFIX):
            continue
        found = True
        for corner in obj.bound_box:
            wc = obj.matrix_world @ mathutils.Vector(corner)
            for i in range(3):
                mins[i] = min(mins[i], wc[i])
                maxs[i] = max(maxs[i], wc[i])
    if not found:
        return None
    return mins, maxs


def _push_render_state(scene):
    return {
        "camera": scene.camera,
        "engine": scene.render.engine,
        "res_x": scene.render.resolution_x,
        "res_y": scene.render.resolution_y,
        "res_pct": scene.render.resolution_percentage,
        "filepath": scene.render.filepath,
        "file_format": scene.render.image_settings.file_format,
    }


def _pop_render_state(scene, state):
    scene.camera = state["camera"]
    try:
        scene.render.engine = state["engine"]
    except TypeError:
        pass
    scene.render.resolution_x = state["res_x"]
    scene.render.resolution_y = state["res_y"]
    scene.render.resolution_percentage = state["res_pct"]
    scene.render.filepath = state["filepath"]
    scene.render.image_settings.file_format = state["file_format"]


class BlenderMCPServer:
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None
        # OPENBIMAGENT (b): in background mode commands cannot go through
        # bpy.app.timers (no event loop); they are queued here and pumped by
        # the main thread in run_headless_forever().
        self._bg_queue = queue.Queue()
        # OPENBIMAGENT (g): None = unlocked; otherwise {"objects": set, "collections": set}
        self.editable_scope = None
        # OPENBIMAGENT (c): snapshot bookkeeping
        self.snapshot_dir = os.getenv(
            "OPENBIMAGENT_SNAPSHOT_DIR",
            os.path.join(tempfile.gettempdir(), "openbimagent_blender", "snapshots"))
        self.snapshots = []  # chronological list of snapshot paths
        self.last_snapshot = None

    # ------------------------------------------------------------------
    # OPENBIMAGENT (b): headless-allowed start()
    # ------------------------------------------------------------------
    def start(self):
        # OPENBIMAGENT (b): upstream refused to start when bpy.app.background,
        # claiming "commands would never execute". True only for the timer-based
        # dispatch; the fork dispatches via _bg_queue pumped on the main thread,
        # so `blender -b --python addon.py` serves commands fine.
        if self.running:
            log("server already running")
            return

        self.running = True
        os.makedirs(self.snapshot_dir, exist_ok=True)

        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)

            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            log(f"server started on {self.host}:{self.port} "
                f"(background={bpy.app.background}, blender={bpy.app.version_string})")
        except Exception as e:
            log(f"failed to start server: {e}")
            self.stop()

    def stop(self):
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except Exception:
                pass
            self.server_thread = None
        log("server stopped")

    def _server_loop(self):
        """Main accept loop in a separate thread (upstream, unchanged logic)."""
        log("server thread started")
        self.socket.settimeout(1.0)

        while self.running:
            try:
                try:
                    client, address = self.socket.accept()
                    log(f"connected to client: {address}")
                    client_thread = threading.Thread(target=self._handle_client, args=(client,))
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    log(f"error accepting connection: {e}")
                    time.sleep(0.5)
            except Exception as e:
                log(f"error in server loop: {e}")
                if not self.running:
                    break
                time.sleep(0.5)
        log("server thread stopped")

    def _handle_client(self, client):
        """Handle a connected client (upstream, with OPENBIMAGENT (b) background dispatch)."""
        log("client handler started")
        client.settimeout(None)
        buffer = b''

        try:
            while self.running:
                try:
                    data = client.recv(65536)
                    if not data:
                        log("client disconnected")
                        break

                    buffer += data
                    try:
                        command = json.loads(buffer.decode('utf-8'))
                        buffer = b''

                        if bpy.app.background:
                            # OPENBIMAGENT (b): no timer/event loop in background;
                            # hand the command to the main-thread pump.
                            self._bg_queue.put((command, client))
                        else:
                            def execute_wrapper():
                                try:
                                    response = self.execute_command(command)
                                    client.sendall(json.dumps(response).encode('utf-8'))
                                except Exception as e:
                                    log(f"error executing command: {e}")
                                    traceback.print_exc()
                                    try:
                                        client.sendall(json.dumps(
                                            {"status": "error", "message": str(e)}).encode('utf-8'))
                                    except Exception:
                                        pass
                                return None
                            bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                    except json.JSONDecodeError:
                        pass  # incomplete data, wait for more
                except Exception as e:
                    log(f"error receiving data: {e}")
                    break
        except Exception as e:
            log(f"error in client handler: {e}")
        finally:
            try:
                client.close()
            except Exception:
                pass
            log("client handler stopped")

    def run_headless_forever(self):
        """OPENBIMAGENT (b): main-thread command pump for background mode.

        `blender -b --python addon.py` reaches the end of the script and would
        exit; this loop keeps the process alive and executes queued commands on
        the main thread, where the bpy API is safe to use.
        """
        log("entering headless command pump (main thread)")
        while self.running:
            try:
                command, client = self._bg_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                response = self.execute_command(command)
            except Exception as e:
                log(f"error executing command: {e}")
                traceback.print_exc()
                response = {"status": "error", "message": str(e)}
            try:
                client.sendall(json.dumps(response).encode('utf-8'))
            except Exception as e:
                log(f"failed to send response: {e}")
        log("headless command pump stopped")

    # ------------------------------------------------------------------
    # command dispatch
    # ------------------------------------------------------------------
    def execute_command(self, command):
        """Execute a command in the main Blender thread (upstream pattern)."""
        try:
            return self._execute_command_internal(command)
        except Exception as e:
            log(f"error executing command: {e}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def _execute_command_internal(self, command):
        cmd_type = command.get("type")
        params = command.get("params", {})

        # OPENBIMAGENT (d): tool slimming. All Polyhaven/Sketchfab/Hyper3D/
        # Hunyuan3D handlers removed; fork tools added.
        handlers = {
            "ping": self.ping,
            "describe_capabilities": self.describe_capabilities,
            "get_scene_info": self.get_scene_info,
            "get_object_info": self.get_object_info,
            "get_viewport_screenshot": self.get_viewport_screenshot,
            "execute_code": self.execute_code,
            "execute_plan": self.execute_plan,
            "batch_render": self.batch_render,
            "camera_turntable": self.camera_turntable,
            "camera_path_render": self.camera_path_render,
            "set_editable_scope": self.set_editable_scope,
            "get_editable_scope": self.get_editable_scope,
            "restore_snapshot": self.restore_snapshot,
            "get_telemetry_consent": self.get_telemetry_consent,
        }

        handler = handlers.get(cmd_type)
        if handler:
            try:
                log(f"executing handler for {cmd_type}")
                result = handler(**params)
                log(f"handler {cmd_type} complete")
                return {"status": "success", "result": result}
            except Exception as e:
                log(f"error in handler {cmd_type}: {e}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

    # ------------------------------------------------------------------
    # OPENBIMAGENT (e): health check
    # ------------------------------------------------------------------
    def ping(self):
        import sys as _sys  # local import, not exposed to the sandbox
        return {
            "pong": True,
            "server": "openbimagent-blender-mcp",
            "fork_version": FORK_VERSION,
            "upstream": {"repo": UPSTREAM_REPO, "commit": UPSTREAM_COMMIT, "version": UPSTREAM_VERSION},
            "blender_version": bpy.app.version_string,
            "blender_version_tuple": list(bpy.app.version),
            "background": bpy.app.background,
            "python_version": _sys.version.split()[0],
            "time": datetime.now().isoformat(timespec="seconds"),
        }

    # ------------------------------------------------------------------
    # OPENBIMAGENT (h): capability disclosure
    # ------------------------------------------------------------------
    def describe_capabilities(self):
        import sys as _sys  # local import, not exposed to the sandbox
        return {
            "server": {
                "name": "openbimagent-blender-mcp (fork of ahujasid/blender-mcp)",
                "fork_version": FORK_VERSION,
                "upstream": {"repo": UPSTREAM_REPO, "commit": UPSTREAM_COMMIT, "version": UPSTREAM_VERSION},
            },
            "host": {
                "blender_version": bpy.app.version_string,
                "blender_version_tuple": list(bpy.app.version),
                "python_version": _sys.version.split()[0],
                "background": bpy.app.background,
                "render_engines_legal": probe_render_engines(),
                "render_engine_selected": pick_render_engine(),
            },
            "tools": TOOL_MANIFEST,
            "typed_execution": {
                "protocol_version": "1.0",
                "host_api_version": "5.2",
                "units": ["m", "mm"],
                "operations": ["create_object", "set_properties", "connect_topology"],
                "object_types": [
                    "utility_system", "manhole", "inlet", "outlet", "junction",
                    "valve", "equipment", "terminal", "distribution_port", "pipe_segment",
                ],
                "primitives": ["empty", "cylinder", "uv_sphere", "polyline_curve"],
                "controlled_save": True,
                "idempotent_receipts": True,
                "semantic_snapshot": True,
            },
            "limits": {
                "max_mcp_tools": 12,
                "execute_code": {
                    "ast_allowlist_imports": sorted(ALLOWED_IMPORT_ROOTS),
                    "banned_names": sorted(BANNED_BUILTIN_NAMES),
                    "dunder_access": "banned",
                    "auto_snapshot_before_exec": True,
                    "rollback_on_error_or_scope_violation": True,
                },
                "scope_lock": {
                    "default": "unlocked",
                    "semantics": "after execute_code, any created/modified/deleted object "
                                  "outside the whitelist triggers rollback to the pre-exec snapshot",
                    "temp_prefix_exempt": TEMP_PREFIX,
                },
                "screenshot": {
                    "black_threshold_mean_luminance": BLACK_THRESHOLD,
                    "headless_method": "bpy.ops.render.render(write_still=True) fallback",
                },
                "snapshots": {"dir": self.snapshot_dir, "keep_last": 12},
            },
            "telemetry": {"enabled": TELEMETRY_ENABLED, "hard_disabled": True},
            "known_issues": KNOWN_ISSUES,
        }

    # ------------------------------------------------------------------
    # OPENBIMAGENT (a): telemetry hard-off
    # ------------------------------------------------------------------
    def get_telemetry_consent(self):
        # Hard-coded; no addon preference or env var can change this.
        return {"consent": False, "hard_disabled": True, "enabled": TELEMETRY_ENABLED}

    # ------------------------------------------------------------------
    # upstream tools (get_scene_info / get_object_info unchanged in behavior)
    # ------------------------------------------------------------------
    def get_scene_info(self):
        """Get information about the current Blender scene (upstream)."""
        try:
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
                "materials_count": len(bpy.data.materials),
            }
            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:
                    break
                scene_info["objects"].append({
                    "name": obj.name,
                    "type": obj.type,
                    "location": [round(float(obj.location.x), 2),
                                 round(float(obj.location.y), 2),
                                 round(float(obj.location.z), 2)],
                })
            return scene_info
        except Exception as e:
            log(f"error in get_scene_info: {e}")
            traceback.print_exc()
            return {"error": str(e)}

    @staticmethod
    def _get_aabb(obj):
        """World-space axis-aligned bounding box of a mesh object (upstream)."""
        if obj.type != 'MESH':
            raise TypeError("Object must be a mesh")
        local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]
        world_bbox_corners = [obj.matrix_world @ corner for corner in local_bbox_corners]
        min_corner = mathutils.Vector(map(min, zip(*world_bbox_corners)))
        max_corner = mathutils.Vector(map(max, zip(*world_bbox_corners)))
        return [[*min_corner], [*max_corner]]

    def get_object_info(self, name):
        """Get detailed information about a specific object (upstream)."""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
            "materials": [],
        }
        if obj.type == "MESH":
            obj_info["world_bounding_box"] = self._get_aabb(obj)
        for slot in obj.material_slots:
            if slot.material:
                obj_info["materials"].append(slot.material.name)
        if obj.type == 'MESH' and obj.data:
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }
        return obj_info

    # ------------------------------------------------------------------
    # OPENBIMAGENT (f): screenshot with non-black assertion + render fallback
    # ------------------------------------------------------------------
    def get_viewport_screenshot(self, max_size=800, filepath=None, format="png"):
        """Capture the 3D viewport; assert the result is not a black frame.

        GUI: gpu.types.GPUOffScreen.draw_view3d (upstream path) with a 5.2
        gpu.init() guard. Background: no View3D region exists, so go straight
        to the render fallback. Either way the mean luminance is measured; a
        black offscreen capture automatically retries via the render fallback,
        and a still-black result is returned as an error (never silently).
        """
        if not filepath:
            return {"error": "No filepath provided"}

        if bpy.app.background or bpy.context.screen is None:
            return self._render_fallback_capture(filepath, max_size, reason="background-no-viewport")

        # --- GUI path: offscreen viewport draw (upstream) ---
        area = region = space = None
        for a in bpy.context.screen.areas:
            if a.type == 'VIEW_3D':
                area = a
                space = a.spaces.active
                region = next((r for r in a.regions if r.type == 'WINDOW'), None)
                break
        if not area or region is None or space is None:
            return self._render_fallback_capture(filepath, max_size, reason="no-view3d-area")

        try:
            import numpy as np
            if not gpu_init_guard():
                raise RuntimeError("gpu module unavailable")
            import gpu

            r3d = space.region_3d
            src_w, src_h = region.width, region.height
            if max(src_w, src_h) > max_size:
                s = max_size / max(src_w, src_h)
                width, height = max(1, int(src_w * s)), max(1, int(src_h * s))
            else:
                width, height = src_w, src_h

            offscreen = gpu.types.GPUOffScreen(width, height)
            try:
                offscreen.draw_view3d(
                    bpy.context.scene, bpy.context.view_layer, space, region,
                    r3d.view_matrix, r3d.window_matrix, do_color_management=True)
                buf = offscreen.texture_color.read()
            finally:
                offscreen.free()

            buf.dimensions = width * height * 4
            pixels = np.asarray(buf, dtype=np.float32) / 255.0

            image = bpy.data.images.new(f"{TEMP_PREFIX}viewport", width, height, alpha=True)
            image.pixels.foreach_set(pixels.ravel())
            image.filepath_raw = filepath
            image.file_format = format.upper()
            image.save()
            bpy.data.images.remove(image)

            brightness = image_file_brightness(filepath)
            if brightness < BLACK_THRESHOLD:
                log(f"offscreen capture black (brightness={brightness:.4f}); falling back to render")
                return self._render_fallback_capture(filepath, max_size,
                                                     reason=f"offscreen-black({brightness:.4f})")
            return {"success": True, "width": width, "height": height,
                    "filepath": filepath, "method": "offscreen", "brightness": brightness}
        except Exception as e:
            log(f"offscreen capture failed ({e}); falling back to render")
            return self._render_fallback_capture(filepath, max_size, reason=f"offscreen-error({e})")

    def _render_fallback_capture(self, filepath, max_size, reason):
        """OPENBIMAGENT (f/5.2): headless capture via bpy.ops.render.render."""
        try:
            out = self._render_single(filepath, width=max_size, height=max_size, camera=None)
        except Exception as e:
            return {"error": f"render fallback failed ({reason}): {e}"}
        if out["brightness"] < BLACK_THRESHOLD:
            return {"error": f"screenshot black even after render fallback "
                             f"(brightness={out['brightness']:.4f}, reason={reason})",
                    "brightness": out["brightness"], "method": "render_fallback"}
        return {"success": True, "width": out["width"], "height": out["height"],
                "filepath": filepath, "method": "render_fallback",
                "brightness": out["brightness"], "fallback_reason": reason}

    def _ensure_temp_camera_and_light(self, scene, target=None, distance=None):
        """Create a temp framing camera / sun light when the scene lacks them.

        Returns (camera_or_None, light_or_None, created: list) so callers can
        clean up only what they created.
        """
        created = []
        cam = scene.camera
        if cam is None:
            bbox = _scene_bbox()
            if bbox:
                mins, maxs = bbox
                center = mathutils.Vector([(mins[i] + maxs[i]) / 2 for i in range(3)])
                diag = (mathutils.Vector(maxs) - mathutils.Vector(mins)).length
            else:
                center = mathutils.Vector((0, 0, 0))
                diag = 4.0
            dist = distance or max(diag * 1.8, 3.0)
            direction = mathutils.Vector((1.0, -1.0, 0.7)).normalized()
            cam_data = bpy.data.cameras.new(f"{TEMP_PREFIX}Cam")
            cam = bpy.data.objects.new(f"{TEMP_PREFIX}Cam", cam_data)
            scene.collection.objects.link(cam)
            cam.location = center + direction * dist
            cam.rotation_euler = (center - cam.location).to_track_quat('-Z', 'Y').to_euler()
            cam.data.clip_end = max(1000.0, dist * 10)
            created.append(cam)
        has_light = any(o.type == 'LIGHT' for o in scene.objects)
        light = None
        if not has_light:
            light_data = bpy.data.lights.new(f"{TEMP_PREFIX}Sun", type='SUN')
            light_data.energy = 3.0
            light = bpy.data.objects.new(f"{TEMP_PREFIX}Sun", light_data)
            scene.collection.objects.link(light)
            light.rotation_euler = (math.radians(50), 0.0, math.radians(30))
            created.append(light)
        return cam, light, created

    def _render_single(self, filepath, width, height, camera, engine=None):
        """Render one still with engine probing + state save/restore; returns stats."""
        scene = bpy.context.scene
        state = _push_render_state(scene)
        created = []
        try:
            cam, _light, created = self._ensure_temp_camera_and_light(scene)
            if camera is not None:
                cam_obj = bpy.data.objects.get(camera) if isinstance(camera, str) else camera
                if cam_obj is None:
                    raise ValueError(f"camera not found: {camera}")
                if cam_obj.type != 'CAMERA':
                    raise ValueError(f"object is not a camera: {camera}")
                scene.camera = cam_obj
            else:
                scene.camera = cam

            chosen = pick_render_engine(engine)
            scene.render.engine = chosen
            scene.render.resolution_x = int(width)
            scene.render.resolution_y = int(height)
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = 'PNG'
            scene.render.filepath = filepath

            bpy.ops.render.render(write_still=True)

            brightness = image_file_brightness(filepath)
            return {"filepath": filepath, "width": int(width), "height": int(height),
                    "engine": chosen, "brightness": brightness,
                    "camera": scene.camera.name if scene.camera else None}
        finally:
            for obj in created:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:
                    pass
            _pop_render_state(scene, state)

    # ------------------------------------------------------------------
    # OPENBIMAGENT (c): snapshots
    # ------------------------------------------------------------------
    def _save_snapshot(self, tag="pre_exec"):
        os.makedirs(self.snapshot_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(self.snapshot_dir, f"snapshot_{ts}_{tag}.blend")
        # copy=True: save a snapshot copy without changing the session's filepath
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
        self.snapshots.append(path)
        self.last_snapshot = path
        # rotate
        while len(self.snapshots) > 12:
            old = self.snapshots.pop(0)
            try:
                os.remove(old)
            except OSError:
                pass
        # session snapshot event (JSONL trace; no code content, just a hash)
        try:
            event_path = os.path.join(self.snapshot_dir, "snapshot_events.jsonl")
            with open(event_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.now().isoformat(timespec="milliseconds"),
                    "event": "snapshot", "tag": tag, "path": path,
                }) + "\n")
        except OSError as e:
            log(f"snapshot event log failed: {e}")
        return path

    def restore_snapshot(self, path=None):
        target = path or self.last_snapshot
        if not target:
            raise ValueError("no snapshot available to restore")
        if not os.path.exists(target):
            raise ValueError(f"snapshot file missing: {target}")
        bpy.ops.wm.open_mainfile(filepath=target)
        return {"restored": target}

    # ------------------------------------------------------------------
    # OPENBIMAGENT (g): editable-scope lock
    # ------------------------------------------------------------------
    def set_editable_scope(self, objects=None, collections=None, enabled=True):
        if not enabled:
            self.editable_scope = None
            return {"enabled": False}
        scope = {"objects": set(objects or []), "collections": set(collections or [])}
        missing_objects = sorted(n for n in scope["objects"] if bpy.data.objects.get(n) is None)
        missing_collections = sorted(n for n in scope["collections"] if bpy.data.collections.get(n) is None)
        self.editable_scope = scope
        return {
            "enabled": True,
            "objects": sorted(scope["objects"]),
            "collections": sorted(scope["collections"]),
            "missing_objects": missing_objects,
            "missing_collections": missing_collections,
        }

    def get_editable_scope(self):
        if self.editable_scope is None:
            return {"enabled": False}
        return {"enabled": True,
                "objects": sorted(self.editable_scope["objects"]),
                "collections": sorted(self.editable_scope["collections"])}

    def _is_editable(self, obj):
        scope = self.editable_scope
        if scope is None:
            return True
        if obj.name in scope["objects"]:
            return True
        for coll in obj.users_collection:
            c = coll
            while c is not None:
                if c.name in scope["collections"]:
                    return True
                # walk up via parent collections
                parents = [pc for pc in bpy.data.collections if c.name in [ch.name for ch in pc.children]]
                c = parents[0] if parents else None
        return False

    @staticmethod
    def _object_fingerprint(obj):
        fp = [
            obj.type,
            tuple(round(v, 6) for v in obj.location),
            tuple(round(v, 6) for v in obj.rotation_euler),
            tuple(round(v, 6) for v in obj.scale),
            tuple(round(v, 6) for v in obj.dimensions),
            obj.parent.name if obj.parent else None,
            obj.hide_viewport, obj.hide_render,
            tuple(m.name for m in obj.modifiers),
            tuple(sorted(s.material.name for s in obj.material_slots if s.material)),
            obj.data.name if obj.data else None,
        ]
        if obj.type == 'MESH' and obj.data:
            import numpy as np
            mesh = obj.data
            nv = len(mesh.vertices)
            if nv <= 200000:
                arr = np.empty(nv * 3, dtype=np.float32)
                mesh.vertices.foreach_get("co", arr)
                vhash = hashlib.md5(np.round(arr, 5).tobytes()).hexdigest()
            else:
                arr = np.empty(1000 * 3, dtype=np.float32)
                mesh.vertices.foreach_get("co", arr)  # first 1000 verts only
                vhash = f"large:{hashlib.md5(np.round(arr, 5).tobytes()).hexdigest()}"
            fp.append((nv, len(mesh.polygons), vhash))
        return repr(fp)

    def _fingerprint_out_of_scope(self):
        fps = {}
        for obj in bpy.context.scene.objects:
            if obj.name.startswith(TEMP_PREFIX):
                continue
            if not self._is_editable(obj):
                fps[obj.name] = self._object_fingerprint(obj)
        return fps

    def _verify_scope(self, before_fps, before_names):
        """Compare scene against pre-exec fingerprints; return violation strings."""
        violations = []
        after_names = {obj.name for obj in bpy.context.scene.objects}
        # deleted or modified out-of-scope objects
        for name, fp in before_fps.items():
            obj = bpy.data.objects.get(name)
            if obj is None or name not in after_names:
                violations.append(f"object '{name}' was deleted outside the editable scope")
            elif self._object_fingerprint(obj) != fp:
                violations.append(f"object '{name}' was modified outside the editable scope")
        # newly created objects must be editable (or temp)
        for name in sorted(after_names - set(before_names)):
            if name.startswith(TEMP_PREFIX):
                continue
            obj = bpy.data.objects.get(name)
            if obj is not None and not self._is_editable(obj):
                violations.append(f"object '{name}' was created outside the editable scope")
        return violations

    # ------------------------------------------------------------------
    # OPENBIMAGENT M1: typed execution (never enters execute_code/exec)
    # ------------------------------------------------------------------
    def execute_plan(self, plan, output_path, approved=False):
        root = os.getenv("OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT", "")
        return execute_typed_plan(
            plan=plan,
            output_path=output_path,
            authorized_root=root,
            approved=approved,
            bpy_module=bpy,
            snapshot_fn=self._save_snapshot,
            fork_version=FORK_VERSION,
        )

    # ------------------------------------------------------------------
    # OPENBIMAGENT (c)+(g): sandboxed execute_code
    # ------------------------------------------------------------------
    def execute_code(self, code):
        """Execute Blender Python code with AST allowlist + auto snapshot + scope lock."""
        # 1) AST allowlist (c) -- reject before touching the scene
        violations = validate_code_ast(code)
        if violations:
            raise SandboxViolation(
                "AST allowlist rejected the code: " + "; ".join(violations))

        # 2) automatic snapshot (c)
        snapshot_path = self._save_snapshot(tag="pre_exec")
        code_hash = hashlib.md5(code.encode("utf-8")).hexdigest()

        # 3) scope-lock baseline (g)
        scope_active = self.editable_scope is not None
        before_fps = self._fingerprint_out_of_scope() if scope_active else {}
        before_names = {obj.name for obj in bpy.context.scene.objects}

        namespace = {"bpy": bpy, "bmesh": bmesh, "mathutils": mathutils, "math": math}
        capture_buffer = io.StringIO()
        try:
            with redirect_stdout(capture_buffer):
                exec(code, namespace)
        except Exception as e:
            # roll back half-applied state
            log(f"execute_code raised ({e}); rolling back to {snapshot_path}")
            try:
                bpy.ops.wm.open_mainfile(filepath=snapshot_path)
            except Exception as rb:
                log(f"rollback failed: {rb}")
            raise Exception(f"Code execution error (rolled back to snapshot): {e}")

        # 4) scope verification (g)
        if scope_active:
            violations = self._verify_scope(before_fps, before_names)
            if violations:
                log(f"scope violation; rolling back to {snapshot_path}: {violations}")
                try:
                    bpy.ops.wm.open_mainfile(filepath=snapshot_path)
                except Exception as rb:
                    log(f"rollback failed: {rb}")
                raise Exception(
                    "Scope violation (rolled back to snapshot): " + "; ".join(violations))

        captured_output = capture_buffer.getvalue()
        return {"executed": True, "result": captured_output,
                "snapshot": snapshot_path, "code_hash": code_hash,
                "scope_checked": scope_active}

    # ------------------------------------------------------------------
    # OPENBIMAGENT (d): render tools
    # ------------------------------------------------------------------
    def batch_render(self, output_dir, cameras=None, width=512, height=512, engine=None):
        """Render one still per camera. `cameras=None` renders every camera in the scene."""
        os.makedirs(output_dir, exist_ok=True)
        scene = bpy.context.scene
        if cameras is None:
            cam_names = [o.name for o in scene.objects if o.type == 'CAMERA'
                         and not o.name.startswith(TEMP_PREFIX)]
            if not cam_names:
                raise ValueError("no cameras in scene and none requested")
        else:
            cam_names = list(cameras)
        results = []
        for i, name in enumerate(cam_names):
            fp = os.path.join(output_dir, f"batch_{i:03d}_{name}.png")
            out = self._render_single(fp, width, height, camera=name, engine=engine)
            out["ok"] = out["brightness"] >= BLACK_THRESHOLD
            results.append(out)
        return {"count": len(results), "results": results,
                "all_nonblack": all(r["ok"] for r in results)}

    def camera_turntable(self, output_dir, target=None, target_location=None,
                         radius=None, height=None, frames=8,
                         width=512, height_px=None, engine=None):
        """Orbit a temp camera around `target` (object name) or `target_location` and render."""
        os.makedirs(output_dir, exist_ok=True)
        scene = bpy.context.scene

        if target:
            obj = bpy.data.objects.get(target)
            if obj is None:
                raise ValueError(f"target object not found: {target}")
            center = obj.matrix_world.translation.copy()
            diag = max(obj.dimensions.length, 1.0)
        elif target_location:
            center = mathutils.Vector(target_location)
            diag = 2.0
        else:
            bbox = _scene_bbox()
            if bbox:
                mins, maxs = bbox
                center = mathutils.Vector([(mins[i] + maxs[i]) / 2 for i in range(3)])
                diag = max((mathutils.Vector(maxs) - mathutils.Vector(mins)).length, 1.0)
            else:
                center = mathutils.Vector((0, 0, 0))
                diag = 2.0

        r = radius if radius is not None else max(diag * 2.0, 3.0)
        h = height if height is not None else center.z + r * 0.35
        w = int(width)
        hh = int(height_px) if height_px else w

        cam_data = bpy.data.cameras.new(f"{TEMP_PREFIX}TurntableCam")
        cam = bpy.data.objects.new(f"{TEMP_PREFIX}TurntableCam", cam_data)
        scene.collection.objects.link(cam)
        cam.data.clip_end = max(1000.0, r * 10)

        state = _push_render_state(scene)
        results = []
        try:
            _cam, _light, created_light = self._ensure_temp_camera_and_light(scene)
            # keep our turntable camera (not the temp framing one)
            scene.camera = cam
            chosen = pick_render_engine(engine)
            scene.render.engine = chosen
            scene.render.resolution_x = w
            scene.render.resolution_y = hh
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = 'PNG'

            for i in range(int(frames)):
                ang = 2.0 * math.pi * i / int(frames)
                cam.location = (center.x + r * math.cos(ang),
                                center.y + r * math.sin(ang), h)
                cam.rotation_euler = (center - cam.location).to_track_quat('-Z', 'Y').to_euler()
                bpy.context.view_layer.update()
                fp = os.path.join(output_dir, f"turntable_{i:03d}.png")
                scene.render.filepath = fp
                bpy.ops.render.render(write_still=True)
                brightness = image_file_brightness(fp)
                results.append({"frame": i, "filepath": fp, "angle_deg": round(math.degrees(ang), 1),
                                "brightness": brightness, "ok": brightness >= BLACK_THRESHOLD})
        finally:
            for obj in [o for o in scene.objects if o.name.startswith(TEMP_PREFIX)]:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:
                    pass
            _pop_render_state(scene, state)
        return {"frames": len(results), "results": results,
                "all_nonblack": all(r["ok"] for r in results)}

    def camera_path_render(self, output_dir, points, target=None, target_location=None,
                           width=512, height_px=None, engine=None):
        """Move a temp camera through `points` [[x,y,z],...] rendering each frame."""
        if not points or len(points) < 1:
            raise ValueError("points must be a non-empty list of [x,y,z]")
        os.makedirs(output_dir, exist_ok=True)
        scene = bpy.context.scene

        if target:
            obj = bpy.data.objects.get(target)
            if obj is None:
                raise ValueError(f"target object not found: {target}")
            look_at = obj.matrix_world.translation.copy()
        elif target_location:
            look_at = mathutils.Vector(target_location)
        else:
            bbox = _scene_bbox()
            look_at = (mathutils.Vector([(bbox[0][i] + bbox[1][i]) / 2 for i in range(3)])
                       if bbox else mathutils.Vector((0, 0, 0)))

        w = int(width)
        hh = int(height_px) if height_px else w

        cam_data = bpy.data.cameras.new(f"{TEMP_PREFIX}PathCam")
        cam = bpy.data.objects.new(f"{TEMP_PREFIX}PathCam", cam_data)
        scene.collection.objects.link(cam)
        cam.data.clip_end = 2000.0

        state = _push_render_state(scene)
        results = []
        try:
            self._ensure_temp_camera_and_light(scene)
            scene.camera = cam
            chosen = pick_render_engine(engine)
            scene.render.engine = chosen
            scene.render.resolution_x = w
            scene.render.resolution_y = hh
            scene.render.resolution_percentage = 100
            scene.render.image_settings.file_format = 'PNG'

            for i, p in enumerate(points):
                cam.location = mathutils.Vector(p)
                cam.rotation_euler = (look_at - cam.location).to_track_quat('-Z', 'Y').to_euler()
                bpy.context.view_layer.update()
                fp = os.path.join(output_dir, f"path_{i:03d}.png")
                scene.render.filepath = fp
                bpy.ops.render.render(write_still=True)
                brightness = image_file_brightness(fp)
                results.append({"frame": i, "filepath": fp, "point": list(p),
                                "brightness": brightness, "ok": brightness >= BLACK_THRESHOLD})
        finally:
            for obj in [o for o in scene.objects if o.name.startswith(TEMP_PREFIX)]:
                try:
                    bpy.data.objects.remove(obj, do_unlink=True)
                except Exception:
                    pass
            _pop_render_state(scene, state)
        return {"frames": len(results), "results": results,
                "all_nonblack": all(r["ok"] for r in results)}


# ---------------------------------------------------------------------------
# Blender UI (OPENBIMAGENT (d): stripped to port + start/stop; all asset-store
# checkboxes, API-key fields, free-trial and terms operators removed.
# OPENBIMAGENT (a): no telemetry preference at all.)
# ---------------------------------------------------------------------------
class BLENDERMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP (openBIMAgent fork)"
    bl_idname = "BLENDERMCP_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BlenderMCP'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "blendermcp_port")
        if not scene.blendermcp_server_running:
            layout.operator("blendermcp.start_server", text="Connect to MCP server")
        else:
            layout.operator("blendermcp.stop_server", text="Disconnect from MCP server")
            layout.label(text=f"Running on port {scene.blendermcp_port}")
        layout.separator()
        layout.label(text=f"fork {FORK_VERSION} (upstream {UPSTREAM_VERSION})")
        layout.label(text="Telemetry: HARD DISABLED")
        layout.label(text="Asset stores removed (fork)")


class BLENDERMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendermcp.start_server"
    bl_label = "Start BlenderMCP server"
    bl_description = "Start the BlenderMCP server"

    def execute(self, context):
        scene = context.scene
        if not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server:
            bpy.types.blendermcp_server = BlenderMCPServer(port=scene.blendermcp_port)
        bpy.types.blendermcp_server.start()
        scene.blendermcp_server_running = bpy.types.blendermcp_server.running
        return {'FINISHED'}


class BLENDERMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendermcp.stop_server"
    bl_label = "Stop the BlenderMCP server"
    bl_description = "Stop the BlenderMCP server"

    def execute(self, context):
        if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
            bpy.types.blendermcp_server.stop()
            del bpy.types.blendermcp_server
        context.scene.blendermcp_server_running = False
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
def register():
    bpy.types.Scene.blendermcp_port = bpy.props.IntProperty(
        name="Port",
        description="Port for the BlenderMCP server",
        default=9876, min=1024, max=65535)
    bpy.types.Scene.blendermcp_server_running = bpy.props.BoolProperty(
        name="Server Running", default=False)
    bpy.types.Scene.blendermcp_auto_start_server = bpy.props.BoolProperty(
        name="Auto-Start Server",
        description="Automatically start the MCP server when Blender loads",
        default=True)

    bpy.utils.register_class(BLENDERMCP_PT_Panel)
    bpy.utils.register_class(BLENDERMCP_OT_StartServer)
    bpy.utils.register_class(BLENDERMCP_OT_StopServer)

    scene = getattr(bpy.context, 'scene', None)
    if scene is not None:
        port = scene.blendermcp_port
        auto_start = scene.blendermcp_auto_start_server
    else:
        port = 9876
        auto_start = True
    # OPENBIMAGENT: env override so tests can run on a dedicated port without
    # touching user preferences.
    port = int(os.getenv("OPENBIMAGENT_BLENDER_PORT", port))

    if auto_start and (not hasattr(bpy.types, "blendermcp_server") or not bpy.types.blendermcp_server):
        bpy.types.blendermcp_server = BlenderMCPServer(port=port)
    if auto_start and not bpy.types.blendermcp_server.running:
        bpy.types.blendermcp_server.start()
        try:
            bpy.context.scene.blendermcp_server_running = bpy.types.blendermcp_server.running
        except AttributeError:
            pass
    log("addon registered")


def unregister():
    if hasattr(bpy.types, "blendermcp_server") and bpy.types.blendermcp_server:
        bpy.types.blendermcp_server.stop()
        del bpy.types.blendermcp_server

    bpy.utils.unregister_class(BLENDERMCP_PT_Panel)
    bpy.utils.unregister_class(BLENDERMCP_OT_StartServer)
    bpy.utils.unregister_class(BLENDERMCP_OT_StopServer)

    del bpy.types.Scene.blendermcp_port
    del bpy.types.Scene.blendermcp_server_running
    del bpy.types.Scene.blendermcp_auto_start_server
    log("addon unregistered")


if __name__ == "__main__":
    register()
    # OPENBIMAGENT (b): in background mode the script must not return (Blender
    # would exit); pump the command queue on the main thread instead.
    if bpy.app.background:
        server = bpy.types.blendermcp_server
        try:
            server.run_headless_forever()
        except KeyboardInterrupt:
            server.stop()
