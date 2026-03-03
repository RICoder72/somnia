#!/usr/bin/env python3
"""Quick CLI output test — run inside the quies container."""
import subprocess, json, os, sys

# Get auth the same way the daemon does
token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
if not token:
    # Try 1password
    r = subprocess.run(["op", "read", "op://Key Vault/Claude Code OAuth/credential"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        token = r.stdout.strip()

if not token:
    print("ERROR: No auth token found")
    sys.exit(1)

env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": token}

TEST_PROMPT = """You are a test. Respond with ONLY this exact JSON block, nothing else:

```json
{
  "summary": "Test response",
  "operations": [
    {"op": "create_edge", "source_id": "test-a", "target_id": "test-b", "type": "test", "weight": 1.0}
  ]
}
```"""

# Test 1: --print --output-format json (current daemon behavior)
print("=== TEST 1: --print --output-format json ===")
r1 = subprocess.run(
    ["claude", "-p", TEST_PROMPT, "--print", "--output-format", "json",
     "--model", "claude-sonnet-4-20250514", "--max-turns", "1"],
    capture_output=True, text=True, timeout=120, env=env)
print(f"Exit code: {r1.returncode}")
print(f"Stdout length: {len(r1.stdout)}")
print(f"Stderr length: {len(r1.stderr)}")
try:
    d1 = json.loads(r1.stdout)
    print(f"JSON keys: {list(d1.keys())}")
    print(f"result field: {repr(d1.get('result', 'MISSING')[:500])}")
    print(f"output_tokens: {d1.get('usage', {}).get('output_tokens', '?')}")
except:
    print(f"Raw stdout: {r1.stdout[:1000]}")
print()

# Test 2: --output-format json WITHOUT --print
print("=== TEST 2: --output-format json (no --print) ===")
r2 = subprocess.run(
    ["claude", "-p", TEST_PROMPT, "--output-format", "json",
     "--model", "claude-sonnet-4-20250514", "--max-turns", "1"],
    capture_output=True, text=True, timeout=120, env=env)
print(f"Exit code: {r2.returncode}")
print(f"Stdout length: {len(r2.stdout)}")
try:
    d2 = json.loads(r2.stdout)
    print(f"JSON keys: {list(d2.keys())}")
    print(f"result field: {repr(d2.get('result', 'MISSING')[:500])}")
    print(f"output_tokens: {d2.get('usage', {}).get('output_tokens', '?')}")
except:
    print(f"Raw stdout: {r2.stdout[:1000]}")
print()

# Test 3: --print only (no --output-format)
print("=== TEST 3: --print only (plain text) ===")
r3 = subprocess.run(
    ["claude", "-p", TEST_PROMPT, "--print",
     "--model", "claude-sonnet-4-20250514", "--max-turns", "1"],
    capture_output=True, text=True, timeout=120, env=env)
print(f"Exit code: {r3.returncode}")
print(f"Stdout length: {len(r3.stdout)}")
print(f"Raw stdout: {r3.stdout[:1000]}")
print()

# Test 4: --output-format stream-json
print("=== TEST 4: --output-format stream-json ===")
r4 = subprocess.run(
    ["claude", "-p", TEST_PROMPT, "--output-format", "stream-json",
     "--model", "claude-sonnet-4-20250514", "--max-turns", "1"],
    capture_output=True, text=True, timeout=120, env=env)
print(f"Exit code: {r4.returncode}")
print(f"Stdout length: {len(r4.stdout)}")
print(f"Raw stdout (first 2000): {r4.stdout[:2000]}")
