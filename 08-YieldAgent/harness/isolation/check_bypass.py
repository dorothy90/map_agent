#!/usr/bin/env python3
"""Standalone A/B acceptance check for installed yieldrun confinement."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def envelope(principal, run, command, operation="command"):
    request = {"v": 1, "principal": digest(principal), "run": digest(run),
               "op": operation, "command": command}
    encoded = base64.urlsafe_b64encode(
        json.dumps(request, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return "yieldrun-v1 " + encoded


def ssh_command(args, request):
    return ["ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "ForwardAgent=no", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={args.known_hosts}", "-i", str(args.ssh_key),
            "-p", str(args.ssh_port), "yieldrun@localhost", request]


def invoke(args, principal, run, script):
    remote = "python3 -I -B -c " + shlex.quote(script)
    result = subprocess.run(ssh_command(args, envelope(principal, run, remote)), text=True,
                            capture_output=True, timeout=30, check=False,
                            env={"PATH": os.defpath, "LANG": "C.UTF-8"})
    if result.returncode != 0:
        raise SystemExit("isolated acceptance command failed closed before producing a verdict")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise SystemExit("isolated acceptance command returned invalid output") from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--work-root", type=Path, default=Path("/var/lib/yieldrun/work"))
    args = parser.parse_args()
    if not args.work_root.is_absolute() or not args.ssh_key.is_file() or not args.known_hosts.is_file():
        raise SystemExit("absolute work root and existing SSH key/known_hosts are required")

    identities = (("acceptance-a", "run-a"), ("acceptance-b", "run-b"))
    scopes = {
        principal: {
            "profile": args.work_root / "profiles" / digest(principal),
            "workdir": args.work_root / "runs" / digest(run),
        }
        for principal, run in identities
    }
    create = '''import json,os,pathlib
home=pathlib.Path(os.environ["HOME"]); work=pathlib.Path.cwd()
(home/"profile-canary").write_text("safe"); (work/"work-canary").write_text("safe")
print(json.dumps({"own_profile":(home/"profile-canary").read_text()=="safe","own_work":(work/"work-canary").read_text()=="safe"}))
'''
    for principal, run in identities:
        if not all(invoke(args, principal, run, create).values()):
            raise SystemExit("own-scope acceptance failed")

    verdicts = {}
    for index, (principal, run) in enumerate(identities):
        other = identities[1 - index][0]
        target_profile = scopes[other]["profile"] / "profile-canary"
        target_work = scopes[other]["workdir"] / "work-canary"
        attack = f'''import json,os,pathlib,subprocess
targets=[pathlib.Path({str(target_profile)!r}),pathlib.Path({str(target_work)!r})]
checks={{}}
def denied(name,fn):
 try: fn(); checks[name]=False
 except OSError: checks[name]=True
denied("list",lambda:list(targets[0].parent.iterdir()))
denied("stat",lambda:targets[0].stat())
denied("read",lambda:targets[0].read_text())
denied("overwrite",lambda:targets[0].write_text("changed"))
denied("delete",lambda:targets[0].unlink())
denied("traversal",lambda:(pathlib.Path.cwd()/".."/{digest(identities[1-index][1])!r}/"work-canary").read_text())
link=pathlib.Path.cwd()/"cross-link"
try: link.symlink_to(targets[0]); link.read_text(); checks["symlink"]=False
except OSError: checks["symlink"]=True
finally: link.unlink(missing_ok=True)
p=subprocess.run(["/bin/cat",str(targets[1])],capture_output=True,text=True)
checks["subprocess"]=p.returncode!=0 and "safe" not in p.stdout
print(json.dumps(checks,sort_keys=True))
'''
        checks = invoke(args, principal, run, attack)
        verdicts[f"{principal}_to_{other}"] = checks
        if not checks or not all(checks.values()):
            raise SystemExit("cross-scope acceptance failed: " + json.dumps(verdicts, sort_keys=True))

    verify = '''import json,os,pathlib
home=pathlib.Path(os.environ["HOME"]); work=pathlib.Path.cwd()
ok=(home/"profile-canary").read_text()=="safe" and (work/"work-canary").read_text()=="safe"
(home/"profile-canary").unlink(); (work/"work-canary").unlink()
print(json.dumps({"canaries_intact":ok}))
'''
    for principal, run in identities:
        if invoke(args, principal, run, verify) != {"canaries_intact": True}:
            raise SystemExit("canary integrity check failed")
    print(json.dumps(verdicts, sort_keys=True))


if __name__ == "__main__":
    main()
