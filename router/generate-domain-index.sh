#!/bin/sh
# generate-domain-index.sh
# Scans /domains/*/documents/ and generates an index.html listing.
# Run manually or via cron whenever domains change.
#
# Usage: ./generate-domain-index.sh [domains_path] [output_file]
#   domains_path: path to domains root (default: /domains)
#   output_file:  where to write index.html (default: /domains-index/index.html)

DOMAINS_PATH="${1:-/domains}"
OUTPUT_FILE="${2:-/domains-index/index.html}"
OUTPUT_DIR="$(dirname "$OUTPUT_FILE")"

mkdir -p "$OUTPUT_DIR"

cat > "$OUTPUT_FILE" <<'HEADER'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Constellation — Domain Documents</title>
  <style>
    :root { --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a; --text: #e0e0e8; --dim: #8888a0; --accent: #7c93ee; --accent-hover: #9db0ff; }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; padding: 2rem; }
    .container { max-width: 720px; margin: 0 auto; }
    h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 0.25rem; }
    .subtitle { color: var(--dim); font-size: 0.9rem; margin-bottom: 2rem; }
    .domain-list { list-style: none; }
    .domain-list li { border-bottom: 1px solid var(--border); }
    .domain-list li:first-child { border-top: 1px solid var(--border); }
    .domain-list a { display: flex; align-items: center; gap: 0.75rem; padding: 0.85rem 0.5rem; color: var(--accent); text-decoration: none; transition: background 0.15s, padding-left 0.15s; }
    .domain-list a:hover { background: var(--surface); padding-left: 1rem; color: var(--accent-hover); }
    .icon { font-size: 1.1rem; opacity: 0.7; }
    .name { font-weight: 500; }
    .count { margin-left: auto; color: var(--dim); font-size: 0.8rem; }
    .footer { margin-top: 2rem; color: var(--dim); font-size: 0.75rem; text-align: center; }
  </style>
</head>
<body>
  <div class="container">
    <h1>Domain Documents</h1>
    <p class="subtitle">Constellation — self-service artifact browser</p>
    <ul class="domain-list">
HEADER

# Scan for domains with a documents/ directory
for domain_dir in "$DOMAINS_PATH"/*/documents; do
  [ -d "$domain_dir" ] || continue
  domain_name="$(basename "$(dirname "$domain_dir")")"

  # Skip template
  [ "$domain_name" = "_template" ] && continue

  # Count files (non-recursive, files only)
  file_count=$(find "$domain_dir" -maxdepth 1 -type f | wc -l)
  [ "$file_count" -eq 0 ] && continue

  # Pluralize
  if [ "$file_count" -eq 1 ]; then
    label="1 file"
  else
    label="$file_count files"
  fi

  cat >> "$OUTPUT_FILE" <<ENTRY
      <li><a href="/domains/${domain_name}/"><span class="icon">📁</span><span class="name">${domain_name}</span><span class="count">${label}</span></a></li>
ENTRY
done

cat >> "$OUTPUT_FILE" <<'FOOTER'
    </ul>
    <p class="footer">Generated TIMESTAMP</p>
  </div>
</body>
</html>
FOOTER

# Stamp the generation time
TIMESTAMP="$(date '+%Y-%m-%d %H:%M')"
sed -i "s|TIMESTAMP|$TIMESTAMP|g" "$OUTPUT_FILE"

echo "Generated $OUTPUT_FILE"
