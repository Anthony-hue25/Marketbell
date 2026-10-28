"""Measured local benchmarks (mocked inference): API latency under load and N-event workloads."""
import json, statistics, sys, threading, time
from harness import *

def pct(values, p):
    values = sorted(values)
    return round(values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))] * 1000, 1) if values else None

def api_load(concurrency, seconds, path='/api/state'):
    users = [session('192.0.2.%d' % (i + 1)) for i in range(concurrency)]
    lat, errors, stop = [], [0], time.time() + seconds
    def run(i):
        cookie, _ = users[i]
        while time.time() < stop:
            t = time.perf_counter()
            try:
                r = requests.get(API + path, headers=headers('192.0.2.%d' % (i + 1), cookie), timeout=30)
                if r.status_code != 200: errors[0] += 1
            except Exception:
                errors[0] += 1
            lat.append(time.perf_counter() - t)
    threads = [threading.Thread(target=run, args=(i,)) for i in range(concurrency)]
    [t.start() for t in threads]; [t.join() for t in threads]
    return {'path': path, 'concurrency': concurrency, 'requests': len(lat), 'rps': round(len(lat) / seconds, 1),
            'p50_ms': pct(lat, 50), 'p95_ms': pct(lat, 95), 'p99_ms': pct(lat, 99), 'errors': errors[0]}

def workload(n, delay, tag):
    control(NEM, delay=delay, fail_remaining=0)
    t0 = time.time()
    ids = [webhook(['NVDA', 'JPM', 'PSX', 'VLO'][i % 4], f'{tag} workload event {i}: company announces an operating update.', f'{tag}-{i}')[1]['job'] for i in range(n)]
    inject = time.time() - t0
    q = ','.join("'%s'" % i for i in ids)
    samples = []
    def sample():
        out = sh("docker stats --no-stream --format '{{.CPUPerc}}|{{.MemUsage}}' mb-worker", check=False)
        if out: samples.append(out)
        return psql(f"SELECT count(*) FROM jobs WHERE id IN ({q}) AND status IN ('completed','failed','completed_with_errors')") == str(n)
    wait(sample, 3600, 3)
    total = time.time() - t0
    rows = psql(f"SELECT julianday(started_at)-julianday(created), julianday(updated)-julianday(started_at), julianday(updated)-julianday(created), status FROM jobs WHERE id IN ({q})")
    qd, run, e2e, st = [], [], [], {}
    for r in rows.splitlines():
        a, b, c, s = r.split('|'); qd.append(float(a) * 86400); run.append(float(b) * 86400); e2e.append(float(c) * 86400); st[s] = st.get(s, 0) + 1
    cpu = [float(s.split('|')[0].rstrip('%')) for s in samples if s.split('|')[0].rstrip('%')]
    mem = [s.split('|')[1].split('/')[0].strip() for s in samples]
    return {'events': n, 'mock_model_delay_s': delay, 'inject_seconds': round(inject, 2), 'total_seconds': round(total, 1),
            'throughput_per_min': round(n / total * 60, 1), 'queue_delay_p50_s': round(statistics.median(qd), 2), 'queue_delay_max_s': round(max(qd), 1),
            'run_p50_s': round(statistics.median(run), 2), 'run_p95_s': round(sorted(run)[int(0.95 * (len(run) - 1))], 2),
            'end_to_end_p50_s': round(statistics.median(e2e), 1), 'end_to_end_max_s': round(max(e2e), 1), 'statuses': st,
            'worker_cpu_pct_max': max(cpu) if cpu else None, 'worker_cpu_pct_avg': round(sum(cpu) / len(cpu), 1) if cpu else None,
            'worker_mem_samples': mem[:1] + mem[-1:]}

if __name__ == '__main__':
    print(json.dumps(eval(sys.argv[1]), indent=1))
