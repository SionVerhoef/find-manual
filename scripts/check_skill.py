"""Validate SKILL.md frontmatter: the fields that decide whether the skill loads and triggers."""
import re
import sys

text = open("SKILL.md", encoding="utf-8").read()
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
if errors:
    sys.exit("\n".join(errors))
print(f"ok: name={fields['name']}, description {len(desc)} chars")
