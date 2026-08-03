"""Raw-socket test client for the openBIMAgent blender-mcp fork.

Speaks the addon's JSON protocol directly (no MCP dependency), runs the M0
acceptance cases and prints [PASS]/[FAIL] per case. Driven by run_fork_tests.py,
also runnable standalone against an already-running Blender:

    python socket_test_client.py --port 9887 --out <dir>
"""

import argparse
import json
import os
import socket
import sys
import time

BLACK_THRESHOLD = 0.01


class BlenderSocketClient:
    """Minimal protocol client (mirrors the fork's framing: one JSON command,
    one JSON response, chunked receive)."""

    def __init__(self, host="127.0.0.1", port=9887, timeout=300.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def send(self, cmd_type, params=None):
        """Send a command; return result dict; raise RuntimeError on status=error."""
        if not self.sock:
            self.connect()
        payload = json.dumps({"type": cmd_type, "params": params or {}}).encode("utf-8")
        self.sock.sendall(payload)
        chunks = []
        while True:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("connection closed by Blender addon")
            chunks.append(chunk)
            data = b"".join(chunks)
            try:
                response = json.loads(data.decode("utf-8"))
                break
            except json.JSONDecodeError:
                continue
        if response.get("status") == "error":
            raise RuntimeError(response.get("message", "unknown addon error"))
        return response.get("result", {})


class Suite:
    def __init__(self, client, out_dir, verbose=True):
        self.c = client
        self.out = out_dir
        self.verbose = verbose
        self.results = []  # (case_id, name, ok, detail, seconds)

    def run(self, fn, case_id, name):
        t0 = time.time()
        try:
            detail = fn() or "ok"
            ok = True
        except Exception as e:
            detail, ok = f"{type(e).__name__}: {e}", False
        dt = time.time() - t0
        self.results.append((case_id, name, ok, detail, dt))
        if self.verbose:
            tag = "PASS" if ok else "FAIL"
            print(f"[{tag}] {case_id} {name} ({dt:.1f}s) -- {detail}", flush=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def expect_error_contains(fn, needle, what):
    try:
        fn()
    except RuntimeError as e:
        if needle.lower() in str(e).lower():
            return str(e)
        raise AssertionError(f"{what}: error raised but did not mention '{needle}': {e}")
    raise AssertionError(f"{what}: expected an error containing '{needle}', but the call succeeded")


# ---------------------------------------------------------------------------
# test cases
# ---------------------------------------------------------------------------

def run_all_cases(client, out_dir):
    s = Suite(client, out_dir)
    os.makedirs(out_dir, exist_ok=True)

    # -- T1: health check (e) -------------------------------------------------
    def t1():
        r = client.send("ping")
        expect(r.get("pong") is True, f"no pong: {r}")
        expect(r.get("background") is True, f"expected background mode: {r}")
        bv = r.get("blender_version", "")
        expect(bv.startswith("5."), f"expected Blender 5.x, got {bv}")
        return f"pong, blender={bv}, fork={r.get('fork_version')}, py={r.get('python_version')}"
    s.run(t1, "T1", "ping/health check (e)")

    # -- T2: describe_capabilities (h) ----------------------------------------
    def t2():
        r = client.send("describe_capabilities")
        for key in ("server", "host", "tools", "limits", "telemetry", "known_issues"):
            expect(key in r, f"missing key '{key}' in capabilities")
        expect(r["telemetry"]["enabled"] is False and r["telemetry"]["hard_disabled"] is True,
               "telemetry not hard-disabled in capabilities")
        tool_names = {t["name"] for t in r["tools"]}
        for need in ("execute_code", "batch_render", "camera_turntable",
                     "camera_path_render", "set_editable_scope", "ping"):
            expect(need in tool_names, f"tool '{need}' missing from manifest")
        for banned in ("polyhaven", "sketchfab", "rodin", "hyper3d", "hunyuan"):
            expect(not any(banned in t for t in tool_names), f"cut integration still exposed: {banned}")
        engines = r["host"]["render_engines_legal"]
        expect("BLENDER_EEVEE" in engines, f"5.2 engine list unexpected: {engines}")
        expect("BLENDER_EEVEE_NEXT" not in engines, "BLENDER_EEVEE_NEXT should not exist on 5.2")
        expect(len(r["known_issues"]) >= 3, "known_issues list suspiciously short")
        return f"{len(tool_names)} socket commands, engines={engines}, issues={len(r['known_issues'])}"
    s.run(t2, "T2", "describe_capabilities complete (h)")

    # -- T3: telemetry hard off (a) --------------------------------------------
    def t3():
        r = client.send("get_telemetry_consent")
        expect(r.get("consent") is False, f"consent not False: {r}")
        expect(r.get("hard_disabled") is True and r.get("enabled") is False,
               f"hard-disable flags missing: {r}")
        return f"consent={r['consent']}, hard_disabled={r['hard_disabled']}"
    s.run(t3, "T3", "telemetry hard-off (a)")

    # -- T4: execute_code happy path + auto snapshot (c) -----------------------
    def t4():
        code = (
            "import bpy\n"
            "bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))\n"
            "bpy.context.active_object.name = 'M0Cube'\n"
            "print('cube-ok')\n"
        )
        r = client.send("execute_code", {"code": code})
        expect(r.get("executed") is True, f"not executed: {r}")
        expect("cube-ok" in r.get("result", ""), f"stdout not captured: {r}")
        snap = r.get("snapshot", "")
        expect(snap and os.path.exists(snap), f"snapshot missing on disk: {snap}")
        expect(snap.endswith(".blend"), f"snapshot not a .blend: {snap}")
        info = client.send("get_object_info", {"name": "M0Cube"})
        expect(info.get("type") == "MESH", f"M0Cube not in scene: {info}")
        return f"cube created, snapshot={os.path.basename(snap)}"
    s.run(t4, "T4", "execute_code + auto snapshot (c)")

    # -- T5: AST allowlist (c) --------------------------------------------------
    def t5():
        blocked = [
            ("import os", "import"),
            ("import subprocess", "import"),
            ("import sys", "import"),
            ("import socket", "import"),
            ("from os import path", "import"),
            ("__import__('os')", "__import__"),
            ("open('C:/x.txt', 'w')", "open"),
            ("eval('1+1')", "eval"),
            ("exec('print(1)')", "exec"),
            ("getattr(bpy, 'ops')", "getattr"),
            ("bpy.__class__", "dunder"),
        ]
        for code, needle in blocked:
            expect_error_contains(
                lambda c=code: client.send("execute_code", {"code": c}),
                "AST allowlist", f"code {code!r} must be rejected")
        # allowed side
        r = client.send("execute_code", {"code":
            "import bpy\nimport bmesh\nimport mathutils\nfrom math import sqrt\n"
            "print('allowed-ok', sqrt(4))\n"})
        expect("allowed-ok" in r.get("result", ""), f"whitelisted imports failed: {r}")
        return "11 hostile snippets rejected, whitelisted imports ok"
    s.run(t5, "T5", "AST allowlist blocks os/subprocess/open/eval (c)")

    # -- T6: screenshot non-black (f) -------------------------------------------
    def t6():
        fp = os.path.join(out_dir, "viewport_shot.png")
        r = client.send("get_viewport_screenshot", {"max_size": 256, "filepath": fp, "format": "png"})
        expect("error" not in r, f"screenshot error: {r}")
        expect(os.path.exists(fp) and os.path.getsize(fp) > 0, "screenshot file missing")
        b = r.get("brightness", 0.0)
        expect(b >= BLACK_THRESHOLD, f"black frame slipped through: brightness={b}")
        expect(r.get("method") == "render_fallback",
               f"headless must use render_fallback, got {r.get('method')}")
        return f"method={r['method']}, brightness={b:.4f}, {os.path.getsize(fp)} bytes"
    s.run(t6, "T6", "screenshot non-black assertion (f)")

    # -- T7: editable-scope lock with rollback (g) -------------------------------
    def t7():
        # create an out-of-scope object while unlocked
        client.send("execute_code", {"code":
            "import bpy\n"
            "bpy.ops.mesh.primitive_cube_add(location=(5, 0, 0))\n"
            "bpy.context.active_object.name = 'Ground'\n"})
        # lock scope to M0Cube only
        r = client.send("set_editable_scope", {"objects": ["M0Cube"], "collections": [], "enabled": True})
        expect(r.get("enabled") is True, f"scope not enabled: {r}")

        # in-scope edit is allowed
        r = client.send("execute_code", {"code":
            "import bpy\nbpy.data.objects['M0Cube'].location.z = 1.0\nprint('moved')\n"})
        expect(r.get("executed") is True, f"in-scope edit blocked: {r}")
        expect(r.get("scope_checked") is True, "scope check did not run")

        # 1) modify out-of-scope -> violation + rollback
        expect_error_contains(
            lambda: client.send("execute_code", {"code":
                "import bpy\nbpy.data.objects['Ground'].location.z = 9.0\n"}),
            "Scope violation", "modifying Ground")
        g = client.send("get_object_info", {"name": "Ground"})
        expect(abs(g["location"][2]) < 1e-6, f"rollback failed, Ground z={g['location'][2]}")

        # 2) create out-of-scope -> violation + rollback (object gone afterwards)
        expect_error_contains(
            lambda: client.send("execute_code", {"code":
                "import bpy\nbpy.ops.mesh.primitive_cube_add(location=(9,9,9))\n"
                "bpy.context.active_object.name = 'Hacker'\n"}),
            "Scope violation", "creating Hacker")
        expect_error_contains(
            lambda: client.send("get_object_info", {"name": "Hacker"}),
            "Object not found", "Hacker must be rolled back")
        g = client.send("get_object_info", {"name": "Ground"})
        expect(g.get("name") == "Ground", "Ground lost during rollback")

        # 3) delete out-of-scope -> violation + rollback (object still there)
        expect_error_contains(
            lambda: client.send("execute_code", {"code":
                "import bpy\nbpy.data.objects.remove(bpy.data.objects['Ground'], do_unlink=True)\n"}),
            "Scope violation", "deleting Ground")
        g = client.send("get_object_info", {"name": "Ground"})
        expect(g.get("name") == "Ground", "Ground not restored after delete rollback")

        # in-scope state after rollbacks: M0Cube edit from before must persist
        m = client.send("get_object_info", {"name": "M0Cube"})
        expect(abs(m["location"][2] - 1.0) < 1e-6, f"M0Cube lost its in-scope edit: {m['location']}")

        # unlock
        r = client.send("set_editable_scope", {"enabled": False})
        expect(r.get("enabled") is False, f"unlock failed: {r}")
        return "modify/create/delete outside scope all rejected + rolled back; in-scope edit kept"
    s.run(t7, "T7", "editable-scope lock + rollback (g)")

    # -- T8: batch_render, 2 cameras (d) ----------------------------------------
    def t8():
        client.send("execute_code", {"code":
            "import bpy, mathutils\n"
            "def mkcam(name, loc):\n"
            "    d = bpy.data.cameras.new(name)\n"
            "    o = bpy.data.objects.new(name, d)\n"
            "    bpy.context.scene.collection.objects.link(o)\n"
            "    o.location = loc\n"
            "    o.rotation_euler = ((mathutils.Vector((0,0,0.5)) - o.location).to_track_quat('-Z','Y')).to_euler()\n"
            "mkcam('CamA', (6, -6, 4))\n"
            "mkcam('CamB', (-6, -6, 2))\n"
            "print('cams-ok')\n"})
        outdir = os.path.join(out_dir, "batch")
        r = client.send("batch_render", {"output_dir": outdir, "cameras": ["CamA", "CamB"],
                                         "width": 256, "height": 256})
        expect(r.get("count") == 2, f"expected 2 renders: {r}")
        expect(r.get("all_nonblack") is True, f"black frame in batch: {r}")
        for res in r["results"]:
            expect(os.path.exists(res["filepath"]) and os.path.getsize(res["filepath"]) > 0,
                   f"missing render {res['filepath']}")
        bs = [round(x["brightness"], 3) for x in r["results"]]
        return f"2 cameras rendered, brightness={bs}"
    s.run(t8, "T8", "batch_render 2 cameras (d)")

    # -- T9: camera_turntable (d) -----------------------------------------------
    def t9():
        outdir = os.path.join(out_dir, "turntable")
        r = client.send("camera_turntable", {"output_dir": outdir, "target": "M0Cube",
                                             "frames": 2, "width": 128})
        expect(r.get("frames") == 2, f"expected 2 frames: {r}")
        expect(r.get("all_nonblack") is True, f"black frame in turntable: {r}")
        for res in r["results"]:
            expect(os.path.exists(res["filepath"]), f"missing frame {res['filepath']}")
        return f"2 turntable frames, brightness={[round(x['brightness'],3) for x in r['results']]}"
    s.run(t9, "T9", "camera_turntable (d)")

    # -- T10: camera_path_render (d) ---------------------------------------------
    def t10():
        outdir = os.path.join(out_dir, "path")
        r = client.send("camera_path_render", {
            "output_dir": outdir, "points": [[6, -6, 4], [0, -8, 3]],
            "target": "M0Cube", "width": 128})
        expect(r.get("frames") == 2, f"expected 2 frames: {r}")
        expect(r.get("all_nonblack") is True, f"black frame in path render: {r}")
        return f"2 path frames, brightness={[round(x['brightness'],3) for x in r['results']]}"
    s.run(t10, "T10", "camera_path_render (d)")

    return s.results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9887)
    ap.add_argument("--out", default=os.path.join(tempfile_gettempdir(), "fork_test_out"))
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    client = BlenderSocketClient(args.host, args.port, args.timeout)
    client.connect()
    try:
        results = run_all_cases(client, args.out)
    finally:
        client.close()
    failed = [r for r in results if not r[2]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed", flush=True)
    sys.exit(1 if failed else 0)


def tempfile_gettempdir():
    import tempfile
    return tempfile.gettempdir()


if __name__ == "__main__":
    main()
