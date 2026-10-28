"""Scan tracked/staged publication content without printing matching secrets.

This is a heuristic guard, not a replacement for manual review or OS isolation.
Run --staged immediately before a public commit/push.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
RULES={
    'api_key':re.compile(r'\bsk-[A-Za-z0-9_-]{20,}'),
    'github_token':re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})'),
    'credential_uri':re.compile(r'postgres(?:ql)?://[^\s:/]+:[^\s@]+@'),
    'private_key':re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'personal_windows_path':re.compile(r'(?i)[A-Z]:[\\/]Users[\\/][^\s/\\]+'),
    'private_gold_path':re.compile(r'(?i)[A-Z]:[\\/](?:goldv2|specialized_eval_private|test result)[\\/]'),
    'email':re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
}
FORBIDDEN={'.dump','.zip','.gpg','.pem','.p12','.key','.sqlite','.db','.ipynb'}


def scan(staged=False):
    if staged:
        names=subprocess.check_output(['git','diff','--cached','--name-only','-z'],cwd=ROOT).decode().split('\0')
    else:
        names=[p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file()
            and not any(s.startswith('.') and s not in {'.env.example','.gitignore','.gitattributes'} for s in p.relative_to(ROOT).parts)
            and not any(s in {'__pycache__','outputs','build','dist'} for s in p.relative_to(ROOT).parts)
            and not any(s.endswith('.egg-info') for s in p.parts)]
    findings=[]
    for name in filter(None,names):
        p=ROOT/name
        if p.suffix in FORBIDDEN or (p.name.startswith('.env') and p.name!='.env.example'):
            findings.append({'path':name,'category':'forbidden_artifact'});continue
        raw=subprocess.check_output(['git','show',':'+name],cwd=ROOT) if staged else p.read_bytes()
        if len(raw)>1_000_000:
            findings.append({'path':name,'category':'large_file'});continue
        try:content=raw.decode('utf-8')
        except UnicodeDecodeError:
            findings.append({'path':name,'category':'non_utf8_or_binary'});continue
        for category,pattern in RULES.items():
            for match in pattern.finditer(content):
                findings.append({'path':name,'line':content[:match.start()].count('\n')+1,'category':category})
    print(json.dumps({'status':'FAIL' if findings else 'PASS','files':len([n for n in names if n]),'staged':staged,'findings':findings},indent=2))
    return bool(findings)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--staged',action='store_true')
    raise SystemExit(scan(parser.parse_args().staged))
