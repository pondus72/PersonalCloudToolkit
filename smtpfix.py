#!/usr/bin/env python3
import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

MANIFEST = Path(__file__).with_name("manifest.json")
DEFAULT_SMTP = "/usr/lib/python2.7/site-packages/sendmail/mailer/smtp.py"
DEFAULT_CONFIG = "/etc/sendmail/user_config.json"
DEFAULT_SENDER = "personalcloud@bildesiden.com"
DEFAULT_MOUNT_TARGET = "/"
BACKUP_SUFFIX = ".smtpfix-original"

FIRMWARE_FILES = (
    "/etc/version",
    "/etc/VERSION",
    "/etc/nas_version",
    "/etc/nas-release",
    "/etc/nasos-release",
    "/etc/NASVERSION",
    "/etc/NAS_VERSION",
    "/etc/firmware_version",
    "/etc/firmware",
    "/etc/fw_version",
    "/etc/product_version",
    "/etc/product_info",
    "/etc/os-release",
)

SMTP_TEST_SCRIPT = r'''
from __future__ import print_function
import json
import os
import smtplib
import socket
import ssl
import sys
import time

CONFIG_PATH = sys.argv[1]
TIMEOUT = int(sys.argv[2])


def flatten_dict(value):
    result = {}
    if not isinstance(value, dict):
        return result
    for key, item in value.items():
        key = str(key)
        if isinstance(item, dict):
            nested = flatten_dict(item)
            for nested_key, nested_value in nested.items():
                result.setdefault(nested_key, nested_value)
                result.setdefault(key + "_" + nested_key, nested_value)
        else:
            result[key] = item
    return result


def first(config, keys, default=None):
    for key in keys:
        if key in config and config[key] not in (None, ""):
            return config[key]
    return default


def as_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on", "ssl", "tls", "starttls"):
        return True
    if text in ("0", "false", "no", "off", "none", "plain"):
        return False
    return default


def ok(label, detail):
    print("[OK] %s: %s" % (label, detail))


def fail(label, detail):
    print("[FAIL] %s: %s" % (label, detail))
    raise SystemExit(1)


def seed_ssl_prng():
    rand_add = getattr(ssl, "RAND_add", None)
    if rand_add is None:
        return
    seed_parts = []
    try:
        seed_parts.append(os.urandom(1024))
    except Exception:
        pass
    seed_text = "%s:%s:%s:%s" % (
        time.time(),
        os.getpid(),
        socket.gethostname(),
        CONFIG_PATH,
    )
    try:
        seed_parts.append(seed_text.encode("utf-8"))
    except AttributeError:
        seed_parts.append(seed_text)
    for seed in seed_parts:
        rand_add(seed, float(len(seed)))
    rand_status = getattr(ssl, "RAND_status", None)
    if rand_status is not None and not rand_status():
        fail("SSL", "OpenSSL PRNG not seeded")


def safe_keys(config):
    names = sorted(str(key) for key in config.keys())
    if len(names) > 30:
        names = names[:30] + ["..."]
    return ", ".join(names)


try:
    with open(CONFIG_PATH, "r") as handle:
        raw_config = json.load(handle)
except Exception as exc:
    fail("Configuration", "could not read %s: %s" % (CONFIG_PATH, exc))

config = flatten_dict(raw_config)

host = first(config, ("smtp_host", "smtp_server", "server", "host", "mail_server"))
port = first(config, ("smtp_port", "port", "server_port", "mail_port"))
auth_user = first(config, ("auth_user", "smtp_user", "username", "user", "login"))
auth_pass = first(
    config,
    (
        "auth_pass",
        "auth_password",
        "auth_passwd",
        "auth_pwd",
        "smtp_password",
        "smtp_pass",
        "smtp_passwd",
        "smtp_pwd",
        "mail_password",
        "email_password",
        "password",
        "passwd",
        "pwd",
        "pass",
    ),
)
ssl_setting = first(config, ("smtp_ssl", "ssl", "use_ssl", "ssl_enable", "ssl_enabled"))
tls_setting = first(config, ("smtp_tls", "tls", "starttls", "use_tls", "tls_enable", "tls_enabled"))

if port is None:
    ssl_hint = as_bool(ssl_setting)
    tls_hint = as_bool(tls_setting)
    port = 465 if ssl_hint else 587 if tls_hint else 25

try:
    port = int(port)
except Exception:
    fail("Configuration", "invalid SMTP port: %s" % port)

use_ssl = as_bool(ssl_setting, default=(port == 465))
use_tls = as_bool(tls_setting, default=(port == 587 and not use_ssl))
if use_ssl:
    use_tls = False

missing = []
if not host:
    missing.append("SMTP host")
if not auth_user:
    missing.append("auth_user")
if not auth_pass:
    missing.append("auth password")
if missing:
    fail(
        "Configuration",
        "missing %s in %s; available keys: %s"
        % (", ".join(missing), CONFIG_PATH, safe_keys(config)),
    )

ok(
    "Configuration",
    "host=%s port=%s auth_user=%s ssl=%s starttls=%s"
    % (host, port, auth_user, use_ssl, use_tls),
)

try:
    addresses = socket.getaddrinfo(host, port)
except Exception as exc:
    fail("DNS", exc)
ok("DNS", "%s resolved to %s address(es)" % (host, len(addresses)))

smtp = None
try:
    seed_ssl_prng()
    if use_ssl:
        smtp = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT)
        smtp.ehlo()
        ok("SSL", "SMTP_SSL connected")
    else:
        smtp = smtplib.SMTP(host, port, timeout=TIMEOUT)
        smtp.ehlo()
        if use_tls:
            smtp.starttls()
            smtp.ehlo()
            ok("SSL", "STARTTLS negotiated")
        else:
            ok("SSL", "not configured by user_config.json")
except Exception as exc:
    fail("SSL", exc)

try:
    smtp.login(auth_user, auth_pass)
except Exception as exc:
    fail("SMTP login", exc)
ok("SMTP login", auth_user)

try:
    code, message = smtp.noop()
except Exception as exc:
    fail("NOOP", exc)
if int(code) != 250:
    fail("NOOP", "%s %s" % (code, message))
ok("NOOP", "%s %s" % (code, message))

try:
    smtp.quit()
except Exception:
    pass
'''

PATCH_SCRIPT = r'''
from __future__ import print_function
import hashlib
import json
import os
import py_compile
import stat
import sys

SMTP_PATH = sys.argv[1]
SENDER = sys.argv[2]
EXPECTED_SHA = sys.argv[3]

NEEDLE = b"server.sendmail(sender_address,"
REPLACEMENT = ("server.sendmail(%s," % ("u" + json.dumps(SENDER))).encode("ascii")
TMP_PATH = SMTP_PATH + ".smtpfix.tmp"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fail(label, detail):
    print("[FAIL] %s: %s" % (label, detail))
    raise SystemExit(1)


def ok(label, detail):
    print("[OK] %s: %s" % (label, detail))


try:
    with open(SMTP_PATH, "rb") as handle:
        original = handle.read()
except Exception as exc:
    fail("Patch", "could not read %s: %s" % (SMTP_PATH, exc))

actual_sha = sha256(original)
if actual_sha != EXPECTED_SHA:
    fail("SHA256", "refusing to patch %s" % actual_sha)
ok("SHA256", actual_sha)

count = original.count(NEEDLE)
if count != 1:
    fail("Patch", "expected one sendmail envelope sender call, found %s" % count)

patched = original.replace(NEEDLE, REPLACEMENT, 1)
metadata = os.stat(SMTP_PATH)

try:
    try:
        os.unlink(TMP_PATH)
    except OSError:
        pass
    with open(TMP_PATH, "wb") as handle:
        handle.write(patched)
    os.chmod(TMP_PATH, stat.S_IMODE(metadata.st_mode))
    try:
        os.chown(TMP_PATH, metadata.st_uid, metadata.st_gid)
    except AttributeError:
        pass
    os.rename(TMP_PATH, SMTP_PATH)
    py_compile.compile(SMTP_PATH, doraise=True)
except Exception as exc:
    fail("Patch", exc)

ok("Patch", "envelope sender set to %s" % SENDER)
ok("Patched SHA256", sha256(patched))
'''

RESTORE_SCRIPT = r'''
from __future__ import print_function
import hashlib
import os
import py_compile
import stat
import sys

SMTP_PATH = sys.argv[1]
BACKUP_PATH = sys.argv[2]
EXPECTED_SHA = sys.argv[3]
LABEL = sys.argv[4]
TMP_PATH = SMTP_PATH + ".smtpfix.restore"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fail(label, detail):
    print("[FAIL] %s: %s" % (label, detail))
    raise SystemExit(1)


def ok(label, detail):
    print("[OK] %s: %s" % (label, detail))


try:
    with open(BACKUP_PATH, "rb") as handle:
        original = handle.read()
except Exception as exc:
    fail(LABEL, "could not read backup %s: %s" % (BACKUP_PATH, exc))

backup_sha = sha256(original)
if backup_sha != EXPECTED_SHA:
    fail(LABEL, "backup SHA256 mismatch: %s" % backup_sha)

try:
    metadata = os.stat(SMTP_PATH)
except OSError:
    metadata = os.stat(BACKUP_PATH)

try:
    try:
        os.unlink(TMP_PATH)
    except OSError:
        pass
    with open(TMP_PATH, "wb") as handle:
        handle.write(original)
    os.chmod(TMP_PATH, stat.S_IMODE(metadata.st_mode))
    try:
        os.chown(TMP_PATH, metadata.st_uid, metadata.st_gid)
    except AttributeError:
        pass
    os.rename(TMP_PATH, SMTP_PATH)
    py_compile.compile(SMTP_PATH, doraise=True)
except Exception as exc:
    fail(LABEL, exc)

ok(LABEL, "restored original smtp.py")
ok("SHA256", backup_sha)
'''


def build_manual_install_script(smtp_path, backup_path, sender, expected_sha, mount_target):
    return """#!/bin/sh
SMTP_PATH=%s
BACKUP_PATH=%s
EXPECTED_SHA=%s
SENDER=%s
MOUNT_TARGET=%s

remounted_rw=0
backup_ready=0

sha256_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        out=$(sha256sum "$1") || return 1
        set -- $out
        printf '%%s\\n' "$1"
    else
        out=$(openssl dgst -sha256 "$1") || return 1
        printf '%%s\\n' "${out##* }"
    fi
}

remount_ro() {
    if [ "$remounted_rw" = "1" ]; then
        if mount -o remount,ro "$MOUNT_TARGET"; then
            echo "[OK] Remount: $MOUNT_TARGET ro"
        else
            echo "[FAIL] Remount: $MOUNT_TARGET ro" >&2
            return 1
        fi
    fi
}

rollback() {
    if [ "$backup_ready" = "1" ]; then
        echo "Rollback: restoring backup"
        python - "$SMTP_PATH" "$BACKUP_PATH" "$EXPECTED_SHA" Rollback <<'SMTPFIX_RESTORE_PY'
%s
SMTPFIX_RESTORE_PY
    fi
}

fail() {
    echo "ERROR: $*" >&2
    rollback
    remount_ro
    echo "INSTALL FAILED"
    exit 1
}

if [ "$(id -u)" != "0" ]; then
    echo "ERROR: manual install script must run as root" >&2
    echo "INSTALL FAILED"
    exit 1
fi

echo "Installing SMTP envelope sender patch"
echo "Envelope sender: $SENDER"

actual=$(sha256_file "$SMTP_PATH") || fail "could not calculate preflight SHA256"
if [ "$actual" != "$EXPECTED_SHA" ]; then
    echo "[FAIL] Preflight SHA256: $actual"
    echo "INSTALL FAILED"
    exit 1
fi
echo "[OK] Preflight SHA256: $actual"

mount -o remount,rw "$MOUNT_TARGET" || fail "could not remount $MOUNT_TARGET rw"
remounted_rw=1
echo "[OK] Remount: $MOUNT_TARGET rw"

if [ -f "$BACKUP_PATH" ]; then
    backup_sha=$(sha256_file "$BACKUP_PATH") || fail "could not calculate backup SHA256"
    if [ "$backup_sha" != "$EXPECTED_SHA" ]; then
        fail "existing backup SHA256 mismatch: $backup_sha"
    fi
    echo "[OK] Backup: existing backup verified at $BACKUP_PATH"
else
    cp -p "$SMTP_PATH" "$BACKUP_PATH" || fail "could not create backup"
    backup_sha=$(sha256_file "$BACKUP_PATH") || fail "could not calculate backup SHA256"
    if [ "$backup_sha" != "$EXPECTED_SHA" ]; then
        fail "new backup SHA256 mismatch: $backup_sha"
    fi
    echo "[OK] Backup: $BACKUP_PATH"
fi
backup_ready=1

current=$(sha256_file "$SMTP_PATH") || fail "could not calculate install SHA256"
if [ "$current" != "$EXPECTED_SHA" ]; then
    fail "smtp.py changed before patch: $current"
fi
echo "[OK] Install SHA256: $current"

python - "$SMTP_PATH" "$SENDER" "$EXPECTED_SHA" <<'SMTPFIX_PATCH_PY'
%s
SMTPFIX_PATCH_PY
patch_rc=$?
if [ "$patch_rc" != "0" ]; then
    fail "patch command failed"
fi

remount_ro || {
    echo "INSTALL FAILED"
    exit 1
}
remounted_rw=0
echo "INSTALL OK"
""" % (
        q(smtp_path),
        q(backup_path),
        q(expected_sha),
        q(sender),
        q(mount_target),
        RESTORE_SCRIPT.strip(),
        PATCH_SCRIPT.strip(),
    )


class SmtpFixError(RuntimeError):
    pass


class CommandError(SmtpFixError):
    def __init__(self, command, returncode, stdout, stderr):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__("remote command failed: %s" % command)


class SSHClient:
    def __init__(
        self,
        host,
        user=None,
        port=22,
        identity=None,
        ssh_options=None,
        sudo=False,
    ):
        self.target = "%s@%s" % (user, host) if user else host
        self.sudo = sudo
        self.base_command = ["ssh", "-p", str(port)]
        if identity:
            self.base_command.extend(["-i", identity])
        for option in ssh_options or []:
            self.base_command.extend(["-o", option])
        self.base_command.append(self.target)

    def run(self, command, input_text=None, check=True):
        argv = list(self.base_command)
        argv.append(self.wrap_command(command))
        try:
            result = subprocess.run(
                argv,
                input=input_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
        except FileNotFoundError as exc:
            raise SmtpFixError("ssh command not found on this computer") from exc
        if check and result.returncode != 0:
            raise CommandError(command, result.returncode, result.stdout, result.stderr)
        return result

    def wrap_command(self, command):
        if not self.sudo:
            return command
        return "sudo -n sh -c %s" % q(command)


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def q(value):
    return shlex.quote(value)


def print_check(label, ok, detail):
    status = "OK" if ok else "FAIL"
    print("[%s] %s: %s" % (status, label, detail))


def validate_sender(sender):
    if not sender or not re.match(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$", sender):
        raise SmtpFixError("invalid envelope sender: %s" % sender)


def extract_sha256(output):
    match = re.search(r"\b[0-9a-fA-F]{64}\b", output)
    if not match:
        raise SmtpFixError("could not parse SHA256 from output: %s" % output.strip())
    return match.group(0).lower()


def remote_sha256(ssh, path):
    quoted = q(path)
    command = (
        "if command -v sha256sum >/dev/null 2>&1; then "
        "sha256sum %s; "
        "else openssl dgst -sha256 %s; fi"
    ) % (quoted, quoted)
    result = ssh.run(command)
    return extract_sha256(result.stdout)


def remote_file_exists(ssh, path):
    command = "test -f %s && test -r %s" % (q(path), q(path))
    return ssh.run(command, check=False).returncode == 0


def run_remote_python(ssh, script, script_args):
    command = "python - %s" % " ".join(q(value) for value in script_args)
    result = ssh.run(command, input_text=script, check=False)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode


def remount(ssh, mount_target, mode):
    command = "mount -o remount,%s %s" % (mode, q(mount_target))
    ssh.run(command)
    print_check("Remount", True, "%s %s" % (mount_target, mode))


def ensure_backup(ssh, smtp_path, backup_path, expected_sha):
    if remote_file_exists(ssh, backup_path):
        backup_sha = remote_sha256(ssh, backup_path)
        if backup_sha != expected_sha:
            raise SmtpFixError("existing backup SHA256 mismatch: %s" % backup_sha)
        print_check("Backup", True, "existing backup verified at %s" % backup_path)
        return

    ssh.run("cp -p %s %s" % (q(smtp_path), q(backup_path)))
    backup_sha = remote_sha256(ssh, backup_path)
    if backup_sha != expected_sha:
        raise SmtpFixError("new backup SHA256 mismatch: %s" % backup_sha)
    print_check("Backup", True, backup_path)


def restore_from_backup(ssh, smtp_path, backup_path, expected_sha, label):
    return run_remote_python(
        ssh,
        RESTORE_SCRIPT,
        (smtp_path, backup_path, expected_sha, label),
    )


def base_ssh_argv(args, tty=False):
    target = "%s@%s" % (args.user, args.host) if args.user else args.host
    argv = ["ssh"]
    if tty:
        argv.append("-tt")
    argv.extend(["-p", str(args.port)])
    if args.identity:
        argv.extend(["-i", args.identity])
    for option in args.ssh_option or []:
        argv.extend(["-o", option])
    argv.append(target)
    return argv


def run_manual_install_session(args, script):
    remote_name = args.remote_script
    upload_command = (
        "remote_script=\"$HOME/%s\"; "
        "umask 077; "
        "cat > \"$remote_script\"; "
        "chmod 700 \"$remote_script\""
    ) % remote_name
    run_command = (
        "remote_script=\"$HOME/%s\"; "
        "%s \"$remote_script\"; "
        "status=$?; "
        "rm -f \"$remote_script\"; "
        "exit $status"
    ) % (remote_name, args.root_command)

    script_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            suffix=".sh",
        ) as handle:
            script_path = handle.name
            handle.write(script)
        with open(script_path, "r", encoding="utf-8") as handle:
            upload_argv = base_ssh_argv(args)
            upload_argv.append(upload_command)
            upload_rc = subprocess.call(upload_argv, stdin=handle)
        if upload_rc != 0:
            return upload_rc

        run_argv = base_ssh_argv(args, tty=True)
        run_argv.append(run_command)
        return subprocess.call(run_argv)
    finally:
        if script_path:
            try:
                os.unlink(script_path)
            except OSError:
                pass


def remote_firmware_snapshot(ssh):
    files = " ".join(q(path) for path in FIRMWARE_FILES)
    command = (
        "for p in %s; do "
        "if [ -r \"$p\" ]; then "
        "printf '== %%s ==\\n' \"$p\"; cat \"$p\"; printf '\\n'; "
        "fi; "
        "done"
    ) % files
    return ssh.run(command).stdout


def first_supported_value(text, supported_values):
    for value in supported_values:
        if value and value in text:
            return value
    return None


def verify(args):
    manifest = load_manifest()
    target = manifest["target"]
    smtp = manifest["smtp"]
    expected_sha = smtp["sha256"]
    ssh = SSHClient(
        args.host,
        user=args.user,
        port=args.port,
        identity=args.identity,
        ssh_options=args.ssh_option,
        sudo=args.sudo,
    )

    ok = True

    connected = ssh.run("printf smtpfix-ok").stdout == "smtpfix-ok"
    print_check("SSH", connected, ssh.target)
    ok = ok and connected

    firmware_text = remote_firmware_snapshot(ssh)
    firmware = first_supported_value(firmware_text, target["firmware"])
    print_check(
        "Firmware",
        firmware is not None,
        firmware or "supported version not found in known firmware files",
    )
    ok = ok and firmware is not None

    kernel = ssh.run("uname -r").stdout.strip()
    kernel_ok = target["kernel"] in kernel
    print_check("Kernel", kernel_ok, "%s (expected %s)" % (kernel, target["kernel"]))
    ok = ok and kernel_ok

    python_version = ssh.run("python -V 2>&1").stdout.strip()
    python_ok = target["python"] in python_version
    print_check("Python", python_ok, python_version)
    ok = ok and python_ok

    openssl_version = ssh.run("openssl version").stdout.strip()
    openssl_ok = target["openssl"] in openssl_version
    print_check("OpenSSL", openssl_ok, openssl_version)
    ok = ok and openssl_ok

    smtp_path = args.smtp or smtp["path"]
    exists = remote_file_exists(ssh, smtp_path)
    print_check("smtp.py", exists, smtp_path)
    ok = ok and exists

    if exists:
        actual_sha = remote_sha256(ssh, smtp_path)
        sha_ok = actual_sha == expected_sha
        print_check("SHA256", sha_ok, actual_sha)
        ok = ok and sha_ok

    if ok:
        print("VERIFY OK")
        return 0
    print("VERIFY FAILED")
    return 1


def test(args):
    ssh = SSHClient(
        args.host,
        user=args.user,
        port=args.port,
        identity=args.identity,
        ssh_options=args.ssh_option,
        sudo=args.sudo,
    )
    return run_remote_python(
        ssh,
        SMTP_TEST_SCRIPT,
        (args.config, str(args.timeout)),
    )


def manual_install(args):
    validate_sender(args.sender)
    manifest = load_manifest()
    smtp_path = args.smtp or manifest["smtp"]["path"]
    backup_path = args.backup or (smtp_path + BACKUP_SUFFIX)
    expected_sha = manifest["smtp"]["sha256"]
    script = build_manual_install_script(
        smtp_path,
        backup_path,
        args.sender,
        expected_sha,
        args.mount_target,
    )

    if args.print_script:
        print(script)
        return 0

    print("Starting one-session manual install over SSH.")
    print("The root script will be uploaded temporarily, run with sudo, and removed.")
    print("You may be asked for the SSH password and then the sudo password.")
    print("Remote root command: %s" % args.root_command)
    return run_manual_install_session(args, script)


def install(args):
    validate_sender(args.sender)
    manifest = load_manifest()
    smtp_path = args.smtp or manifest["smtp"]["path"]
    backup_path = args.backup or (smtp_path + BACKUP_SUFFIX)
    expected_sha = manifest["smtp"]["sha256"]
    ssh = SSHClient(
        args.host,
        user=args.user,
        port=args.port,
        identity=args.identity,
        ssh_options=args.ssh_option,
        sudo=args.sudo,
    )

    print("Installing SMTP envelope sender patch")
    print("Envelope sender: %s" % args.sender)

    preflight_sha = remote_sha256(ssh, smtp_path)
    if preflight_sha != expected_sha:
        print_check("Preflight SHA256", False, preflight_sha)
        print("INSTALL FAILED")
        return 1
    print_check("Preflight SHA256", True, preflight_sha)

    remounted_rw = False
    backup_ready = False
    install_ok = False
    try:
        remount(ssh, args.mount_target, "rw")
        remounted_rw = True

        ensure_backup(ssh, smtp_path, backup_path, expected_sha)
        backup_ready = True

        current_sha = remote_sha256(ssh, smtp_path)
        if current_sha != expected_sha:
            raise SmtpFixError("smtp.py changed before patch: %s" % current_sha)
        print_check("Install SHA256", True, current_sha)

        patch_rc = run_remote_python(
            ssh,
            PATCH_SCRIPT,
            (smtp_path, args.sender, expected_sha),
        )
        if patch_rc != 0:
            raise SmtpFixError("patch command failed")

        install_ok = True
    except Exception as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        if remounted_rw and backup_ready:
            print("Rollback: restoring backup")
            rollback_rc = restore_from_backup(
                ssh,
                smtp_path,
                backup_path,
                expected_sha,
                "Rollback",
            )
            if rollback_rc != 0:
                print("ROLLBACK FAILED", file=sys.stderr)
        install_ok = False
    finally:
        if remounted_rw:
            try:
                remount(ssh, args.mount_target, "ro")
            except Exception as exc:
                print("ERROR: remount ro failed: %s" % exc, file=sys.stderr)
                install_ok = False

    if install_ok:
        print("INSTALL OK")
        return 0
    print("INSTALL FAILED")
    return 1


def restore(args):
    manifest = load_manifest()
    smtp_path = args.smtp or manifest["smtp"]["path"]
    backup_path = args.backup or (smtp_path + BACKUP_SUFFIX)
    expected_sha = manifest["smtp"]["sha256"]
    ssh = SSHClient(
        args.host,
        user=args.user,
        port=args.port,
        identity=args.identity,
        ssh_options=args.ssh_option,
        sudo=args.sudo,
    )

    print("Restoring original smtp.py")
    remounted_rw = False
    restore_ok = False
    try:
        remount(ssh, args.mount_target, "rw")
        remounted_rw = True

        restore_rc = restore_from_backup(
            ssh,
            smtp_path,
            backup_path,
            expected_sha,
            "Restore",
        )
        if restore_rc != 0:
            raise SmtpFixError("restore command failed")

        final_sha = remote_sha256(ssh, smtp_path)
        if final_sha != expected_sha:
            raise SmtpFixError("restored smtp.py SHA256 mismatch: %s" % final_sha)
        print_check("Restored SHA256", True, final_sha)
        restore_ok = True
    except Exception as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        restore_ok = False
    finally:
        if remounted_rw:
            try:
                remount(ssh, args.mount_target, "ro")
            except Exception as exc:
                print("ERROR: remount ro failed: %s" % exc, file=sys.stderr)
                restore_ok = False

    if restore_ok:
        print("RESTORE OK")
        return 0
    print("RESTORE FAILED")
    return 1


def add_connection_arguments(parser, include_sudo=True):
    parser.add_argument("--host", required=True, help="NAS hostname or IP address")
    parser.add_argument("--user", default="root", help="SSH user, default: root")
    parser.add_argument("--port", type=int, default=22, help="SSH port, default: 22")
    parser.add_argument("--identity", help="SSH private key file")
    parser.add_argument(
        "--ssh-option",
        action="append",
        help="Extra ssh -o option, can be used more than once",
    )
    if include_sudo:
        parser.add_argument(
            "--sudo",
            action="store_true",
            help="Run remote commands with passwordless sudo -n",
        )


def build_parser():
    parser = argparse.ArgumentParser(prog="smtpfix")
    subcommands = parser.add_subparsers(dest="command")

    verify_parser = subcommands.add_parser("verify", help="Verify NAS firmware and smtp.py")
    add_connection_arguments(verify_parser)
    verify_parser.add_argument("--smtp", default=DEFAULT_SMTP, help="Remote smtp.py path")

    test_parser = subcommands.add_parser("test", help="Test SMTP settings from the NAS")
    add_connection_arguments(test_parser)
    test_parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Remote user_config.json path",
    )
    test_parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="SMTP socket timeout in seconds, default: 20",
    )

    manual_install_parser = subcommands.add_parser(
        "manual-install",
        help="Install patch through one interactive SSH sudo session",
    )
    add_connection_arguments(manual_install_parser, include_sudo=False)
    manual_install_parser.add_argument("--smtp", default=DEFAULT_SMTP, help="Remote smtp.py path")
    manual_install_parser.add_argument("--backup", help="Remote backup path")
    manual_install_parser.add_argument(
        "--sender",
        default=DEFAULT_SENDER,
        help="Envelope sender address, default: %s" % DEFAULT_SENDER,
    )
    manual_install_parser.add_argument(
        "--mount-target",
        default=DEFAULT_MOUNT_TARGET,
        help="Filesystem to remount rw/ro, default: /",
    )
    manual_install_parser.add_argument(
        "--root-command",
        default="sudo sh",
        help="Remote command that runs the uploaded script as root, default: sudo sh",
    )
    manual_install_parser.add_argument(
        "--remote-script",
        default=".smtpfix-manual-install.sh",
        help="Temporary script name under the SSH user's home directory",
    )
    manual_install_parser.add_argument(
        "--print-script",
        action="store_true",
        help="Print the root install script instead of running it",
    )

    install_parser = subcommands.add_parser("install", help="Install SMTP envelope patch")
    add_connection_arguments(install_parser)
    install_parser.add_argument("--smtp", default=DEFAULT_SMTP, help="Remote smtp.py path")
    install_parser.add_argument("--backup", help="Remote backup path")
    install_parser.add_argument(
        "--sender",
        default=DEFAULT_SENDER,
        help="Envelope sender address, default: %s" % DEFAULT_SENDER,
    )
    install_parser.add_argument(
        "--mount-target",
        default=DEFAULT_MOUNT_TARGET,
        help="Filesystem to remount rw/ro, default: /",
    )

    restore_parser = subcommands.add_parser("restore", help="Restore original smtp.py")
    add_connection_arguments(restore_parser)
    restore_parser.add_argument("--smtp", default=DEFAULT_SMTP, help="Remote smtp.py path")
    restore_parser.add_argument("--backup", help="Remote backup path")
    restore_parser.add_argument(
        "--mount-target",
        default=DEFAULT_MOUNT_TARGET,
        help="Filesystem to remount rw/ro, default: /",
    )

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "verify":
        return verify(args)
    if args.command == "test":
        return test(args)
    if args.command == "manual-install":
        return manual_install(args)
    if args.command == "install":
        return install(args)
    if args.command == "restore":
        return restore(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CommandError as exc:
        if exc.stdout.strip():
            print(exc.stdout.strip(), file=sys.stderr)
        if exc.stderr.strip():
            print(exc.stderr.strip(), file=sys.stderr)
        print("ERROR: %s (exit %s)" % (exc.command, exc.returncode), file=sys.stderr)
        raise SystemExit(1)
    except SmtpFixError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
