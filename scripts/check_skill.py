"""Validate SKILL.md frontmatter and defaults.json: what decides whether the skill loads and triggers.

Run from the repository root: python3 scripts/check_skill.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
m = re.match(r"---\n(.*?)\n---\n", text, re.S)
if not m:
    sys.exit("SKILL.md has no frontmatter block")

fields = dict(re.findall(r'^(\w+):\s*"?(.*?)"?\s*$', m.group(1), re.M))
desc = fields.get("description", "")
errors = []
if fields.get("name") != "find-manual":
    errors.append(f"name must be 'find-manual', got {fields.get('name')!r}")
if not desc:
    errors.append("description is missing")
if len(desc) > 1024:
    errors.append(f"description is {len(desc)} chars, max 1024")
if "<" in desc or ">" in desc:
    errors.append("description may not contain < or >")
extra = set(fields) - {"name", "description"}
if extra:
    errors.append(f"frontmatter must hold only name and description, found {sorted(extra)}")

try:
    defaults = json.loads((ROOT / "defaults.json").read_text(encoding="utf-8"))
    for key in ("storage_dir", "manuals_dirs", "suppliers", "retention", "max_cache_mb",
                "answer_language", "manual_languages", "doubt_threshold"):
        if key not in defaults:
            errors.append(f"defaults.json is missing {key!r}")
except (OSError, ValueError) as e:
    errors.append(f"defaults.json: {e}")

if errors:
    sys.exit("\n".join(errors))
print(f"ok: name={fields['name']}, description {len(desc)} chars, defaults.json valid")
