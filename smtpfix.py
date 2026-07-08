#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

MANIFEST=Path(__file__).with_name('manifest.json')
DEFAULT_SMTP='/usr/lib/python2.7/site-packages/sendmail/mailer/smtp.py'

def load_manifest():
    return json.loads(MANIFEST.read_text())

def local_sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(65536),b''):
            h.update(chunk)
    return h.hexdigest()

def remote_sha256(host,user,path):
    cmd=['ssh',f'{user}@{host}',f'sha256sum {path}']
    out=subprocess.check_output(cmd,text=True)
    return out.split()[0]

def verify(args):
    expected=load_manifest()['smtp']['sha256']
    actual=remote_sha256(args.host,args.user,args.smtp) if args.host else local_sha256(args.smtp)
    print('Expected:',expected)
    print('Actual  :',actual)
    return 0 if actual==expected else 1

p=argparse.ArgumentParser()
sub=p.add_subparsers(dest='cmd')
v=sub.add_parser('verify')
v.add_argument('--host')
v.add_argument('--user',default='personalcloud')
v.add_argument('--smtp',default=DEFAULT_SMTP)
a=p.parse_args()
if a.cmd=='verify':
    raise SystemExit(verify(a))
p.print_help()
