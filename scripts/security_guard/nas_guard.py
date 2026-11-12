"""Bounded NAS-only monitoring. NO antivirus/updates, uptime probes or remediation.

The collector reads /proc and a small explicit configuration/code allowlist.
It never walks media, Docker data, business databases, or arbitrary user files.
"""
import argparse
import collections
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import stat
import subprocess
import time

from common import Journal, atomic_json, finding, load_json

BASE = Path('/volume1/docker/panse/security-guard')
DOCKER = '/var/packages/ContainerManager/target/usr/bin/docker'
MARKERS = [b'kpx.lck', b'boat-rc', b'owqdljk.top', b'jskdljk.top',
           b'/var/tmp/.cache/.cpuhp', b'80.87.206.91/A-plex']
PMS = '/volume1/PlexMediaServer/AppData/Plex Media Server'
WATCH = ['/etc/cron.d', '/etc/systemd/system', '/usr/local/etc/rc.d', '/var/spool/cron',
         '/etc/crontab', '/etc/rc.local', '/etc/ld.so.preload', '/root/.profile', '/root/.bashrc',
         '/var/tmp/.profile', '/var/tmp/.bashrc', '/var/tmp/.bash_profile',
         '/var/tmp/.config/systemd/user', PMS + '/Codecs', PMS + '/Plug-ins',
         '/etc/nginx/sites-enabled/server.ReverseProxy.conf', '/etc/passwd', '/etc/group']


def bounded_snapshot(roots=WATCH, max_files=500, max_bytes=8*1024*1024, max_depth=5):
    """A limit is an incomplete result, NEVER a clean result. No symlink traversal."""
    result, errors = {}, []
    budget = {'files': 0, 'bytes': 0}
    def visit(p, depth):
        try:
            s = p.lstat()
        except FileNotFoundError:
            return
        except OSError:
            errors.append(str(p) + ':unreadable')
            return
        if budget['files'] >= max_files:
            errors.append('file_count_limit')
            return
        if stat.S_ISLNK(s.st_mode):
            result[str(p)] = {'link': os.readlink(p), 'uid': s.st_uid, 'mode': stat.S_IMODE(s.st_mode)}
            budget['files'] += 1
        elif stat.S_ISDIR(s.st_mode):
            if depth >= max_depth:
                errors.append(str(p) + ':depth_limit')
                return
            try:
                for child in sorted(p.iterdir()):
                    if budget['files'] >= max_files:
                        errors.append('file_count_limit'); break
                    visit(child, depth+1)
            except OSError:
                errors.append(str(p) + ':unreadable_directory')
        elif stat.S_ISREG(s.st_mode):
            budget['files'] += 1
            row = {'size': s.st_size, 'mtime_ns': s.st_mtime_ns, 'uid': s.st_uid, 'mode': stat.S_IMODE(s.st_mode)}
            if s.st_size <= 1024*1024 and budget['bytes'] + s.st_size <= max_bytes:
                try:
                    data = p.read_bytes()
                    budget['bytes'] += len(data)
                    row['sha256'] = hashlib.sha256(data).hexdigest()
                    # Hash/content comparison already accounts for legitimate touch operations.
                    row.pop('mtime_ns')
                    row['known_marker'] = any(m in data for m in MARKERS)
                except OSError:
                    errors.append(str(p) + ':unreadable')
            else:
                row['metadata_only'] = True
            result[str(p)] = row
    for root in roots:
        visit(Path(root), 0)
    return {'files': result, 'errors': sorted(set(errors)), 'budget': budget}


def hourly():
    out, counts = [], collections.Counter()
    known, temporary, skipped = 0, 0, 0
    pids = [p for p in Path('/proc').iterdir() if p.name.isdigit()]
    for p in pids[:6000]:
        try:
            counts[p.stat().st_uid] += 1
            cmd = (p / 'cmdline').open('rb').read(4096)
            if any(m in cmd for m in MARKERS):
                known += 1
            try:
                if os.readlink(p / 'exe').startswith(('/tmp/', '/var/tmp/')):
                    temporary += 1
            except OSError:
                pass
        except OSError:
            skipped += 1  # Processes may exit during this bounded snapshot.
    if len(pids) > 6000:
        out.append(finding('process_limit', '进程数量超过检查上限', '超过6000个；本轮只检查前6000个，不能视为无异常。', 'critical'))
    if known:
        out.append(finding('known_process', '本次入侵的恶意进程特征再次出现', '命中特征进程%d个；程序仅报警，不自动杀进程。' % known, 'critical'))
    if temporary:
        out.append(finding('temporary_executable', '临时目录中有程序正在运行', '发现%d个进程；需核对，不等于已经认定为病毒。' % temporary, 'critical'))
    if counts.get(297536, 0):
        out.append(finding('plex_restarted', '安全停用中的Plex重新出现进程', 'Plex账号进程%d个；当前尚未授权恢复。' % counts[297536], 'critical'))
    if len(pids) > 2000 or max(counts.values(), default=0) > 800:
        out.append(finding('process_explosion', '群晖进程数量异常增多', '总进程%d；单账号最大%d。' % (len(pids), max(counts.values(), default=0)), 'critical'))
    mem = {k: int(v.split()[0]) for k, v in (l.split(':', 1) for l in Path('/proc/meminfo').read_text().splitlines())}
    load15, cores = os.getloadavg()[2], os.cpu_count() or 4
    if load15 > cores*2 or mem['MemAvailable']/mem['MemTotal'] < .08:
        out.append(finding('resource_pressure', '群晖持续资源压力', '15分钟负载%.1f；可用内存%.1f%%。' % (load15, 100*mem['MemAvailable']/mem['MemTotal'])))
    for port in (23, 32400, 32402):
        with socket.socket() as sock:
            sock.settimeout(.3)
            if sock.connect_ex(('127.0.0.1', port)) == 0:
                out.append(finding('reopened_port_%d' % port, '已关闭的风险端口重新监听', '端口%d；需核对授权变更。' % port, 'critical'))
    # User explicitly stopped AV. Its stopped/stale status is informational,
    # not an alert to restart it, and no executable from that package is called.
    av_running = False
    for p in pids[:6000]:
        try:
            cmd = (p / 'cmdline').open('rb').read(1024)
            if cmd.startswith(b'/var/packages/AntiVirus/') or b'antivirus-priority-scan.py' in cmd:
                av_running = True; break
        except OSError:
            pass
    if av_running:
        out.append(finding('antivirus_unexpected_restart', '已暂停的杀毒任务重新运行', '用户已因卡顿暂停杀毒；请核对启动来源。'))
    return {'findings': out, 'process_count': len(pids), 'plex_process_count': counts.get(297536, 0),
            'load15': round(load15, 2), 'available_memory_mb': round(mem['MemAvailable']/1024),
            'antivirus_running': av_running, 'exited_during_read': skipped}


def daily():
    current = bounded_snapshot()
    baseline = load_json(BASE / 'baseline.json')
    out, changes = [], []
    if baseline is None:
        out.append(finding('missing_baseline', '安全配置基线缺失', '需要人工核对，不能自动将现状当作安全基线。'))
    else:
        old, new = baseline['files'], current['files']
        changes = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
        if changes:
            out.append(finding('configuration_changed', '启动项、账号或Plex代码目录发生变化', '共%d处；详细路径保存在NAS daily.json，需区分授权修改与入侵。' % len(changes)))
    if current['errors']:
        out.append(finding('incomplete_read', '每日安全检查部分内容未读全', '达到限额或读取失败%d项；不将缺失判为正常。' % len(current['errors'])))
    if any(r.get('known_marker') for r in current['files'].values()):
        out.append(finding('malware_file_marker', '启动配置中出现已知恶意特征', '仅记录和提醒；未自动删除文件。', 'critical'))
    uid0 = [line.split(':')[0] for line in Path('/etc/passwd').read_text().splitlines() if line.split(':')[2] == '0']
    if uid0 != ['root']:
        out.append(finding('additional_root', '存在额外UID0账号', '需要人工核对管理员账号。', 'critical'))
    atomic_json(BASE / 'daily-snapshot.json', current)
    return {'findings': out, 'changed_paths': changes, 'read_errors': current['errors'],
            'budget': current['budget'], 'metadata_only_files': sum(bool(v.get('metadata_only')) for v in current['files'].values())}


def send(text, nonce):
    # Reuse ERP's configured ALERT group; no secret copied/exported, no orders/DB writes.
    # The tiny sender process only runs for a new alert/recovery, never for healthy polls.
    code = '''import json,sys
from app.database import SessionLocal
from app.services import notify_service,feishu_client
payload=json.load(sys.stdin)
assert payload['text'].startswith('【群晖与ERP安全监测】') and len(payload['text'].encode())<=8000
with SessionLocal() as db:
 cfg=notify_service.get_config(db)
 assert cfg['route_mode']=='feishu_split' and cfg['alert_chat_id']
 result=feishu_client.send_text(db,cfg['alert_chat_id'],payload['text'])
 print(json.dumps({'message_id':result.get('message_id')}))
'''
    p = subprocess.run([DOCKER, 'exec', '-i', 'panse-system-api-1', 'python', '-c', code],
                       input=json.dumps({'text': text, 'id': nonce}).encode(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
    if p.returncode:
        raise RuntimeError('feishu_sender_failed')
    return json.loads(p.stdout).get('message_id')


def audit():
    now = time.time()
    states = {name: load_json(BASE/(name+'.json'), {}) for name in ('hourly','daily')}
    stale = [name for name, limit in [('hourly', 7800), ('daily', 108000)]
             if not 0 <= now - states[name].get('checked_at', 0) < limit]
    j = Journal(BASE/'alerts.sqlite')
    summary = j.summary()
    reason = {'stale': stale, 'unknown_notifications': summary['unknown']}
    previous = load_json(BASE/'audit.json', {})
    report = {'checked_at': now, **reason, 'active_issues': summary['active'],
              'notify': reason != previous.get('reason') and bool(stale or summary['unknown']),
              'antivirus_scheduled': False, 'windows_external_monitor': False}
    atomic_json(BASE/'audit.json', {'reason': reason, **report})
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['hourly','daily','baseline','audit','commission','snapshot'])
    parser.add_argument('--no-send', action='store_true')
    args = parser.parse_args()
    BASE.mkdir(parents=True, exist_ok=True)
    os.chmod(BASE, 0o700)
    if args.mode == 'snapshot':
        print(json.dumps({n: load_json(BASE/(n+'.json'), {}) for n in ('hourly','daily')}, ensure_ascii=False))
        return
    with (BASE/'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('{"status":"already_running"}'); return
        start = time.monotonic()
        cpu = time.process_time()
        if args.mode == 'baseline':
            if (BASE/'baseline.json').exists():
                raise RuntimeError('baseline_exists_manual_review_required')
            result = bounded_snapshot()
            if result['errors']:
                raise RuntimeError('cannot_accept_incomplete_baseline')
            atomic_json(BASE/'baseline.json', result)
            result = {'status':'baseline_created_not_clean_bill', 'budget':result['budget']}
        elif args.mode == 'audit':
            result = audit()
        else:
            if args.mode == 'commission':
                result = {'findings': [finding('monitor_enabled','群晖轻量安全监测已启用',
                          '每小时异常检查；每天08:20检查少量启动配置、账号与Plex代码目录。相同故障只提醒一次，已确认送达的故障恢复后提醒一次。杀毒更新/扫描保持停止；没有Windows5分钟检查。')]}
            else:
                result = globals()[args.mode]()
            result['checked_at'] = time.time()
            result['wall_seconds'] = round(time.monotonic()-start, 3)
            result['cpu_seconds'] = round(time.process_time()-cpu, 4)
            result['max_rss_kb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            atomic_json(BASE/(args.mode+'.json'), result)
            journal = Journal(BASE/'alerts.sqlite')
            journal.observe(args.mode, result['findings'])
            result['delivery'] = {'sent':False,'reason':'validation_no_send'} if args.no_send else journal.dispatch(send)
            atomic_json(BASE/(args.mode+'.json'), result)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
