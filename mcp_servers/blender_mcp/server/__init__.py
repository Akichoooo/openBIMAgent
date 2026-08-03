"""openBIMAAgent fork of ahujasid/blender-mcp (MCP stdio server side)."""

from .server import BlenderConnection, get_blender_connection, mcp

FORK_VERSION = "1.0.0-m0"

__all__ = ["BlenderConnection", "FORK_VERSION", "get_blender_connection", "mcp"]
