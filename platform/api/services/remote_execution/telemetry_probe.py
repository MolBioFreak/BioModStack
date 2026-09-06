"""Read-only, stdlib-only worker snapshot; sent over the existing SSH transport."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def number(value):
    try:
        value = float(value)
        return value if 0 <= value < float('inf') else None
    except (ValueError, TypeError):
        return None


def snapshot(root):
    result = {'worker_time': time.monotonic(), 'gpus': [], 'cpu': {}, 'ram': {}, 'disk': {}, 'network': {}}
    query = 'index,uuid,name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw'
    try:
        proc = subprocess.run(['nvidia-smi', '--query-gpu=' + query, '--format=csv,noheader,nounits'],
                              capture_output=True, text=True, timeout=3, check=True)
        for line in proc.stdout.splitlines()[:64]:
            cells = [s.strip() for s in line.split(',')]
            if len(cells) == 8 and cells[0].isdigit():
                result['gpus'].append(dict(index=int(cells[0]), uuid=cells[1][:100], name=cells[2][:160],
                    memory_total_mb=number(cells[3]), memory_used_mb=number(cells[4]),
                    utilization=number(cells[5]), temperature=number(cells[6]), power_draw_w=number(cells[7])))
    except (OSError, subprocess.SubprocessError):
        pass
    # Measure the worker envelope, NOT the transient SSH session cgroup:
    # jobs run under different sessions. A VM uses host counters; a private
    # container cgroup namespace exposes its workload envelope at the mount root.
    cg = Path('/sys/fs/cgroup')
    v2 = (cg / 'cgroup.controllers').exists() and (cg / 'memory.max').exists()
    allocation = len(os.sched_getaffinity(0))
    quota = read(cg / 'cpu.max') if v2 else None
    if quota:
        q, period = quota.split()
        if number(q) is not None and number(period):
            allocation = min(allocation, float(q) / float(period))
    cpu = {'allocated_cores': allocation, 'scope': 'cgroup' if v2 else 'host', 'usage_seconds': None}
    stats = read(cg / 'cpu.stat') if v2 else None
    if stats:
        entries = dict(line.split() for line in stats.splitlines())
        usage = number(entries.get('usage_usec'))
        cpu['usage_seconds'] = usage / 1e6 if usage is not None else None
    else:
        stat = read('/proc/stat')
        if stat:
            ticks = [int(v) for v in stat.splitlines()[0].split()[1:9]]
            cpu['host_total_ticks'] = sum(ticks)
            cpu['host_idle_ticks'] = ticks[3] + ticks[4]
    result['cpu'] = cpu
    mem = read('/proc/meminfo') or ''
    mem = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in mem.splitlines()}
    limit = number(read(cg / 'memory.max')) if v2 else None
    used = number(read(cg / 'memory.current')) if v2 else None
    result['ram'] = {'scope': 'cgroup' if v2 and used is not None else 'host',
                     'limit_bytes': min(limit, mem['MemTotal']) if limit is not None else mem.get('MemTotal'),
                     'used_bytes': used if used is not None else
                         mem.get('MemTotal', 0) - mem.get('MemAvailable', 0) if 'MemAvailable' in mem else None}
    try:
        disk = shutil.disk_usage(root)
        result['disk'] = {'path': root, 'total_bytes': disk.total, 'free_bytes': disk.free}
    except OSError:
        pass
    net = read('/proc/net/dev') or ''
    for line in net.splitlines()[2:66]:
        name, counters = line.split(':', 1)
        name = name.strip()
        if name == 'lo':
            continue
        fields = counters.split()
        result['network'][name] = {'rx_bytes': int(fields[0]), 'tx_bytes': int(fields[8])}
    return result


if __name__ == '__main__':
    print(json.dumps(snapshot(sys.argv[1]), separators=(',', ':'), allow_nan=False))
