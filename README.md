# PersonalCloudToolkit

SMTP fix toolkit for Seagate Personal Cloud.

Version: 3.1.0

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

## Runtime values

The tool has no built-in NAS account, host, or envelope sender. You must provide
the connection values on the command line.

For install commands, `--sender` is required. This prevents the tool from
silently installing a private or stale email address.

Target-specific Seagate paths, hashes, and remount defaults live in
`manifest.json`. They can still be overridden with command-line options such as
`--smtp`, `--backup`, `--config`, and `--mount-target`.

## Step-by-step guide for beginners

This is the simple usage path. The examples use anonymized documentation values:

- NAS IP address: `192.0.2.50`
- NAS admin user: `nasadmin`
- Envelope sender to apply: `nas-alerts@example.com`
- SMTP login user shown in output: `smtp-user`
- SMTP server shown in output: `smtp.example.com`

Replace these values with the ones for your NAS and mail account.
Do not copy the example values literally unless they match your setup. For
example, `nasadmin` means "your actual NAS admin username". If SSH rejects that
user, rerun the command with the username that can log in to your NAS.

### 1. Open a terminal on the computer

On Windows, open PowerShell.

Go to the repository folder:

```powershell
cd C:\path\to\PersonalCloudToolkit
```

If `python` works on your computer, run:

```powershell
python smtpfix.py --help
```

If `python` is not on your `PATH`, use the full path to `python.exe`.

### 2. Check the NAS before changing anything

Run:

```powershell
python smtpfix.py verify --host 192.0.2.50 --user nasadmin
```

You may be asked for the SSH password for the NAS user.

The most important checks are:

```text
[OK] SSH
[OK] Kernel
[OK] Python
[OK] OpenSSL
[OK] smtp.py
[OK] SHA256
```

Stop if `SHA256` fails before installation. That means `smtp.py` is not the
known original file, and the tool must not patch it.

If you already installed the patch, a `SHA256` failure is expected because
`smtp.py` is no longer the original file. In that case, use `test` as the
post-installation check. Restore the original file first if you want `verify` to
pass again.

Note: `Firmware` can fail if the Seagate firmware version is not stored in a
known file. The patch is still protected by the SHA256 check.

### 3. Test SMTP without changing the NAS

Run:

```powershell
python smtpfix.py test --host 192.0.2.50 --user nasadmin
```

A successful result looks like this:

```text
[OK] Configuration: host=smtp.example.com port=465 auth_user=smtp-user ssl=True starttls=False
[OK] DNS: smtp.example.com resolved to ...
[OK] SSL: SMTP_SSL connected
[OK] SMTP login: smtp-user
[OK] NOOP: 250 OK
```

This test does not send email. It only checks that the NAS can read the SMTP
configuration, connect to the SMTP provider, log in, and receive a response.

Stop if `SSL`, `SMTP login`, or `NOOP` fails.

### 4. Install the patch

If you log in as a normal NAS admin user and must type a sudo password, use
`manual-install`:

```powershell
python smtpfix.py manual-install --host 192.0.2.50 --user nasadmin --sender nas-alerts@example.com
```

`--sender` is required. Use the email address that should appear as the SMTP
envelope sender and `Return-Path`.

You may be asked for two passwords:

1. The SSH password for `nasadmin`
2. The sudo password after the Seagate root warning appears

When everything works, the end of the output should look like this:

```text
[OK] Patch: envelope sender set to nas-alerts@example.com
[OK] Remount: / ro
INSTALL OK
```

Do not interrupt the installation while it is running. If something fails after
the backup is ready, the tool tries to restore the original file automatically.

### 5. Test again after installation

Run the SMTP test again:

```powershell
python smtpfix.py test --host 192.0.2.50 --user nasadmin
```

Expected result:

```text
[OK] SSL: SMTP_SSL connected
[OK] SMTP login: smtp-user
[OK] NOOP: 250 OK
```

This confirms that SMTP still works after the patch.

Do not use `verify` as the final check after installation. `verify` checks the
original SHA256 value, and that value is expected to change after patching.

### 6. Restore the original file if needed

Run:

```powershell
python smtpfix.py manual-restore --host 192.0.2.50 --user nasadmin
```

Expected ending:

```text
RESTORE OK
```

After restore, the original `smtp.py` is back from the installer backup.

### 7. Short checklist

1. Run `verify` before installation
2. Run `test` before installation
3. Run `manual-install`
4. Run `test` after installation
5. Run `manual-restore` only if you need to undo the patch

The NAS does not need Git, Python 3, or a local installation of this project.
Everything runs from the computer over SSH.

## Commands

Run commands from this repository:

```bash
./smtpfix verify --host nas.example.net --user root
./smtpfix test --host nas.example.net --user root
./smtpfix install --host nas.example.net --user root --sender nas-alerts@example.com
./smtpfix manual-install --host nas.example.net --user nasadmin --sender nas-alerts@example.com
./smtpfix manual-restore --host nas.example.net --user nasadmin
./smtpfix restore --host nas.example.net --user root
```

On Windows, use `smtpfix.cmd` instead of `./smtpfix`.

`--host` and `--user` are required for all commands. `--sender` is required for
`install` and `manual-install`.

If the SSH user is an admin user with passwordless sudo, add `--sudo`.
The tool uses `sudo -n` and fails without making changes if sudo asks for a
password.

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
./smtpfix verify --host 192.0.2.50 --user root
```

Expected output:

```text
[OK] SSH: root@192.0.2.50
[OK] Firmware: 4.3.19.7
[OK] Kernel: 3.10.72 (expected 3.10.72)
[OK] Python: Python 2.7.14
[OK] OpenSSL: OpenSSL 1.0.2k ...
[OK] smtp.py: /usr/lib/python2.7/site-packages/sendmail/mailer/smtp.py
[OK] SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
VERIFY OK
```

If the SHA256 does not match, the installer must not patch the file.
The SHA256 check is the installer gate; firmware detection is a compatibility
check for this target NAS.

Run `verify` before installing the patch, or after restoring the original file.
After installation, `verify` is expected to fail the original SHA256 check.
In that case, the tool explains that `test` is the correct post-installation
check:

```text
[FAIL] SHA256: patched-file-sha256
NOTE: smtp.py is not the original file expected by verify.
NOTE: If the patch is already installed, run 'smtpfix test' as the post-installation check.
NOTE: Restore the original file before running verify as a clean pre-installation check.
VERIFY FAILED
```

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
If SSL/TLS flags are missing, port 465 implies SSL and port 587 implies STARTTLS.
On old NAS OpenSSL builds, `test` seeds the SSL PRNG before connecting.

Example:

```bash
./smtpfix test --host 192.0.2.50 --user root
```

Expected output:

```text
[OK] Configuration: host=smtp.example.com port=587 auth_user=smtp-user ssl=False starttls=True
[OK] DNS: smtp.example.com resolved to 2 address(es)
[OK] SSL: STARTTLS negotiated
[OK] SMTP login: smtp-user
[OK] NOOP: 250 b'OK'
```

The SMTP password is read on the NAS and is never printed.
If required fields are missing, `test` prints available configuration key names
only. It does not print configuration values.

## install

`install` patches only the SMTP envelope sender used by Seagate's sendmail
package. SMTP authentication still uses the configured `auth_user`; the password
is not changed.

`--sender` is required. There is no built-in default envelope sender.

Example envelope sender:

```text
nas-alerts@example.com
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
./smtpfix install --host 192.0.2.50 --user root --sender nas-alerts@example.com
```

With an admin user that has passwordless sudo:

```bash
./smtpfix install --host 192.0.2.50 --user nasadmin --sudo --sender nas-alerts@example.com
```

Expected output:

```text
Installing SMTP envelope sender patch
Envelope sender: nas-alerts@example.com
[OK] Preflight SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] Remount: / rw
[OK] Backup: /usr/lib/python2.7/site-packages/sendmail/mailer/smtp.py.smtpfix-original
[OK] Install SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] Patch: envelope sender set to nas-alerts@example.com
[OK] Patched SHA256: ...
[OK] Remount: / ro
INSTALL OK
```

To use another envelope sender:

```bash
./smtpfix install --host 192.0.2.50 --user root --sender other-sender@example.com
```

## Changing the envelope sender later

`install` and `manual-install` only patch the known original `smtp.py` file. If
the NAS is already patched and you want to change the envelope sender, restore
the original file first and then install again with the new `--sender` value.

Interactive admin-user flow:

```bash
./smtpfix manual-restore --host 192.0.2.50 --user nasadmin
./smtpfix manual-install --host 192.0.2.50 --user nasadmin --sender other-sender@example.com
./smtpfix test --host 192.0.2.50 --user nasadmin
```

Root-user flow:

```bash
./smtpfix restore --host 192.0.2.50 --user root
./smtpfix install --host 192.0.2.50 --user root --sender other-sender@example.com
./smtpfix test --host 192.0.2.50 --user root
```

`--print-script` only prints the generated root script. It does not connect to
the NAS and does not change anything.

## manual-install

`manual-install` is for NAS setups where the admin user can run `sudo`, but SSH
root login or passwordless sudo is not available.

It uploads one temporary root install script under the SSH user's home directory,
runs it with `ssh -tt` and `sudo sh`, then removes the temporary script. You enter
the SSH password and sudo password in your terminal. The root script uses the same
guarded install flow:

1. Preflight SHA256 check
2. Remount `/` read-write
3. Backup `smtp.py`
4. Re-check SHA256
5. Patch the envelope sender
6. Run `py_compile`
7. Remount `/` read-only

If patching fails after backup, the root script restores the verified backup and
remounts read-only.

Example:

```bash
./smtpfix manual-install --host 192.0.2.50 --user nasadmin --sender nas-alerts@example.com
```

To inspect the root script without running it:

```bash
./smtpfix manual-install --host 192.0.2.50 --user nasadmin --sender nas-alerts@example.com --print-script
```

This prints the script only. It does not install the patch.

## manual-restore

`manual-restore` restores the original `smtp.py` through the same interactive
SSH/sudo flow as `manual-install`. Use it when SSH root login or passwordless
sudo is not available.

Example:

```bash
./smtpfix manual-restore --host 192.0.2.50 --user nasadmin
```

Expected output:

```text
Starting one-session manual restore over SSH.
The root script will be uploaded temporarily, run with sudo, and removed.
You may be asked for the SSH password and then the sudo password.
Remote root command: sudo sh
Restoring original smtp.py
[OK] Backup SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] Remount: / rw
[OK] Restore: restored original smtp.py
[OK] SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] Restored SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] Remount: / ro
RESTORE OK
```

## restore

`restore` puts the original `smtp.py` back from the installer backup.

Restore sequence:

1. Remount `/` read-write
2. Verify the backup SHA256
3. Restore original `smtp.py` atomically
4. Run `py_compile`
5. Verify restored SHA256
6. Remount `/` read-only

Example:

```bash
./smtpfix restore --host 192.0.2.50 --user root
```

Expected output:

```text
Restoring original smtp.py
[OK] Remount: / rw
[OK] Restore: restored original smtp.py
[OK] SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] Restored SHA256: 14552f6daadda90eca5b0605dffc7a25c229dfe307c5e5a735d32d4d9e66e95c
[OK] Remount: / ro
RESTORE OK
```
