"""Reproducible Azure + Nebius monthly cost model (docs/AZURE_COST_MODEL.md is generated from this).

Rates: Azure Retail Prices API, region eastus2, USD, retrieved 2026-10-19 unless marked (A) = assumption
or list price not retrieved through the API in this session. Re-run after changing assumptions:
    python -m scripts.cost_model
"""
import json

S = 730 * 3600  # seconds per month (730 h)
R = {  # USD
    'aca_vcpu_active_s': 0.000024, 'aca_mem_active_gib_s': 0.000003,
    'aca_vcpu_idle_s': 0.000003, 'aca_mem_idle_gib_s': 0.000003, 'aca_requests_m': 0.40,
    'aca_free_vcpu_s': 180000, 'aca_free_gib_s': 360000, 'aca_free_requests': 2_000_000,
    'pg_b1ms_h': 0.017, 'pg_b2s_h': 0.068, 'pg_storage_gb': 0.115,  # storage (A): list price, not API-verified
    'pg_backup_gb': 0.095, 'acr_standard_day': 0.6666,  # (A) list price; Standard is the free-account tier
    'kv_10k_ops': 0.03,
    'log_ingest_gb': 2.76, 'log_free_gb': 5, 'alert_15min': 0.50, 'private_dns_zone': 0.50,  # DNS zone (A)
    'blob_gb': 0.021,  # (A)
    'nebius_in_m': 0.30, 'nebius_out_m': 0.90,  # third-party listing of Nebius prices (A): verify on Nebius billing
}
TOKENS = {'in': 1500, 'out': 600}  # per model call; measured small cases ~510 in / ~460 out (incl. reasoning)
CALLS_PER_INVESTIGATION = 2.5      # Research + Challenge + share of retries and 7/30/90-day follow-ups

def aca(cpu, mem, active_fraction, idle_eligible=True):
    a, i = S * active_fraction, S * (1 - active_fraction)
    if not idle_eligible:
        a, i = S, 0
    return {'vcpu_s_active': cpu * a, 'gib_s_active': mem * a, 'vcpu_s_idle': cpu * i, 'gib_s_idle': mem * i}

def job(cpu, mem, runs, seconds):
    return {'vcpu_s_active': cpu * runs * seconds, 'gib_s_active': mem * runs * seconds, 'vcpu_s_idle': 0, 'gib_s_idle': 0}

def compute_cost(parts, requests):
    va = sum(p['vcpu_s_active'] for p in parts); ga = sum(p['gib_s_active'] for p in parts)
    vi = sum(p['vcpu_s_idle'] for p in parts); gi = sum(p['gib_s_idle'] for p in parts)
    # Free grants apply to usage first (applied to active usage here: conservative ordering is not
    # documented, so the grant is applied to the higher-priced active seconds).
    va_b = max(0, va - R['aca_free_vcpu_s']); ga_b = max(0, ga - R['aca_free_gib_s'])
    return round(va_b * R['aca_vcpu_active_s'] + ga_b * R['aca_mem_active_gib_s'] + vi * R['aca_vcpu_idle_s'] + gi * R['aca_mem_idle_gib_s']
                 + max(0, requests - R['aca_free_requests']) / 1e6 * R['aca_requests_m'], 2)

SCENARIOS = {
    'demo':     {'label': '1. Hackathon public demo', 'investigations_day': 20, 'requests': 60_000, 'api_active': 0.03, 'api': (0.25, 0.5),
                 'worker': (0.25, 0.5), 'worker_active': 0.15, 'pg': 'pg_b1ms_h', 'pg_gb': 32, 'logs_gb': 1.0, 'job_seconds': 45},
    'personal': {'label': '2. Typical personal use', 'investigations_day': 30, 'requests': 30_000, 'api_active': 0.02, 'api': (0.25, 0.5),
                 'worker': (0.25, 0.5), 'worker_active': 0.2, 'pg': 'pg_b1ms_h', 'pg_gb': 32, 'logs_gb': 1.0, 'job_seconds': 50},
    'moderate': {'label': '3. Moderate multi-user (~50 active users)', 'investigations_day': 150, 'requests': 1_500_000, 'api_active': 0.25, 'api': (0.5, 1.0),
                 'worker': (0.5, 1.0), 'worker_active': 0.5, 'pg': 'pg_b1ms_h', 'pg_gb': 32, 'logs_gb': 3.0, 'job_seconds': 120},
    'stress':   {'label': '4. High news volume stress', 'investigations_day': 1000, 'requests': 3_000_000, 'api_active': 0.4, 'api': (0.5, 1.0),
                 'worker': (0.5, 1.0), 'worker_active': 1.0, 'pg': 'pg_b2s_h', 'pg_gb': 64, 'logs_gb': 8.0, 'job_seconds': 280},
}

def scenario(key, s, mode):
    api = aca(*s['api'], s['api_active'])
    if mode == 'continuous':
        worker = aca(*s['worker'], s['worker_active'])
        worker_hi = aca(*s['worker'], 1.0, idle_eligible=False)  # if the worker never qualifies as idle
    else:
        runs = 12 * 24 * 30.4 if mode == 'scheduled' else 24 * 30.4  # every 5 minutes / hourly
        worker = job(*s['worker'], runs, s['job_seconds'] * (1 if mode == 'scheduled' else 3))  # hourly runs carry more items
        worker_hi = worker
    anchor = job(0.25, 0.5, 30, 60)
    lines = {
        'Container Apps compute + requests': compute_cost([api, worker, anchor], s['requests']),
        'PostgreSQL Flexible compute': round(R[s['pg']] * 730, 2),
        'PostgreSQL storage (A)': round(R['pg_storage_gb'] * s['pg_gb'], 2),
        'PostgreSQL backups (within free allowance (A))': 0.0,
        'Container Registry Standard (A)': round(R['acr_standard_day'] * 30.4, 2),
        'Key Vault operations': round(max(0.01, 5000 / 10000 * R['kv_10k_ops']), 2),
        'Log Analytics ingestion': round(max(0, s['logs_gb'] - R['log_free_gb']) * R['log_ingest_gb'], 2),
        'Log search alerts (8 x 15 min)': round(8 * R['alert_15min'], 2),
        'Private DNS zone (vnet mode) (A)': R['private_dns_zone'],
        'Blob anchors (A)': 0.01,
        'Network egress (< 100 GB free (A))': 0.0,
    }
    azure = round(sum(lines.values()), 2)
    # New Azure free account, first 12 months: PostgreSQL Flexible B1ms (750 h) with 32 GB, and one
    # Standard container registry, are included (A: eligibility is per account; verify on the first bill).
    free_items = ('PostgreSQL Flexible compute', 'PostgreSQL storage (A)', 'Container Registry Standard (A)') if s['pg'] == 'pg_b1ms_h' and s['pg_gb'] <= 32 else ()
    azure_free = round(azure - sum(lines[k] for k in free_items), 2)
    upper = round(azure - lines['Container Apps compute + requests'] + compute_cost([api, worker_hi, anchor], s['requests']), 2)
    calls = s['investigations_day'] * 30.4 * CALLS_PER_INVESTIGATION
    nebius = round(calls * (TOKENS['in'] * R['nebius_in_m'] + TOKENS['out'] * R['nebius_out_m']) / 1e6, 2)
    return {'scenario': s['label'], 'worker_mode': mode, 'lines': lines, 'azure_total': azure, 'azure_upper_if_worker_never_idle': upper,
            'azure_total_free_account_first_12_months': azure_free, 'nebius_model_calls': round(calls), 'nebius_estimate': nebius, 'news_data': 0.0, 'total': round(azure + nebius, 2),
            'total_upper': round(upper + nebius, 2)}

def main():
    out = [scenario(k, s, m) for k, s in SCENARIOS.items() for m in ('continuous', 'scheduled', 'hourly')]
    print(json.dumps({'rates': R, 'tokens_per_call': TOKENS, 'calls_per_investigation': CALLS_PER_INVESTIGATION, 'results': out}, indent=1))

if __name__ == '__main__':
    main()
