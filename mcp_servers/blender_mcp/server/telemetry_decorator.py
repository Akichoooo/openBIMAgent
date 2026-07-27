"""OPENBIMAGENT (a): no-op telemetry decorators.

Replaces upstream src/blender_mcp/telemetry_decorator.py. The forked server
does not decorate any tool with these; the module exists only so that code
written against the upstream API keeps importing. Both decorators return the
wrapped function untouched.
"""

import functools


def telemetry_tool(tool_name: str):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator


def rich_telemetry_tool(tool_name: str, capture_code: bool = False):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator
