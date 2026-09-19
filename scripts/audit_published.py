import json,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
for role,name in json.loads((root/'results/index.json').read_text()).items():
    print('Auditing '+role,flush=True)
    subprocess.run([sys.executable,'-B','-m','provenance.recorded_source.audit',str(root/name)],cwd=root,check=True)
