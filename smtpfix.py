#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path

MANIFEST = Path(__file__).with_name('manifest.json')


def load_manifest():
    return json.loads(MANIFEST.read_text())


def sha256(path):
    h = hashlib.sha256()
    with open(path,'rb') as f:
        while True:
            b=f.read(65536)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def verify(smtp_path):
    manifest=load_manifest()
    expected=manifest['smtp']['sha256']
    actual=sha256(smtp_path)
    print('Expected:',expected)
    print('Actual  :',actual)
    print('PASS' if expected==actual else 'FAIL')
    return 0 if expected==actual else 1

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('command',choices=['verify'])
    p.add_argument('--smtp',default='/usr/lib/python2.7/site-packages/sendmail/mailer/smtp.py')
    a=p.parse_args()
    raise SystemExit(verify(a.smtp))
