# OPENBIMAGENT fork of ahujasid/blender-mcp src/blender_mcp/server.py
# UPSTREAM: https://github.com/ahujasid/blender-mcp @ da4e16d2069ce5154eaa2535bf995e843caf5c73 (v1.6.0)
# Pristine baseline: ../vendor/server.py
# Fork changes (marked "OPENBIMAGENT (<item>)"):
#   (a) telemetry imports/calls removed entirely (hard off, see ./telemetry.py stub)
#   (d) tools slimmed to 11 (<=12): Polyhaven/Sketchfab/Hyper3D/Hunyuan removed;
#       batch_render/camera_turntable/camera_path_render/ping/describe_capabilities/
#       set_editable_scope/restore_snapshot added
#   (e) health check: startup ping probe + first-packet retry + env-tunable timeout
#   (f) get_viewport_screenshot enforces the addon's non-black assertion
#   (h) describe_capabilities tool
# Sandbox (c) and scope lock (g) are enforced addon-side (inside Blender), the
# only place where they cannot be bypassed; this server stays a thin transport.

from mcp.server.fastmcp import FastMCP, Context, Image
import socket
import json
import asyncio  # noqa: F401  (kept for parity with upstream imports)
import logging
import tempfile
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any
import os
import sys
import time

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BlenderMCPServer")

# Default configuration
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9876

# OPENBIMAGENT: fork identity
FORK_VERSION = "1.0.0-m0"
UPSTREAM_REPO = "https://github.com/ahujasid/blender-mcp"
UPSTREAM_COMMIT = "da4e16d2069ce5154eaa2535bf995e843caf5c73"

# OPENBIMAGENT (e): health-check tuning
COMMAND_TIMEOUT = float(os.getenv("OPENBIMAGENT_BLENDER_TIMEOUT", "180"))
FIRST_PACKET_RETRIES = 2          # reconnect+retry budget for a fresh command
STARTUP_PROBE_RETRIES = 5         # ping attempts right after TCP connect
STARTUP_PROBE_DELAY = 1.0         # seconds between startup ping attempts
AUTHORIZED_ROOT = os.getenv("OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT", "")


@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket = None

    def connect(self) -> bool:
        """Connect to the Blender addon socket server (upstream + OPENBIMAGENT (e) probe)."""
        if self.sock:
            return True

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {str(e)}")
            self.sock = None
            return False

        # OPENBIMAGENT (e): startup probe -- a bare TCP accept is not proof the
        # addon command loop is alive; require a pong before declaring success.
        for attempt in range(1, STARTUP_PROBE_RETRIES + 1):
            try:
                self._send_raw("ping", {})
                return True
            except Exception as e:
                logger.warning(f"Startup ping probe {attempt}/{STARTUP_PROBE_RETRIES} failed: {e}")
                time.sleep(STARTUP_PROBE_DELAY)
        logger.error("Blender addon did not answer ping after TCP connect")
        self.disconnect()
        return False

    def disconnect(self):
        """Disconnect from the Blender addon (upstream)."""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """Receive the complete response in chunks (upstream; timeout now env-tunable)."""
        chunks = []
        sock.settimeout(COMMAND_TIMEOUT)  # OPENBIMAGENT (e): timeout slices

        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        if not chunks:
                            raise Exception("Connection closed before receiving any data")
                        break

                    chunks.append(chunk)

                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        continue
                except socket.timeout:
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise
        except socket.timeout:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise

        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def _send_raw(self, command_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Single attempt at command/response over the current socket."""
        command = {"type": command_type, "params": params or {}}
        self.sock.sendall(json.dumps(command).encode('utf-8'))
        response_data = self.receive_full_response(self.sock)
        response = json.loads(response_data.decode('utf-8'))
        if response.get("status") == "error":
            raise Exception(response.get("message", "Unknown error from Blender"))
        return response.get("result", {})

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Blender and return the response.

        OPENBIMAGENT (e): first-packet retry -- if the first attempt on a (possibly
        stale pooled) socket fails, reconnect and retry before giving up.
        """
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")

        last_err = None
        for attempt in range(1, FIRST_PACKET_RETRIES + 2):
            try:
                logger.info(f"Sending command: {command_type} (attempt {attempt})")
                return self._send_raw(command_type, params)
            except socket.timeout:
                last_err = Exception(
                    f"Timeout waiting for Blender response after {COMMAND_TIMEOUT:.0f}s "
                    f"(command={command_type})")
                logger.error(str(last_err))
                self.sock = None  # timeout mid-command: do not retry, state unknown
                raise last_err
            except (ConnectionError, BrokenPipeError, ConnectionResetError, OSError) as e:
                last_err = e
                logger.warning(f"Connection error on attempt {attempt}: {e}")
                self.sock = None
                if attempt <= FIRST_PACKET_RETRIES:
                    time.sleep(0.5)
                    if not self.connect():
                        last_err = ConnectionError("Reconnect to Blender failed")
                        continue
            except Exception:
                # Addon-side error (status=error) or bad JSON: not retryable.
                raise
        raise Exception(f"Communication error with Blender after retries: {last_err}")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle (upstream pattern, telemetry removed)."""
    try:
        logger.info(f"BlenderMCP fork server starting up (fork {FORK_VERSION}, "
                    f"upstream {UPSTREAM_REPO}@{UPSTREAM_COMMIT[:8]})")

        # OPENBIMAGENT (e): startup probe -- verify Blender is reachable early.
        try:
            get_blender_connection()
            logger.info("Successfully connected to Blender on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Blender on startup: {str(e)}")
            logger.warning("Make sure the Blender addon is running before using Blender tools")

        yield {}
    finally:
        global _blender_connection
        if _blender_connection:
            logger.info("Disconnecting from Blender on shutdown")
            _blender_connection.disconnect()
            _blender_connection = None
        logger.info("BlenderMCP fork server shut down")


mcp = FastMCP(
    "BlenderMCP-openBIMAgent",
    lifespan=server_lifespan
)

# Global connection for resources (upstream pattern)
_blender_connection = None


def get_blender_connection():
    """Get or create a persistent Blender connection.

    OPENBIMAGENT (e): liveness is validated with the fork's `ping` command
    (upstream used get_polyhaven_status, which no longer exists).
    """
    global _blender_connection

    if _blender_connection is not None:
        try:
            _blender_connection.send_command("ping")
            return _blender_connection
        except Exception as e:
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _blender_connection.disconnect()
            except Exception:
                pass
            _blender_connection = None

    if _blender_connection is None:
        host = os.getenv("BLENDER_HOST", DEFAULT_HOST)
        port = int(os.getenv("BLENDER_PORT", DEFAULT_PORT))
        _blender_connection = BlenderConnection(host=host, port=port)
        if not _blender_connection.connect():
            _blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        logger.info("Created new persistent connection to Blender")

    return _blender_connection


# ---------------------------------------------------------------------------
# tools (OPENBIMAGENT (d): 11 tools, <=12 budget)
# ---------------------------------------------------------------------------

@mcp.tool()
def ping(ctx: Context) -> str:
    """OPENBIMAGENT (e): health check. Returns pong, fork version, host Blender version."""
    try:
        blender = get_blender_connection()
        result = blender.send_command("ping")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Ping failed: {str(e)}")
        return f"Ping failed: {str(e)}"


@mcp.tool()
def describe_capabilities(ctx: Context) -> str:
    """OPENBIMAGENT (h): server version, host Blender version, tool manifest,
    limits (sandbox/scope/screenshot), and known issues (Blender 5.2 notes).
    Agents should call this first to align with the fork."""
    try:
        blender = get_blender_connection()
        result = blender.send_command("describe_capabilities")
        result["mcp_server"] = {
            "fork_version": FORK_VERSION,
            "upstream": {"repo": UPSTREAM_REPO, "commit": UPSTREAM_COMMIT},
            "command_timeout_s": COMMAND_TIMEOUT,
            "first_packet_retries": FIRST_PACKET_RETRIES,
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"describe_capabilities failed: {str(e)}")
        return f"Error describing capabilities: {str(e)}"


@mcp.tool()
def get_scene_info(ctx: Context) -> str:
    """Get detailed information about the current Blender scene (upstream)."""
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_scene_info")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting scene info from Blender: {str(e)}")
        return f"Error getting scene info: {str(e)}"


@mcp.tool()
def get_object_info(ctx: Context, object_name: str) -> str:
    """Get detailed information about a specific object in the Blender scene (upstream).

    Parameters:
    - object_name: The name of the object to get information about
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("get_object_info", {"name": object_name})
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting object info from Blender: {str(e)}")
        return f"Error getting object info: {str(e)}"


@mcp.tool()
def get_viewport_screenshot(ctx: Context, max_size: int = 800) -> Image:
    """Capture a screenshot of the current Blender 3D viewport.

    OPENBIMAGENT (f): the addon asserts the image is not black (mean luminance
    >= threshold); headless it automatically falls back to an offscreen render.
    This tool raises if a black frame would be returned.

    Parameters:
    - max_size: Maximum size in pixels for the largest dimension

    Returns the screenshot as an Image.
    """
    try:
        blender = get_blender_connection()

        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"blender_screenshot_{os.getpid()}.png")

        result = blender.send_command("get_viewport_screenshot", {
            "max_size": max_size,
            "filepath": temp_path,
            "format": "png"
        })

        if "error" in result:
            raise Exception(result["error"])

        # OPENBIMAGENT (f): double-check the non-black assertion server-side
        brightness = result.get("brightness", 0.0)
        if brightness < 0.01:
            raise Exception(f"Screenshot rejected: mean luminance {brightness:.4f} below threshold")

        if not os.path.exists(temp_path):
            raise Exception("Screenshot file was not created")

        with open(temp_path, 'rb') as f:
            image_bytes = f.read()
        os.remove(temp_path)

        logger.info(f"Screenshot ok: method={result.get('method')} brightness={brightness:.4f}")
        return Image(data=image_bytes, format="png")
    except Exception as e:
        logger.error(f"Error capturing screenshot: {str(e)}")
        raise Exception(f"Screenshot failed: {str(e)}")


@mcp.tool()
def execute_plan(
    ctx: Context,
    plan: Dict[str, Any],
    output_path: str,
    approved: bool = False,
) -> str:
    """Execute one approved typed municipal plan; never translates it to Python code."""
    try:
        if not AUTHORIZED_ROOT:
            raise ValueError("OPENBIMAGENT_BLENDER_AUTHORIZED_ROOT is required")
        target = os.path.abspath(output_path)
        root = os.path.abspath(AUTHORIZED_ROOT)
        if os.path.commonpath([root, target]) != root:
            raise ValueError(f"output_path escaped authorized root: {target}")
        if not target.lower().endswith(".blend"):
            raise ValueError("output_path must end with .blend")
        blender = get_blender_connection()
        result = blender.send_command(
            "execute_plan",
            {"plan": plan, "output_path": target, "approved": approved},
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error executing typed plan: {str(e)}")
        raise Exception(f"Typed plan execution failed: {str(e)}")


@mcp.tool()
def execute_blender_code(ctx: Context, code: str) -> str:
    """Execute Python code in Blender, step-by-step in small chunks.

    OPENBIMAGENT (c/g), enforced addon-side:
    - AST allowlist: imports limited to bpy/bmesh/mathutils/math; no os/sys/
      subprocess/socket/open/exec/eval/__import__/getattr, no dunder access.
    - a .blend snapshot is saved automatically before execution;
    - if an editable scope is set (set_editable_scope), out-of-scope changes
      roll back to the snapshot and raise an error;
    - runtime exceptions also roll back.

    Parameters:
    - code: The Python code to execute
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("execute_code", {"code": code})
        return (f"Code executed successfully: {result.get('result', '')}\n"
                f"snapshot: {result.get('snapshot', '')}")
    except Exception as e:
        logger.error(f"Error executing code: {str(e)}")
        return f"Error executing code: {str(e)}"


@mcp.tool()
def set_editable_scope(ctx: Context, objects: list[str] = None,
                       collections: list[str] = None, enabled: bool = True) -> str:
    """OPENBIMAGENT (g): restrict execute_code to a whitelist of object names
    and/or collection names. After each execute_code, any created/modified/
    deleted object outside the scope rolls the scene back to the pre-execution
    snapshot and returns an error. Call with enabled=False to unlock.

    Parameters:
    - objects: object names that may be modified
    - collections: collection names whose members (recursively) may be modified
    - enabled: False clears the scope entirely
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("set_editable_scope", {
            "objects": objects or [],
            "collections": collections or [],
            "enabled": enabled,
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error setting editable scope: {str(e)}")
        return f"Error setting editable scope: {str(e)}"


@mcp.tool()
def restore_snapshot(ctx: Context, path: str = None) -> str:
    """OPENBIMAGENT (c/g): reload a .blend snapshot taken before execute_code.
    Defaults to the most recent snapshot.

    Parameters:
    - path: snapshot file path, or omit for latest
    """
    try:
        blender = get_blender_connection()
        result = blender.send_command("restore_snapshot", {"path": path} if path else {})
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error restoring snapshot: {str(e)}")
        return f"Error restoring snapshot: {str(e)}"


@mcp.tool()
def batch_render(ctx: Context, output_dir: str, cameras: list[str] = None,
                 width: int = 512, height: int = 512, engine: str = None) -> str:
    """OPENBIMAGENT (d): render one still image per camera.

    Parameters:
    - output_dir: directory for batch_XXX_<camera>.png files
    - cameras: camera object names; omit to render every camera in the scene
    - width/height: render resolution
    - engine: render engine id; omit for auto-probe (5.2-safe: BLENDER_EEVEE first)

    Returns per-camera file paths and brightness stats; all_nonblack=False if
    any frame is black.
    """
    try:
        blender = get_blender_connection()
        params = {"output_dir": output_dir, "width": width, "height": height}
        if cameras is not None:
            params["cameras"] = cameras
        if engine:
            params["engine"] = engine
        result = blender.send_command("batch_render", params)
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in batch_render: {str(e)}")
        return f"Error in batch_render: {str(e)}"


@mcp.tool()
def camera_turntable(ctx: Context, output_dir: str, target: str = None,
                     target_location: list[float] = None, radius: float = None,
                     height: float = None, frames: int = 8,
                     width: int = 512, engine: str = None) -> str:
    """OPENBIMAGENT (d): orbit a temporary camera around a target and render
    `frames` stills (turntable_XXX.png). The temp camera is removed afterwards.

    Parameters:
    - output_dir: directory for frames
    - target: object name to orbit (or use target_location / scene center)
    - target_location: [x,y,z] look-at point if no target object
    - radius: orbit radius (default: auto from target size)
    - height: camera Z height (default: auto)
    - frames: number of frames around the circle
    - width: frame size in pixels (square)
    - engine: render engine id; omit for auto-probe
    """
    try:
        blender = get_blender_connection()
        params = {"output_dir": output_dir, "frames": frames, "width": width}
        if target is not None:
            params["target"] = target
        if target_location is not None:
            params["target_location"] = target_location
        if radius is not None:
            params["radius"] = radius
        if height is not None:
            params["height"] = height
        if engine:
            params["engine"] = engine
        result = blender.send_command("camera_turntable", params)
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in camera_turntable: {str(e)}")
        return f"Error in camera_turntable: {str(e)}"


@mcp.tool()
def camera_path_render(ctx: Context, output_dir: str, points: list[list[float]],
                       target: str = None, target_location: list[float] = None,
                       width: int = 512, engine: str = None) -> str:
    """OPENBIMAGENT (d): move a temporary camera through waypoints and render
    each frame (path_XXX.png) -- a walkthrough/flythrough frame sequence.
    The temp camera is removed afterwards.

    Parameters:
    - output_dir: directory for frames
    - points: [[x,y,z], ...] camera positions, one rendered frame per point
    - target: object name to look at (or target_location / scene center)
    - target_location: [x,y,z] look-at point if no target object
    - width: frame size in pixels (square)
    - engine: render engine id; omit for auto-probe
    """
    try:
        blender = get_blender_connection()
        params = {"output_dir": output_dir, "points": points, "width": width}
        if target is not None:
            params["target"] = target
        if target_location is not None:
            params["target_location"] = target_location
        if engine:
            params["engine"] = engine
        result = blender.send_command("camera_path_render", params)
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error in camera_path_render: {str(e)}")
        return f"Error in camera_path_render: {str(e)}"


# Main execution

def main():
    """Run the MCP server (upstream pattern)."""
    try:
        interactive = sys.stdin.isatty()
    except (AttributeError, OSError):
        interactive = False
    if interactive:
        logger.info(
            "BlenderMCP (openBIMAgent fork) is an MCP server and is meant to be "
            "launched by your MCP client, not run by hand. It will now wait "
            "silently for a client on stdin -- that is normal, not a hang. "
            "Press Ctrl-C to exit."
        )
    mcp.run()


if __name__ == "__main__":
    main()
