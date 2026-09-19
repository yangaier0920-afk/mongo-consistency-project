import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
old=root/'provenance/recorded_source'
for p in old.glob('*.py'):
    target=root/'experiments'/p.name.replace('test_v2','test_consistency')
    expected=p.read_bytes().replace(b'experiments_v2',b'experiments').replace(b'test_v2',b'test_consistency')
    if not target.is_file() or target.read_bytes()!=expected:
        raise SystemExit('Unexpected code change: '+str(target))
for role,name in json.loads((root/'results/index.json').read_text()).items():
    batch=root/name
    for filename,h in json.loads((batch/'plan.json').read_text())['source_sha256'].items():
        if hashlib.sha256((old/filename).read_bytes()).hexdigest()!=h:
            raise SystemExit('Recorded source mismatch: '+filename)
    for filename,h in json.loads((batch/'SHA256SUMS.json').read_text()).items():
        p=(batch/filename).resolve()
        if not p.is_relative_to(batch.resolve()) or hashlib.sha256(p.read_bytes()).hexdigest()!=h:
            raise SystemExit('Result mismatch: '+filename)
    print(role+': source and result hashes match')
print('Exact naming-only migration: PASS')
