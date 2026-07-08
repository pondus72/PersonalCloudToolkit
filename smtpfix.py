#!/usr/bin/env python3
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

MANIFEST = Path(__file__).with_name("manifest.json")
DEFAULT_SMTP = "/usr/lib/python2.7/site-packages/sendmail/mailer/smtp.py"
DEFAULT_CONFIG = "/etc/sendmail/user_config.json"

FIRMWARE_FILES = (
    "/etc/version",
    "/etc/nas_version",
    "/etc/NASVERSION",
    "/etc/firmware_version",
    "/etc/os-release",
)

SMTP_TEST_SCRIPT = r'''
from __future__ import print_function
import json
import smtplib
import socket
import sys

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
    ("auth_pass", "auth_password", "smtp_password", "password", "passwd"),
)
use_ssl = as_bool(first(config, ("smtp_ssl", "ssl", "use_ssl", "ssl_enable", "ssl_enabled")))
use_tls = as_bool(
    first(config, ("smtp_tls", "tls", "starttls", "use_tls", "tls_enable", "tls_enabled"))
)

if port is None:
    port = 465 if use_ssl else 587 if use_tls else 25

try:
    port = int(port)
except Exception:
    fail("Configuration", "invalid SMTP port: %s" % port)

missing = []
if not host:
    missing.append("SMTP host")
if not auth_user:
    missing.append("auth_user")
if not auth_pass:
    missing.append("auth password")
if missing:
    fail("Configuration", "missing %s in %s" % (", ".join(missing), CONFIG_PATH))

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
    def __init__(self, host, user=None, port=22, identity=None, ssh_options=None):
        self.target = "%s@%s" % (user, host) if user else host
        self.base_command = ["ssh", "-p", str(port)]
        if identity:
            self.base_command.extend(["-i", identity])
        for option in ssh_options or []:
            self.base_command.extend(["-o", option])
        self.base_command.append(self.target)

    def run(self, command, input_text=None, check=True):
        argv = list(self.base_command)
        argv.append(command)
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


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def q(value):
    return shlex.quote(value)


def print_check(label, ok, detail):
    status = "OK" if ok else "FAIL"
    print("[%s] %s: %s" % (status, label, detail))


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
    kernel_ok = kernel == target["kernel"]
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
    )
    return run_remote_python(
        ssh,
        SMTP_TEST_SCRIPT,
        (args.config, str(args.timeout)),
    )


def add_connection_arguments(parser):
    parser.add_argument("--host", required=True, help="NAS hostname or IP address")
    parser.add_argument("--user", default="root", help="SSH user, default: root")
    parser.add_argument("--port", type=int, default=22, help="SSH port, default: 22")
    parser.add_argument("--identity", help="SSH private key file")
    parser.add_argument(
        "--ssh-option",
        action="append",
        help="Extra ssh -o option, can be used more than once",
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

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "verify":
        return verify(args)
    if args.command == "test":
        return test(args)
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
