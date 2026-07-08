# PersonalCloudToolkit

SMTP fix toolkit for Seagate Personal Cloud.

Target:

- Firmware: 4.3.19.7
- Kernel: 3.10.72
- NAS Python: 2.7.14
- NAS OpenSSL: 1.0.2k

The toolkit runs on Windows, Linux, or macOS and talks to the NAS only over SSH.
It is not installed on the NAS.

## Scope

This project only fixes the Seagate sendmail SMTP envelope sender problem.

It does not add SMART, Plex, UPS, backup, monitoring, or other NAS features.

## Commands

Run commands from this repository:

```bash
python smtpfix.py verify --host personalcloud.local --user root
python smtpfix.py test --host personalcloud.local --user root
python smtpfix.py install --host personalcloud.local --user root
```

The command name shown by the CLI is `smtpfix`.

## verify

`verify` connects to the NAS over SSH and checks:

- SSH connection
- Firmware
- Kernel
- Python version
- OpenSSL version
- `/usr/lib/python2.7/site-packages/sendmail/mailer/smtp.py`
- Original `smtp.py` SHA256

It makes no changes on the NAS.

Example:

```bash
python smtpfix.py verify --host 192.168.1.50 --user root
```

Expected output:

```text
[OK] SSH: root@192.168.1.50
[OK] Firmware: 4.3.19.7
[OK] Kernel: 3.10.72 (expected 3.10.72)
[OK] Python: Python 2.7.14
[OK] OpenSSL: OpenSSL 1.0.2k ...
[OK] smtp.py: /usr/lib/python2.7/site-packages/sendmail/mailer/smtp.py
[OK] SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
VERIFY OK
```

If the SHA256 does not match, the installer must not patch the file.

## test

`test` connects to the NAS over SSH, reads `/etc/sendmail/user_config.json`, and
tests the configured SMTP service from the NAS.

It checks:

- SMTP configuration can be read
- DNS lookup
- SSL or STARTTLS negotiation when configured
- SMTP login using the configured `auth_user`
- SMTP `NOOP`

It makes no changes on the NAS and does not patch `smtp.py`.

Example:

```bash
python smtpfix.py test --host 192.168.1.50 --user root
```

Expected output:

```text
[OK] Configuration: host=smtp.domeneshop.no port=587 auth_user=bildesiden1 ssl=False starttls=True
[OK] DNS: smtp.domeneshop.no resolved to 2 address(es)
[OK] SSL: STARTTLS negotiated
[OK] SMTP login: bildesiden1
[OK] NOOP: 250 b'OK'
```

The SMTP password is read on the NAS and is never printed.

## install

`install` patches only the SMTP envelope sender used by Seagate's sendmail
package. SMTP authentication still uses the configured `auth_user`; the password
is not changed.

Default envelope sender:

```text
personalcloud@bildesiden.com
```

Install sequence:

1. Preflight SHA256 check while the NAS is still read-only
2. Remount `/` read-write
3. Backup `smtp.py`
4. Re-check SHA256
5. Patch the `server.sendmail(sender_address, ...)` envelope argument
6. Run `py_compile` on the patched file
7. Remount `/` read-only

If any step fails after the backup is ready, `install` automatically restores the
original `smtp.py` from the verified backup and then remounts read-only.

Example:

```bash
python smtpfix.py install --host 192.168.1.50 --user root
```

Expected output:

```text
Installing SMTP envelope sender patch
Envelope sender: personalcloud@bildesiden.com
[OK] Preflight SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] Remount: / rw
[OK] Backup: /usr/lib/python2.7/site-packages/sendmail/mailer/smtp.py.smtpfix-original
[OK] Install SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] Patch: envelope sender set to personalcloud@bildesiden.com
[OK] Patched SHA256: ...
[OK] Remount: / ro
INSTALL OK
```

To use another envelope sender:

```bash
python smtpfix.py install --host 192.168.1.50 --user root --sender personalcloud@example.com
```
