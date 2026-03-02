"""Dashboard generation tools."""

import subprocess
from fastmcp import FastMCP

from config import PUBLIC_BASE_URL


def register(mcp: FastMCP):

    @mcp.tool()
    def dashboard_generate(domain: str = "burrillville") -> str:
        """Regenerate the project dashboard for a domain.

        Queries Store for all projects, contacts, and relationships,
        then generates an interactive HTML dashboard and publishes it.

        Args:
            domain: Domain to generate dashboard for (default: burrillville)
        """
        try:
            result = subprocess.run(
                ["python3", "/data/repos/vigil/scripts/project_dashboard.py",
                 "--domain", domain],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0:
                return f"❌ Dashboard generation failed:\\n{result.stderr}"

            # Copy to domain-specific output
            import shutil
            from config import OUTPUTS_DIR
            src = OUTPUTS_DIR / "project-dashboard.html"
            dest_dir = OUTPUTS_DIR / domain
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_dir / "project-dashboard.html")

            url = f"{PUBLIC_BASE_URL}/{domain}/project-dashboard.html"
            return f"✅ Dashboard regenerated\\n{result.stdout.strip()}\\n📎 {url}"

        except subprocess.TimeoutExpired:
            return "❌ Dashboard generation timed out (30s)"
        except Exception as e:
            return f"❌ Error: {e}"
