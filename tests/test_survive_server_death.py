"""
A render must outlive the server that started it (2026-11-13: server died,
render died with it through its stdout pipe). Uses a fake Blender; never
touches the real queue or state.json.

    python tests\\test_survive_server_death.py
"""

import os
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import runner

results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


tmp = tempfile.mkdtemp(prefix="rendafar_test_")
log = os.path.join(tmp, "job.log")
fake_blender = os.path.join(tmp, "fake_blender.py")
open(fake_blender, "w").write(
    "import time,sys\n"
    "for i in range(1, 13):\n"
    "    print(f'Fra:0 | Remaining: 00:01:00.00 | Mem: 10M | Sample {i}/12', flush=True)\n"
    "    time.sleep(0.5)\n"
    "print('DONE', flush=True)\n")

# A "server" that launches the fake Blender exactly as runner.start does,
# then gets killed hard.
server = os.path.join(tmp, "fake_server.py")
open(server, "w").write(
    "import subprocess,sys,time\n"
    f"blog=open({log!r},'ab')\n"
    f"p=subprocess.Popen([sys.executable,{fake_blender!r}],stdout=blog,stderr=subprocess.STDOUT,"
    "stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NEW_PROCESS_GROUP|0x08000000)\n"
    "blog.close()\n"
    "print(p.pid,flush=True)\n"
    "time.sleep(60)\n")

srv = subprocess.Popen([sys.executable, server], stdout=subprocess.PIPE, text=True)
child = int(srv.stdout.readline())
time.sleep(1.5)
srv.kill()                                   # TerminateProcess: the server is gone
srv.wait()
time.sleep(6)
text = open(log, encoding="utf-8").read()
check("render kept going after the server was killed", "DONE" in text, text[-120:])
check("all 12 samples reached the log", "Sample 12/12" in text)

# The tail: a Runner reading the log written by someone else.
open(log, "w").close()
r = runner.Runner()
proc = subprocess.Popen([sys.executable, fake_blender], stdout=open(log, "ab"),
                        stderr=subprocess.STDOUT)
r._proc, r._log_path, r._log_offset = proc, log, 0
t = threading.Thread(target=r._pump, daemon=True)
t.start()
time.sleep(3)
mid = r._live
proc.wait()
t.join(15)
check("live progress read from the log while rendering",
      mid and 1 <= mid.get("sample", 0) < 12, mid)
check("tail stops after the process ends", not t.is_alive())
check("last line reached the buffer", "DONE" in r.log_tail(5), r.log_tail(3))

# Supervisor: restarts a crashing server, stops on a clean exit.
flag = os.path.join(tmp, "runs")
fake_srv = os.path.join(tmp, "crashy_server.py")
open(fake_srv, "w").write(
    "import os,sys,time\n"
    f"f={flag!r}\n"
    "n=int(open(f).read()) if os.path.exists(f) else 0\n"
    "open(f,'w').write(str(n+1))\n"
    "time.sleep(35)\n"                       # live long enough not to count as 'quick'
    "sys.exit(0 if n>=1 else 3)\n")
env = dict(os.environ, RENDAFAR_SUPERVISED=fake_srv)
sup = subprocess.Popen([sys.executable, os.path.join(HERE, "supervisor.py")], env=env)
try:
    sup.wait(120)
except subprocess.TimeoutExpired:
    sup.kill()
runs = int(open(flag).read()) if os.path.exists(flag) else 0
check("supervisor restarted after a crash, then stopped on clean exit",
      runs == 2 and sup.returncode == 0, f"runs={runs} rc={sup.returncode}")

print(f"\n  {sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
