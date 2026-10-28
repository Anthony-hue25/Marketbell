"""Decision explanation contract: what happened, who may act, and what kind of statement each line is.

Pure functions over records MarketBell already keeps (the event, its sealed evidence and the decision
context captured at resolution). Nothing here calls a model, changes a decision or grants authority; it
labels what already happened so that observed facts, model interpretation, proposals, approvals and
verified actions are never confused.

Decision rights are derived from the existing case statuses rather than a second status system:
  needs            -> OWNER_REVIEW   (approve, reject or correct)
  handled          -> AUTO_HANDLE    (routine no-action, or a note under an approved standing rule)
                      or OWNER_REVIEW when the owner decided it
  rejected         -> OWNER_REVIEW   (owner rejected)
  blocked          -> BLOCKED        (hard policy failed or evidence missing)
  held             -> MONITOR        (reviews uncertain or disagree, or unowned risk)
  queued/investigating -> IN_PROGRESS
  failed           -> NOT_VERIFIED   (fail safe: no action, no synthetic verdict)
"""
from datetime import timedelta
from .config import timestamp

FRESH_DAYS = 3
STALE_DAYS = 7          # matches the 7-day news lookback; older items are never treated as current
HORIZONS = (7, 30, 90)
WORKFLOW_NOTE = ('This record shows the workflow ran and your policy was applied. It is not evidence that the '
                 'research is right or that acting on it would be profitable.')

RIGHTS = {
    'AUTO_HANDLE': 'Handled within authority',
    'OWNER_REVIEW': 'Owner review',
    'BLOCKED': 'Blocked',
    'MONITOR': 'Monitoring',
    'IN_PROGRESS': 'In progress',
    'NOT_VERIFIED': 'Not verified',
}


def _time(value):
    try:
        return timestamp(value)
    except (TypeError, ValueError, AttributeError):
        return None


def reference_time(event):
    """Decision-time knowledge boundary. A recorded replay, or fresh inference over a preserved evidence
    package, is judged at that package's session, not today."""
    if event.get('mode') == 'verified' or event.get('recording') or event.get('simulation'):
        t = _time(str(event.get('session', '')) + 'T23:59:59+00:00')
        if t:
            return t
    return _time(event.get('created'))


def freshness(event):
    """Age of each evidence item at detection, labelled fresh / aging / stale / undated."""
    ref = reference_time(event)
    items = []
    for e in event.get('evidence', []):
        published = _time(e.get('published_at'))
        when = published or _time(e.get('retrieved_at'))   # undated items are aged from retrieval, and say so
        if e.get('type') == 'daily_market_data':
            label = 'price baseline'
        elif not when or not ref:
            label = 'undated'
        else:
            age = ref - when
            label = 'stale' if age > timedelta(days=STALE_DAYS) else 'aging' if age > timedelta(days=FRESH_DAYS) else 'fresh'
        items.append({'id': e.get('id'), 'type': e.get('type'), 'publisher': e.get('publisher'),
                      'at': when.isoformat() if when else None, 'dated_by': 'published' if published else 'retrieved' if when else None, 'freshness': label, 'limitation': e.get('limitation')})
    return items


def cited_ids(event):
    return {c.get('evidence_id') for k in ('research', 'challenge') for c in (event.get(k) or {}).get('claims', [])}


def evidence_current(event):
    """At least one cited item (other than price data) is no older than STALE_DAYS at detection.
    Recorded replays and labelled simulations are judged at their own session."""
    cited = cited_ids(event)
    fresh = [i for i in freshness(event) if i['id'] in cited and i['type'] != 'daily_market_data']
    return any(i['freshness'] in ('fresh', 'aging') for i in fresh)


def decision_right(event):
    s = event.get('status')
    owner = bool(event.get('user_involved'))
    if s == 'needs':
        return 'OWNER_REVIEW'
    if s == 'handled':
        return 'OWNER_REVIEW' if owner else 'AUTO_HANDLE'
    if s == 'rejected':
        return 'OWNER_REVIEW'
    if s == 'blocked':
        return 'BLOCKED'
    if s == 'held':
        return 'MONITOR'
    if s == 'failed':
        return 'NOT_VERIFIED'
    return 'IN_PROGRESS'


def stage(event):
    """The furthest point of the research -> authority -> action chain this case actually reached."""
    s, action = event.get('status'), event.get('action')
    if s in ('queued', 'investigating'):
        return ('interpretation', 'AI interpretation in progress') if event.get('research') else ('evidence', 'Evidence collected; investigation in progress')
    if s == 'failed':
        return 'not_verified', 'Not verified. No action taken'
    if s == 'needs':
        return 'proposed', 'Proposed only. Not executed; waiting for your decision'
    if s == 'rejected':
        return 'rejected', 'Rejected by you. Nothing prepared'
    if s == 'blocked':
        return 'blocked', 'Blocked by policy. Nothing prepared'
    if s == 'held':
        return 'monitoring', 'Monitoring. No action proposed'
    if s == 'handled' and action and action.get('verified'):
        if event.get('ruleId'):
            return 'completed_verified', 'Completed and verified under your standing rule: research note saved and read back. No order'
        return 'completed_verified', 'Approved by you, then completed and verified: research note saved and read back. No order'
    if s == 'handled' and event.get('user_involved'):
        return 'acknowledged', 'Acknowledged by you. Nothing prepared'
    return 'no_action', 'Reviewed. No action needed'


def _why(event, right):
    s = event.get('status')
    if right == 'AUTO_HANDLE' and event.get('ruleId'):
        return ('Your approved standing rule covered this case, fresh policy checks passed and both independent '
                'reviews cleared, so MarketBell prepared the note without asking. Approval of earlier cases did not extend this rule.')
    if right == 'AUTO_HANDLE':
        return 'Both reviews found nothing that needs action. No action was taken, so no authority was needed.'
    if right == 'OWNER_REVIEW' and s == 'needs':
        if event.get('rule_conflict'):
            return 'Your standing rules conflict on this case, so it comes back to you.'
        if event.get('rule_withheld'):
            return event.get('rule_withheld_reason') or 'A standing rule matched, but the evidence was too thin to use it automatically.'
        if event.get('kind') == 'Risk':
            return 'A material risk to something you follow is your judgement to make.'
        return 'No approved standing rule covers this case, so preparing a note needs your approval.'
    if right == 'OWNER_REVIEW':
        return 'You made this decision.'
    if right == 'BLOCKED':
        return 'A hard policy check failed. Policies outrank rules and model recommendations.'
    if right == 'MONITOR':
        return 'The independent reviews were not both clear (or the risk concerns a stock you do not own), so nothing is proposed.'
    if right == 'NOT_VERIFIED':
        return 'The investigation could not be verified (' + str(event.get('error') or 'technical failure') + '). MarketBell does not substitute a verdict.'
    return 'Investigation not finished.'


def cautions(event):
    """Deterministic research-quality challenges. Each states a limitation; none is a risk metric."""
    out = []
    ctx = event.get('decision_context') or {}
    cited = cited_ids(event)
    fresh = {i['id']: i for i in freshness(event)}
    cited_items = [fresh[i] for i in cited if i in fresh]
    if any(i['freshness'] == 'stale' for i in cited_items):
        out.append(f'Some cited evidence was more than {STALE_DAYS} days old when this case was detected.')
    types = {i['type'] for i in cited_items}
    if cited_items and types <= {'headline_only', 'daily_market_data'}:
        out.append('Support is headlines or price data only. A recent price move is not demonstrated profitability, and past returns are not expected returns.')
    reviews = [event.get(k) for k in ('research', 'challenge') if event.get(k)]
    if reviews and all(r.get('decision') == 'clear' for r in reviews) and len(cited) <= 1:
        out.append('Both reviews rest on a single cited source; strong conclusions from limited evidence deserve caution.')
    if len(reviews) == 2 and (reviews[0].get('decision') != reviews[1].get('decision') or reviews[0].get('classification') != reviews[1].get('classification')):
        out.append('The two independent reviews disagreed.')
    if ctx.get('held') and event.get('kind') == 'Opportunity':
        out.append(f"You already hold {event.get('symbol')}; more research here adds to the same position rather than diversifying.")
    if ctx.get('held') and ctx.get('watched'):
        out.append(f"{event.get('symbol')} is both held and on your watchlist, so its news counts twice in your attention.")
    limit, exposure = ctx.get('max_sector_pct'), ctx.get('sector_exposure_pct')
    if event.get('kind') == 'Opportunity' and type(limit) in (int, float) and type(exposure) in (int, float) and limit > 0 and exposure >= limit / 2:
        out.append(f"{event.get('sector')} is already {exposure:g}% of your portfolio (limit {limit:g}%); a similar company may overlap rather than diversify.")
    if event.get('mode') == 'verified':
        out.append('Recorded run replay: a historical, hypothetical outcome in your demo workspace, not a live decision.')
    if event.get('simulation'):
        out.append('Illustrative simulation; not live operation.')
    return out


def rules_section(event):
    ctx = event.get('decision_context') or {}
    lines = []
    if event.get('ruleId'):
        lines.append(f"Applied: standing rule {event['ruleId'][:8]} v{event.get('rule_version')}. " + (ctx.get('applied_rule_text') or ''))
    for r in event.get('rule_withheld') or []:
        lines.append(f"Matched but not applied automatically: rule {r[:8]}. " + (event.get('rule_withheld_reason') or ''))
    if event.get('rule_conflict'):
        lines.append('Conflicting rules: ' + ', '.join(r[:8] for r in event['rule_conflict']) + '.')
    other = [r for r in ctx.get('rules', []) if not r.get('matched')]
    if other:
        lines.append(f"{len(other)} other rule(s) checked and not matching (scope, expiry, approval timing or limits).")
    return lines


def downside(event):
    out = []
    for key in event.get('blocked_checks') or []:
        out.append({'kind': 'policy', 'text': 'Policy check failed: ' + key.replace('_', ' ') + '.'})
    ch = event.get('challenge') or {}
    if ch and not ch.get('skipped') and (ch.get('decision') != 'clear' or ch.get('classification') == 'Risk'):
        out.append({'kind': 'model_interpretation', 'text': ch.get('summary', '')})
    return out


def outcome_check(event):
    s, kind = event.get('status'), event.get('kind')
    if s in ('blocked', 'failed', 'rejected', 'queued', 'investigating') or kind == 'Routine' and s != 'needs':
        return None
    days = ' / '.join(str(h) for h in HORIZONS)
    if kind == 'Risk':
        return f'At {days} days: whether later company disclosures confirm or ease this risk.'
    if kind == 'Opportunity':
        return f"At {days} days: whether later evidence supports or contradicts the note's reasoning. A price rise alone would not show it was right."
    return None


def proposed_action(event):
    if event.get('status') in ('queued', 'investigating', 'failed'):
        return None
    if event.get('kind') == 'Risk' and event.get('status') == 'needs':
        return 'Review and acknowledge this risk. No note, ownership change or order.'
    if event.get('kind') == 'Opportunity' and event.get('status') in ('needs', 'handled'):
        return 'Prepare a research consideration note. Never an order.'
    return 'None.'


def explain(event):
    right = decision_right(event)
    key, label = stage(event)
    base = {'decision_right': right, 'decision_right_label': RIGHTS[right], 'stage': key, 'stage_label': label,
            'why_authority': _why(event, right),
            'what_happened': {'kind': 'observed', 'text': event.get('trigger')}}
    if event.get('status') == 'handled' and key == 'no_action' and not event.get('user_involved'):
        base['level'] = 'brief'
        return base
    research = event.get('research') or {}
    fresh = freshness(event)
    cited = cited_ids(event)
    labels = [i['freshness'] for i in fresh if i['id'] in cited and i['type'] != 'daily_market_data']
    overall = ('stale' if labels and all(l == 'stale' for l in labels) else 'fresh' if labels and all(l == 'fresh' for l in labels)
               else 'mixed' if labels else 'none cited')
    price = (event.get('policy') or {}).get('completed_session_fresh')
    base.update(level='full',
        why_it_matters={'kind': 'model_interpretation', 'text': research.get('summary')} if research else None,
        evidence={'kind': 'observed', 'items': fresh, 'cited_freshness': overall,
                  'price_baseline': None if price is None else ('verified for the latest completed session' if price else 'not verified'),
                  'sufficiency': event.get('evidence_sufficiency')},
        uncertainty={'kind': 'model_interpretation', 'text': (event.get('challenge') or {}).get('summary')} if event.get('challenge') else None,
        downside=downside(event), cautions=cautions(event), rules=rules_section(event),
        proposed_action=proposed_action(event), outcome_to_check=outcome_check(event), note=WORKFLOW_NOTE)
    return base


def operations(events, deferred=0, verified_outcomes=0):
    """Counts straight from case records. Nothing is inferred or added."""
    counts = {k: 0 for k in RIGHTS}
    rule_applied = 0
    for e in events:
        counts[decision_right(e)] += 1
        rule_applied += 1 if e.get('ruleId') and e.get('status') == 'handled' else 0
    return {'handled_without_you': counts['AUTO_HANDLE'], 'under_standing_rule': rule_applied,
            'needs_you': sum(1 for e in events if e.get('status') == 'needs'),
            'decided_by_you': sum(1 for e in events if e.get('status') in ('handled', 'rejected') and e.get('user_involved')),
            'rejected': sum(1 for e in events if e.get('status') == 'rejected'),
            'blocked': counts['BLOCKED'], 'monitoring': counts['MONITOR'],
            'awaiting_evidence': counts['IN_PROGRESS'] + int(deferred or 0), 'not_verified': counts['NOT_VERIFIED'],
            'verified_outcomes': int(verified_outcomes or 0)}
