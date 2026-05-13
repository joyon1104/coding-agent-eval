#!/usr/bin/env python3
"""Coding-Agent-Eval Dashboard Server.

Lightweight HTTP server that serves the dashboard UI and provides
API endpoints to read evaluation results from results/runs/.

Usage:
    python dashboard/server.py [--port 8080]
"""

import json
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Project paths
DASHBOARD_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DASHBOARD_DIR.parent
RESULTS_DIR = PROJECT_ROOT / "results" / "runs"
STATIC_DIR = DASHBOARD_DIR / "static"


class DashboardHandler(SimpleHTTPRequestHandler):
    """HTTP handler that serves static files and API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            self._handle_api(path, parsed.query)
        else:
            # Serve static files (SPA: fallback to index.html)
            if path == "/":
                self.path = "/index.html"
            super().do_GET()

    def _handle_api(self, path, query):
        try:
            if path == "/api/runs":
                self._api_runs()
            elif path.startswith("/api/runs/") and path.endswith("/summary"):
                run_id = path[len("/api/runs/"):-len("/summary")]
                self._api_summary(run_id)
            elif path.startswith("/api/runs/") and "/eval/" in path:
                # /api/runs/{run_id}/eval/{instance_id}
                rest = path[len("/api/runs/"):]
                parts = rest.split("/eval/")
                if len(parts) == 2:
                    self._api_eval_detail(parts[0], parts[1])
                else:
                    self._send_error(400, "Invalid eval path")
            elif path.startswith("/api/runs/") and path.endswith("/patches"):
                run_id = path[len("/api/runs/"):-len("/patches")]
                self._api_patches(run_id)
            else:
                self._send_error(404, "Unknown API endpoint")
        except Exception as e:
            self._send_error(500, str(e))

    def _api_runs(self):
        """List all runs with summary data."""
        runs = []
        if not RESULTS_DIR.exists():
            self._send_json(runs)
            return

        for run_dir in sorted(RESULTS_DIR.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue

            run_info = {"run_id": run_dir.name}

            # Try summary.json first (new layout)
            summary_path = run_dir / "summary.json"
            if summary_path.exists():
                summary = json.loads(summary_path.read_text())
                per_task = summary.get("per_task", [])
                total_tokens = sum(t.get("tokens", 0) for t in per_task)
                run_info.update({
                    "agent": summary.get("agent", ""),
                    "model": summary.get("model", ""),
                    "tier": summary.get("tier", ""),
                    "num_tasks": summary.get("num_tasks", 0),
                    "started_at": summary.get("started_at", ""),
                    "completed_at": summary.get("completed_at", ""),
                    "total_tokens": total_tokens,
                    "metrics": {},
                })
                # Extract top-level metrics from first agent
                for agent_name, agent_data in summary.get("agents", {}).items():
                    run_info["metrics"] = agent_data.get("metrics", {})
                    break
                runs.append(run_info)
                continue

            # Fallback: metadata.json (legacy)
            meta_path = run_dir / "metadata.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                run_info.update({
                    "agent": meta.get("agent", meta.get("agents", ["unknown"])[0] if meta.get("agents") else "unknown"),
                    "model": meta.get("model", ""),
                    "tier": meta.get("tier", ""),
                    "num_tasks": meta.get("num_tasks", 0),
                    "started_at": meta.get("started_at", ""),
                    "completed_at": meta.get("completed_at", ""),
                    "metrics": {},
                })
                runs.append(run_info)

        self._send_json(runs)

    def _api_summary(self, run_id):
        """Get full summary for a run."""
        run_dir = RESULTS_DIR / run_id

        # Try summary.json
        summary_path = run_dir / "summary.json"
        if summary_path.exists():
            self._send_json(json.loads(summary_path.read_text()))
            return

        # Fallback: metadata.json
        meta_path = run_dir / "metadata.json"
        if meta_path.exists():
            self._send_json(json.loads(meta_path.read_text()))
            return

        self._send_error(404, f"Run not found: {run_id}")

    def _api_eval_detail(self, run_id, instance_id):
        """Get eval detail for a specific instance."""
        eval_path = RESULTS_DIR / run_id / "eval" / f"{instance_id}.json"
        if eval_path.exists():
            self._send_json(json.loads(eval_path.read_text()))
            return
        self._send_error(404, f"Eval not found: {run_id}/{instance_id}")

    def _api_patches(self, run_id):
        """List patch results for a run."""
        run_dir = RESULTS_DIR / run_id
        patches = []

        # New layout: patches/
        patches_dir = run_dir / "patches"
        if patches_dir.is_dir():
            for f in sorted(patches_dir.glob("*.json")):
                try:
                    data = json.loads(f.read_text())
                    # Exclude raw_output to keep response small
                    data.pop("raw_output", None)
                    patches.append(data)
                except (json.JSONDecodeError, KeyError):
                    continue
            self._send_json(patches)
            return

        # Legacy: agent-name subdirectories
        for agent_dir in run_dir.iterdir():
            if not agent_dir.is_dir() or agent_dir.name in ("eval", "reports"):
                continue
            for f in sorted(agent_dir.glob("*.json")):
                if f.name == "metadata.json":
                    continue
                try:
                    data = json.loads(f.read_text())
                    data.pop("raw_output", None)
                    patches.append(data)
                except (json.JSONDecodeError, KeyError):
                    continue

        self._send_json(patches)

    def _send_json(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code, message):
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Quieter logging
        if "/api/" in str(args[0]):
            return
        super().log_message(format, *args)


def main():
    port = 8080
    if len(sys.argv) > 1:
        for i, arg in enumerate(sys.argv[1:]):
            if arg == "--port" and i + 2 < len(sys.argv):
                port = int(sys.argv[i + 2])
            elif arg.isdigit():
                port = int(arg)

    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Coding-Agent-Eval Dashboard: http://localhost:{port}")
    print(f"Results dir: {RESULTS_DIR}")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
