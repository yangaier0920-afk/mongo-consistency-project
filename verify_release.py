"""Verify published source/data checksums without Docker or third-party packages."""
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    manifest = root / 'RELEASE_SHA256.json'
    if not manifest.exists():
        print('No release manifest found. The final experiment may not be published yet.')
        return 1
    entries = json.loads(manifest.read_text(encoding='utf-8'))
    failures = []
    for name, expected in entries.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            failures.append(f'Missing or invalid path: {name}')
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            failures.append(f'Changed file: {name}')
    for failure in failures:
        print(failure)
    print(f'Checked {len(entries)} files; mismatches: {len(failures)}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
