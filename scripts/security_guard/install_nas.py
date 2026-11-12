"""Install only this standalone monitor's units; no ERP image or AV changes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

BASE = Path('/volume1/docker/panse/security-guard')
LOCK = Path('/volume1/docker/panse/.panse-release-lock')


def command(*args):
    p = subprocess.run(list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=100)
    if p.returncode:
        raise RuntimeError('command_failed: ' + str(args[:3]) + ' ' + p.stdout[-1000:])
    return p.stdout


def main():
    owner = 'nas-light-security-%d|installer|standalone' % os.getpid()
    LOCK.mkdir()  # Existing owner is never removed or overridden.
    (LOCK/'owner').write_text(owner)
    try:
        units = {}
        for mode, schedule in [('hourly', '*-*-* *:05:00'), ('daily', '*-*-* 08:20:00')]:
            name = 'panse-security-' + mode
            units[name+'.service'] = '''[Unit]
Description=Panse bounded NAS security check (%s)
After=network.target
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /volume1/docker/panse/security-guard/nas_guard.py %s
WorkingDirectory=/volume1/docker/panse/security-guard
Nice=19
CPUAccounting=yes
CPUQuota=10%%
MemoryAccounting=yes
MemoryLimit=64M
IOSchedulingClass=idle
TimeoutStartSec=90
TimeoutStopSec=5
KillMode=control-group
UMask=0077
''' % (mode, mode)
            units[name+'.timer'] = '''[Unit]
Description=Panse bounded NAS security schedule (%s)
[Timer]
OnCalendar=%s
Persistent=true
AccuracySec=60
Unit=%s.service
[Install]
WantedBy=timers.target
''' % (mode, schedule, name)
        backups = BASE/'unit-backup'
        backups.mkdir(exist_ok=True)
        for name, content in units.items():
            p = Path('/etc/systemd/system')/name
            if p.exists() and p.read_text() != content:
                (backups/(name+'.%d' % time.time())).write_bytes(p.read_bytes())
            p.write_text(content)
            p.chmod(0o644)
        command('systemctl', 'daemon-reload')
        command('systemctl', 'enable', 'panse-security-hourly.timer', 'panse-security-daily.timer')
        # Enabled-unit symlinks belong in the reviewed baseline, before timers start.
        if not (BASE/'baseline.json').exists():
            command('/usr/bin/python3', str(BASE/'nas_guard.py'), 'baseline')
        test = command('/usr/bin/python3', '-m', 'unittest', '-v', 'test_guard')
        # Actual cgroup-constrained execution; no fake malware and no synthetic alert.
        command('systemctl', 'start', 'panse-security-hourly.service')
        command('systemctl', 'start', 'panse-security-daily.service')
        samples = {mode: json.loads((BASE/(mode+'.json')).read_text()) for mode in ('hourly','daily')}
        if any(s['cpu_seconds'] > 2 or s['wall_seconds'] > 30 or s['max_rss_kb'] > 60000 for s in samples.values()):
            command('systemctl', 'disable', 'panse-security-hourly.timer', 'panse-security-daily.timer')
            raise RuntimeError('performance_acceptance_failed_timers_not_started')
        command('systemctl', 'start', 'panse-security-hourly.timer', 'panse-security-daily.timer')
        report = {'installed_at': time.time(), 'tests': test, 'samples': samples,
                  'files': {n: hashlib.sha256((BASE/n).read_bytes()).hexdigest() for n in ['common.py','nas_guard.py','test_guard.py','install_nas.py']},
                  'units': command('systemctl','list-timers','--all','panse-security-*')}
        (BASE/'installation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))
    finally:
        if (LOCK/'owner').read_text() == owner:
            (LOCK/'owner').unlink()
            LOCK.rmdir()


if __name__ == '__main__':
    main()
