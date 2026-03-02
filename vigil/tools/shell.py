"""
Shell execution tool.
"""

from fastmcp import FastMCP

from core.shell import run_simple


def register(mcp: FastMCP):

    @mcp.tool()
    def shell_exec(command: str, timeout: int = 30) -> str:
        """Execute shell command in the Vigil container."""
        return run_simple(command, timeout)
