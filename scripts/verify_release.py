"""Check this public source manifest and compile ASTs; no external connections."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'RELEASE_MANIFEST.json').read_text(encoding='utf-8'))
for name, expected in manifest['files'].items():
    path=(root/name).resolve()
    if not path.is_relative_to(root) or path.is_symlink():
        raise SystemExit('Invalid release path')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise SystemExit('Hash mismatch: '+name)
    if path.suffix=='.py': ast.parse(path.read_text(encoding='utf-8'),filename=name)
if (root/'.git').exists():
    tracked=set(filter(None,subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')))
    if tracked != set(manifest['files']) | {'RELEASE_MANIFEST.json'}:
        raise SystemExit('Tracked file set differs from release manifest')
print(f"PASS: {len(manifest['files'])} file hashes; Python ASTs parse")
