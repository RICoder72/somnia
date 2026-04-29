"""Dashboard generation tools."""

import subprocess
from pathlib import Path
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
                ["python3", "/data/repos/somnia/vigil/scripts/project_dashboard.py",
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

    @mcp.tool()
    def cost_savings_report_generate(domain: str = "burrillville") -> str:
        """Regenerate the cost & savings report for a domain.

        Queries Store for accomplishments with financial_impact populated, plus
        a curated list of accomplishments awaiting financial assessment, and
        renders a portal-ready HTML report into the workspace's reports/ folder.

        Args:
            domain: Domain to generate report for (default: burrillville)
        """
        try:
            output_path = Path(f"/data/workspaces/{domain}/reports/cost-savings-report.html")
            output_path.parent.mkdir(parents=True, exist_ok=True)

            result = subprocess.run(
                ["python3", "/data/repos/somnia/vigil/scripts/cost_savings_report.py",
                 "--domain", domain,
                 "--output", str(output_path)],
                capture_output=True, text=True, timeout=30
            )

            if result.returncode != 0:
                return f"❌ Cost savings report generation failed:\\n{result.stderr}"

            portal_url = f"https://zanni.synology.me/portal/reports/{domain}/view?file=cost-savings-report.html"
            return (f"✅ Cost & savings report regenerated\\n"
                    f"{result.stdout.strip()}\\n"
                    f"📎 {portal_url}")

        except subprocess.TimeoutExpired:
            return "❌ Cost savings report generation timed out (30s)"
        except Exception as e:
            return f"❌ Error: {e}"

