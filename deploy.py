#!/usr/bin/env python3
"""
Interactive deployer for Hideki Okamoto's DataStream 2 + Elasticsearch + Kibana
StackScript (1059555) on Akamai Cloud.

    python3 deploy.py                 ask, terraform apply, post-install, print DS2 settings
    python3 deploy.py --defaults      same, accepting every default without asking
    python3 deploy.py post-install    re-run the post-install step on an existing deploy
    python3 deploy.py summary         print the DataStream 2 / Kibana settings again
    python3 deploy.py destroy         terraform destroy

Every question shows a default in [brackets]; press Enter to accept it, or pass
--defaults to accept them all and skip the confirmations. Answers
are saved to terraform/terraform.tfvars.json and terraform/deploy.local.json
(both gitignored, chmod 600) and become the defaults on the next run.

Only the Python standard library is used.
"""
import argparse
import configparser
import getpass
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TF_DIR = ROOT / "terraform"
REMOTE_DIR = ROOT / "remote"
DASHBOARD = ROOT / "kibana" / "akamai-debug.ndjson"
TFVARS = TF_DIR / "terraform.tfvars.json"
LOCAL = TF_DIR / "deploy.local.json"
KNOWN_HOSTS = TF_DIR / "known_hosts"

# Hideki's StackScript pastes passwords into JSON and shell strings without
# escaping, so keep them to characters that survive both.
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_-]{12,128}$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
STACKSCRIPT_TIMEOUT = 45 * 60

# Set by --defaults: every question takes its default without prompting.
ACCEPT_DEFAULTS = False


########################################
# Terminal helpers
########################################

def bold(text):
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def section(title):
    print(f"\n{bold('== ' + title + ' ==')}")


def die(msg):
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def ask(label, default=None, validate=None, help_text=None):
    """Prompt with a [default]. Empty input accepts the default."""
    if help_text:
        print(f"  {help_text}")
    shown = f" [{default}]" if default not in (None, "") else ""
    if ACCEPT_DEFAULTS:
        if default is None:
            die(f"'{label}' has no default. Run without --defaults to answer it.")
        error = validate(str(default)) if validate else None
        if error:
            die(f"Default for '{label}' is invalid: {error}")
        print(f"{label}: {default}")
        return str(default)
    while True:
        value = input(f"{label}{shown}: ").strip()
        if not value:
            if default is None:
                print("  A value is required.")
                continue
            value = str(default)
        error = validate(value) if validate else None
        if error:
            print(f"  {error}")
            continue
        return value


def ask_optional(label, default, shown_default=None):
    """Free-text prompt where an empty answer is allowed."""
    if ACCEPT_DEFAULTS:
        print(f"{label}: {default or shown_default or '(empty)'}")
        return default
    hint = default or shown_default or ""
    return input(f"{label} [{hint}]: ").strip() or default


def ask_yes(label, default):
    hint = "Y/n" if default else "y/N"
    if ACCEPT_DEFAULTS:
        print(f"{label} {'yes' if default else 'no'}")
        return default
    while True:
        value = input(f"{label} [{hint}]: ").strip().lower()
        if not value:
            return default
        if value in ("y", "yes", "s", "si", "sí"):
            return True
        if value in ("n", "no"):
            return False


def generate_password(length=24):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def check_password(value):
    if not PASSWORD_RE.match(value):
        return "Use 12+ characters from A-Z a-z 0-9 _ - (the StackScript does not escape others)."
    return None


def check_int(minimum):
    def _check(value):
        if not value.isdigit() or int(value) < minimum:
            return f"Enter a whole number >= {minimum}."
        return None
    return _check


def check_cidrs(value):
    for cidr in split_list(value):
        if "/" not in cidr:
            return f"{cidr} is not a CIDR, e.g. 203.0.113.10/32"
        if cidr in ("0.0.0.0/0", "::/0"):
            print("  Warning: this opens SSH and Kibana to the whole Internet.")
    return None


def split_list(value):
    return [item.strip() for item in value.split(",") if item.strip()]


########################################
# Local state
########################################

def load_json(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)


########################################
# Environment discovery
########################################

def require_tools():
    missing = [tool for tool in ("terraform", "ssh", "scp", "ssh-keygen") if not shutil.which(tool)]
    if missing:
        die(f"Missing tools on PATH: {', '.join(missing)}")


def linode_cli_token():
    path = Path.home() / ".config" / "linode-cli"
    if not path.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(path)
    user = parser.defaults().get("default-user")
    if user and parser.has_section(user):
        return parser.get(user, "token", fallback=None)
    return None


def token_is_valid(token):
    request = urllib.request.Request(
        "https://api.linode.com/v4/profile", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15):
            return True
    except urllib.error.HTTPError as err:
        return err.code not in (401, 403)
    except urllib.error.URLError:
        print("  Could not reach api.linode.com to check the token, continuing.")
        return True


def get_token():
    token = os.environ.get("LINODE_TOKEN")
    source = "LINODE_TOKEN"
    if not token:
        token = linode_cli_token()
        source = "linode-cli config"
    while True:
        if token:
            if token_is_valid(token):
                print(f"Linode token: from {source}")
                return token
            print(f"  The token from {source} was rejected by the Linode API.")
        if ACCEPT_DEFAULTS:
            die("No valid Linode token. Export LINODE_TOKEN or configure linode-cli.")
        print("  Create one at https://cloud.linode.com/profile/tokens "
              "(Read/Write: Linodes, Firewalls, Volumes; Read: StackScripts).")
        token = getpass.getpass("Linode Personal Access Token (not saved to disk): ").strip()
        source = "prompt"


def detect_public_ip():
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://checkip.amazonaws.com"):
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ip = resp.read().decode().strip()
                socket.inet_aton(ip)
                return ip
        except (OSError, ValueError):
            continue
    return None


def ensure_ssh_key(path_str):
    private = Path(path_str).expanduser()
    public = private.with_name(private.name + ".pub")
    if not private.exists():
        if not ask_yes(f"{private} does not exist. Generate a new ed25519 key there?", True):
            die("An SSH key is needed for the post-install step.")
        private.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"ds2-elk-tf@{socket.gethostname()}", "-f", str(private)],
            check=True,
        )
    if not public.exists():
        die(f"Public key {public} not found next to the private key.")
    return str(private), public.read_text().strip()


########################################
# Questions
########################################

def collect_answers():
    tfvars = load_json(TFVARS)
    local = load_json(LOCAL)

    section("Instance")
    tfvars["label"] = ask("Label (prefix for every resource)", tfvars.get("label", "ds2-elk"))
    tfvars["region"] = ask("Region", tfvars.get("region", "us-east"),
                           help_text="List: linode-cli regions list, or https://www.linode.com/global-infrastructure/")
    tfvars["instance_type"] = ask("Instance type", tfvars.get("instance_type", "g6-dedicated-4"),
                                  help_text="8 GB minimum. See the Sizing table in README.md.")
    tfvars["data_volume_size_gb"] = int(ask(
        "Data volume size in GB (0 = keep data on the instance disk)",
        tfvars.get("data_volume_size_gb", 100), validate=check_int(0)))
    tfvars["backups_enabled"] = ask_yes("Enable Linode Backups (extra cost)?", tfvars.get("backups_enabled", False))
    tfvars["tags"] = split_list(ask("Tags (comma separated)",
                                    ",".join(tfvars.get("tags", ["ds2", "elasticsearch", "kibana"]))))

    section("Access")
    detected = detect_public_ip()
    default_cidrs = ",".join(tfvars["allowed_admin_cidrs"]) if tfvars.get("allowed_admin_cidrs") else (
        f"{detected}/32" if detected else None)
    tfvars["allowed_admin_cidrs"] = split_list(ask(
        "CIDRs allowed to SSH and Kibana (comma separated)", default_cidrs, validate=check_cidrs,
        help_text=f"Your public IP looks like {detected}." if detected else None))

    local["ssh_key"], pubkey = ensure_ssh_key(ask("SSH private key", local.get("ssh_key", "~/.ssh/ds2-elk-tf")))
    tfvars["authorized_keys"] = [pubkey]
    tfvars["ssh_user"] = ask("SSH sudo user", tfvars.get("ssh_user", "elkadmin"),
                             validate=lambda v: None if USER_RE.match(v) and v != "root" else "Lowercase Linux user name, not root.")
    tfvars["ssh_user_password"] = ask("Password for the SSH user (sudo)",
                                      tfvars.get("ssh_user_password", generate_password()), validate=check_password)
    tfvars["root_password"] = ask("Root password", tfvars.get("root_password", generate_password()),
                                  validate=check_password)

    section("Elasticsearch, Kibana and DataStream 2")
    tfvars["es_admin_password"] = ask("Password for 'elastic' (Kibana login)",
                                      tfvars.get("es_admin_password", generate_password()), validate=check_password)
    tfvars["ds2_ingest_user"] = ask("DataStream 2 ingest user", tfvars.get("ds2_ingest_user", "ds2_ingest"),
                                    validate=lambda v: None if re.match(r"^[A-Za-z0-9_-]+$", v) else "Letters, digits, _ and - only.")
    tfvars["ds2_ingest_password"] = ask("DataStream 2 ingest password",
                                        tfvars.get("ds2_ingest_password", generate_password()), validate=check_password)
    local["retention_days"] = int(ask(
        "Delete logs older than N days (0 = keep forever)", local.get("retention_days", 7), validate=check_int(0),
        help_text="Hideki's ILM policy never deletes, so the disk eventually fills up. 7 days suits a debug stack."))
    local["import_dashboard"] = ask_yes("Import the extra 'Akamai Debug' dashboard?", local.get("import_dashboard", True))

    section("HTTPS for the DataStream 2 endpoint")
    print("  Without TLS, DataStream 2 posts logs and its basic-auth credentials over plain HTTP\n"
          "  (port 9200, open only to Akamai's IP ranges). With TLS, nginx + Let's Encrypt serve\n"
          "  https://<hostname>/_bulk on 443 and port 80 opens to everyone for certificate renewals.")
    tfvars["enable_tls"] = ask_yes("Enable HTTPS with Let's Encrypt?", tfvars.get("enable_tls", False))
    if tfvars["enable_tls"]:
        print("  Leave the hostname empty to use the instance's reverse DNS name\n"
              "  (<ip-dashed>.ip.linodeusercontent.com, no DNS work needed). A custom hostname\n"
              "  needs an A record pointing to the instance; the deploy waits for it.")
        tfvars["tls_hostname"] = ask_optional("Certificate hostname", tfvars.get("tls_hostname", ""), "reverse DNS")
        local["tls_email"] = ask_optional("Let's Encrypt email (optional)", local.get("tls_email", ""))
    else:
        tfvars["tls_hostname"] = ""

    return tfvars, local


def print_plan(tfvars, local):
    section("Review")
    rows = [
        ("Label / region / type", f"{tfvars['label']} / {tfvars['region']} / {tfvars['instance_type']}"),
        ("Data volume", f"{tfvars['data_volume_size_gb']} GB" if tfvars["data_volume_size_gb"] else "none"),
        ("Backups", "yes" if tfvars["backups_enabled"] else "no"),
        ("Admin CIDRs", ", ".join(tfvars["allowed_admin_cidrs"])),
        ("SSH", f"{tfvars['ssh_user']} with {local['ssh_key']}"),
        ("DS2 ingest user", tfvars["ds2_ingest_user"]),
        ("Retention", f"{local['retention_days']} days" if local["retention_days"] else "keep forever"),
        ("Akamai Debug dashboard", "import" if local["import_dashboard"] else "skip"),
        ("HTTPS", (tfvars["tls_hostname"] or "reverse DNS name") if tfvars["enable_tls"] else "off"),
    ]
    for key, value in rows:
        print(f"  {key:<24} {value}")
    print(f"\n  Passwords are stored in {TFVARS.relative_to(ROOT)} (chmod 600).")


########################################
# Terraform
########################################

def terraform(args, token, capture=False):
    env = dict(os.environ, LINODE_TOKEN=token, TF_IN_AUTOMATION="1")
    cmd = ["terraform", *args]
    if capture:
        return subprocess.run(cmd, cwd=TF_DIR, env=env, check=True, capture_output=True, text=True).stdout
    result = subprocess.run(cmd, cwd=TF_DIR, env=env)
    if result.returncode != 0:
        die(f"terraform {args[0]} failed.")


def terraform_outputs(token=""):
    raw = terraform(["output", "-json"], token, capture=True)
    outputs = {key: value["value"] for key, value in json.loads(raw).items()}
    if "public_ipv4" not in outputs:
        die("No Terraform outputs found. Run 'python3 deploy.py' first.")
    return outputs


########################################
# SSH / post-install
########################################

def ssh_opts(key):
    return ["-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new", "-o", f"UserKnownHostsFile={KNOWN_HOSTS}"]


def ssh(outputs, key, command, stdin=None, capture=True):
    target = f"{outputs['ssh_user']}@{outputs['public_ipv4']}"
    return subprocess.run(["ssh", *ssh_opts(key), target, command], input=stdin, text=True,
                          capture_output=capture)


def wait_for_stackscript(outputs, key, sudo_password):
    ip = outputs["public_ipv4"]
    print(f"\nHideki's StackScript installs Elasticsearch and Kibana; this takes about 10 minutes.\n"
          f"Follow along in another terminal with:\n"
          f"  ssh -i {key} {outputs['ssh_user']}@{ip} 'tail -f /var/log/stackscript.log'\n")
    deadline = time.time() + STACKSCRIPT_TIMEOUT
    sudo_checked = False
    while time.time() < deadline:
        if ssh(outputs, key, "true").returncode != 0:
            status = "waiting for SSH (the StackScript creates the user first)"
        else:
            if not sudo_checked:
                if ssh(outputs, key, "sudo -S -p '' true", stdin=sudo_password + "\n").returncode != 0:
                    die("sudo rejected the SSH user's password.")
                sudo_checked = True
            # /root/README is the last thing StackScript 1059555 writes.
            if ssh(outputs, key, "sudo -S -p '' test -f /root/README", stdin=sudo_password + "\n").returncode == 0:
                print("StackScript finished.")
                return
            last = ssh(outputs, key, "tail -n 1 /var/log/stackscript.log 2>/dev/null").stdout.strip()
            status = f"installing: {last[:90]}" if last else "installing"
        print(f"  [{time.strftime('%H:%M:%S')}] {status}")
        time.sleep(20)
    die("Timed out waiting for the StackScript. Check /var/log/stackscript.log on the instance, "
        "then run 'python3 deploy.py post-install'.")


def wait_for_dns(hostname, ip):
    print(f"\nCreate a DNS A record: {hostname} -> {ip}")
    while True:
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(hostname, None, socket.AF_INET)}
        except socket.gaierror:
            addresses = set()
        if ip in addresses:
            print(f"{hostname} resolves to {ip}.")
            return
        print(f"  [{time.strftime('%H:%M:%S')}] {hostname} -> {', '.join(sorted(addresses)) or 'no answer'}, "
              "waiting (Ctrl+C to stop, then 'python3 deploy.py post-install')")
        time.sleep(30)


def post_install(outputs):
    tfvars, local = load_json(TFVARS), load_json(LOCAL)
    if not tfvars:
        die(f"{TFVARS.relative_to(ROOT)} not found. Run 'python3 deploy.py' first.")
    key = str(Path(local.get("ssh_key", "~/.ssh/ds2-elk-tf")).expanduser())
    sudo_password = tfvars["ssh_user_password"]

    wait_for_stackscript(outputs, key, sudo_password)

    tls_hostname = outputs["tls_hostname"] if outputs.get("enable_tls") else ""
    if tls_hostname and tfvars.get("tls_hostname"):
        wait_for_dns(tls_hostname, outputs["public_ipv4"])

    section("Post-install")
    env = {
        "ES_PASSWORD": tfvars["es_admin_password"],
        "VOLUME_DEVICE": outputs.get("volume_filesystem_path") or "",
        "RETENTION_DAYS": str(local.get("retention_days", 0)),
        "IMPORT_DASHBOARD": "yes" if local.get("import_dashboard", True) else "no",
        "TLS_HOSTNAME": tls_hostname,
        "TLS_EMAIL": local.get("tls_email", ""),
    }
    env_text = "".join(f"{name}={shlex.quote(value)}\n" for name, value in env.items())

    if ssh(outputs, key, "mkdir -p ds2-elk && chmod 700 ds2-elk").returncode != 0:
        die("Could not create ~/ds2-elk on the instance.")
    target = f"{outputs['ssh_user']}@{outputs['public_ipv4']}:ds2-elk/"
    subprocess.run(["scp", "-q", *ssh_opts(key), str(REMOTE_DIR / "post-install.sh"), str(DASHBOARD), target],
                   check=True)
    if ssh(outputs, key, "umask 077 && cat > ds2-elk/post-install.env", stdin=env_text).returncode != 0:
        die("Could not upload the post-install settings.")

    result = ssh(outputs, key, "sudo -S -p '' bash \"$HOME/ds2-elk/post-install.sh\"",
                 stdin=sudo_password + "\n", capture=False)
    if result.returncode != 0:
        die("Post-install failed (see output above). Fix the cause and run 'python3 deploy.py post-install'.")


########################################
# Summary
########################################

def summary(outputs):
    tfvars, local = load_json(TFVARS), load_json(LOCAL)
    ip = outputs["public_ipv4"]
    section("Kibana")
    print(f"  URL       {outputs['kibana_url']}")
    print(f"  User      elastic")
    print(f"  Password  {tfvars['es_admin_password']}")
    print(f"  Dashboards: Akamai, Akamai Common Media Client Data"
          f"{', Akamai Debug' if local.get('import_dashboard', True) else ''}")

    section("SSH")
    print(f"  ssh -i {local.get('ssh_key', '~/.ssh/ds2-elk-tf')} {outputs['ssh_user']}@{ip}")

    section("DataStream 2 destination (Control Center > DataStream > Create stream)")
    print(f"  Log type / Format     CDN / JSON")
    print(f"  Destination           Elasticsearch")
    print(f"  Endpoint              {outputs['elasticsearch_bulk_endpoint']}")
    print(f"  Index name            datastream2")
    print(f"  User name             {tfvars['ds2_ingest_user']}")
    print(f"  Password              {tfvars['ds2_ingest_password']}")
    print(f"  Send compressed data  ON")
    print("  Validate & Save fails because the validator probes from a Control Center IP outside\n"
          "  the ACL: click 'Skip validation'. Activation takes about 90 minutes.")

    section("Property Manager")
    print("  Add the DataStream behavior (v2, your stream, sampling 100, 'Log Akamai Edge Server IP' ON)\n"
          "  and the 'Log Request Details' behavior, then activate on Staging and Production.")


########################################
# Commands
########################################

def cmd_deploy(_args):
    require_tools()
    print(bold("ds2-elk-tf: Akamai DataStream 2 -> Elasticsearch + Kibana on Akamai Cloud"))
    if not ACCEPT_DEFAULTS:
        print("Press Enter to accept the value in [brackets].")
    token = get_token()

    tfvars, local = collect_answers()
    print_plan(tfvars, local)
    if not ask_yes("\nSave these settings and run Terraform?", True):
        die("Aborted, nothing was changed.")
    save_json(TFVARS, tfvars)
    save_json(LOCAL, local)

    section("Terraform")
    terraform(["init", "-input=false"], token)
    terraform(["plan", "-input=false", "-out=tfplan"], token)
    if not ask_yes("Apply this plan?", True):
        die("Aborted before apply. Settings are saved; run 'python3 deploy.py' again to resume.")
    state = load_json(TF_DIR / "terraform.tfstate")
    had_instance = any(r.get("type") == "linode_instance" for r in state.get("resources", []))
    terraform(["apply", "-input=false", "tfplan"], token)
    (TF_DIR / "tfplan").unlink(missing_ok=True)

    outputs = terraform_outputs(token)
    if not had_instance:
        # Fresh instance: forget any host key cached for a previous one on this IP.
        subprocess.run(["ssh-keygen", "-R", outputs["public_ipv4"], "-f", str(KNOWN_HOSTS)], capture_output=True)
    post_install(outputs)
    summary(outputs)


def cmd_post_install(_args):
    require_tools()
    outputs = terraform_outputs()
    post_install(outputs)
    summary(outputs)


def cmd_summary(_args):
    summary(terraform_outputs())


def cmd_destroy(_args):
    require_tools()
    token = get_token()
    terraform(["destroy"], token)
    KNOWN_HOSTS.unlink(missing_ok=True)
    print(f"\nSettings kept in {TFVARS.relative_to(ROOT)} for the next deploy. Delete it to start fresh.\n"
          "The DataStream 2 stream and Property Manager behaviors are not managed here: remove them in Control Center.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="deploy", choices=["deploy", "post-install", "summary", "destroy"])
    parser.add_argument("-d", "--defaults", action="store_true",
                        help="deploy: accept every default (previous answers, generated passwords, "
                             "detected IP) and skip the confirmations")
    args = parser.parse_args()
    global ACCEPT_DEFAULTS
    ACCEPT_DEFAULTS = args.defaults and args.command == "deploy"
    commands = {"deploy": cmd_deploy, "post-install": cmd_post_install, "summary": cmd_summary, "destroy": cmd_destroy}
    try:
        commands[args.command](args)
    except KeyboardInterrupt:
        die("Interrupted.")


if __name__ == "__main__":
    main()
