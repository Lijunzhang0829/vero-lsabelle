import json
import subprocess
from pathlib import Path

run = Path("/data/zljj/vero-runs/refine-attribution-001")
(run / "results").mkdir(parents=True, exist_ok=False)
name = "vero-isabelle-attribution"
cmd = [
    "docker",
    "run",
    "-d",
    "--name",
    name,
    "--network",
    "none",
    "--security-opt",
    "no-new-privileges",
    "--tmpfs",
    "/tmp:exec,size=4g",
    "--tmpfs",
    "/sel4-project:exec",
    "--tmpfs",
    "/workspace:exec",
    "-e",
    "PYTHONPATH=/vero-src",
    "-e",
    "L4V_ARCH=ARM",
]
mounts = [
    ("/data/zljj/vero-environment/Runtime/l4v", "/sel4-project/verification/l4v", True),
    (
        "/data/zljj/sel4-dev/verification/isabelle",
        "/workspace/verification/isabelle",
        True,
    ),
    (
        "/data/zljj/sel4-dev/verification/isabelle",
        "/sel4-project/verification/isabelle",
        True,
    ),
    ("/data/zljj/vero-environment/Semantic-Base/workspace", "/semantic-base", True),
    ("/data/zljj/vero-environment/Semantic-Base/heaps", "/trusted-heaps", True),
    ("/data/zljj/vero-isabelle/src", "/vero-src", True),
    ("/data/zljj/vero-runs/refine-plan-001/results", "/original", True),
    (str(run / "control"), "/control", True),
    (str(run / "results"), "/results", False),
]
for src, dst, ro in mounts:
    cmd += [
        "--mount",
        "type=bind,src=" + src + ",dst=" + dst + (",readonly" if ro else ""),
    ]
cmd += ["--entrypoint", "/bin/bash", "sel4-public:tuned", "-c", "sleep infinity"]
subprocess.run(cmd, check=True)
init = """set -eu
useradd -u 1002 -M -s /bin/bash checker
mkdir -p /home/checker/.isabelle/etc
cp /root/.isabelle/etc/settings /home/checker/.isabelle/etc/settings
ln -s /root/.isabelle/contrib /home/checker/.isabelle/contrib
ln -s /trusted-heaps /home/checker/.isabelle/heaps
chmod 711 /root /root/.isabelle
chown -hR 1002:1002 /home/checker
chmod 755 /results
"""
subprocess.run(["docker", "exec", name, "bash", "-c", init], check=True)
subprocess.run(
    [
        "docker",
        "exec",
        "-d",
        name,
        "bash",
        "-c",
        "python3 /control/check.py > /results/driver.log 2>&1; echo $? > /results/exit-code",
    ],
    check=True,
)
print(
    json.dumps(
        {
            "container": name,
            "network": "none",
            "credentials_mounted": False,
            "run": str(run),
        }
    )
)
