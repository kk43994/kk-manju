# KK_MANJU_MANAGED_ENTRY v1
import json,runpy,sys
from pathlib import Path
state=Path('/Users/zhoukaihao/.local/share/kk-manju-companion')
arguments=sys.argv[1:]
verbs={'serve','start','run','project','issue','state','pending','receipt'}
end=next((i for i,x in enumerate(arguments) if x in verbs),len(arguments))
if '--state-dir' in arguments[:end]:
    state=Path(arguments[arguments.index('--state-dir')+1]).expanduser()
active=json.loads((state/'cloud/active.json').read_text())
root=Path(active['root']).resolve()
if root.parent!=(state/'cloud/releases').resolve():
    raise SystemExit('自动更新启动目录不匹配')
sys.path.insert(0,str(root/'src'))
runpy.run_path(str(root/'src/companion.py'),run_name='__main__')
