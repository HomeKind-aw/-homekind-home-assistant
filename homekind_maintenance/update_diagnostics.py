"""Private bounded observations. No credentials, media URLs or content names."""
from datetime import datetime, timezone
import json
from .maintenance import atomic
from .windows import evaluate
from .window_sync import READINESS

LABELS = {
    'ready': 'Ready for the approved update window.',
    'window_open': 'The approved update window is open.',
    'outside_window': 'Waiting for the saved update window.',
    'approval_needed': 'The available version needs central release clearance.',
    'script_running': 'A Home Assistant script is still running.',
    'home_in_use': 'Waiting for audio served by Home Assistant to finish.',
    'media_unverified': 'A playing media source could not be verified as independent of Home Assistant.',
    'readiness_unavailable': 'The local readiness check could not be completed.',
    'updates_disabled': 'Managed system updates are switched off locally.',
    'connector_busy': 'Waiting for connector maintenance to finish.',
    'supervisor_unhealthy': 'Home Assistant reports a system health issue.',
    'supervisor_unsupported': 'Home Assistant reports an unsupported setup.',
    'non_stable_channel': 'Home Assistant must use the stable release channel.',
    'core_unavailable': 'Waiting for the Home Assistant API to respond.',
    'permission_unverified': 'The saved update authorisation needs to be checked.',
    'cloud_unavailable': 'Waiting for a fresh response from Belmelo.',
    'controls_busy': 'Waiting for a current device-control request to finish.',
    'paused': 'Automatic updates are paused.',
    'deferred': 'Automatic updates have been deferred.',
    'clock_rollback': 'The local clock moved backwards; the update is held.',
    'previous_outcome_unknown': 'A previous maintenance outcome needs review.',
    'update_in_progress': 'The update or its backup/health checks are in progress.',
    'already_completed': 'This update window has already been used.',
    'insufficient_time': 'There is not enough time left in this window to start safely.',
    'local_opt_out': 'Automatic updates are switched off in the saved choices.',
    'settings_pending': 'Waiting for the saved update choices to be acknowledged.',
    'healthy': 'The installed version passed the local health check.',
    'failed': 'An update attempt failed; its saved outcome needs review.',
    'cancelled': 'The update was cancelled before dispatch.',
}


def read_observation(directory):
    path = directory / 'update-check.json'
    try:
        if not path.exists() or path.stat().st_size > 16384:
            return None
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or set(value) != {'schema', 'reason', 'readiness', 'revision', 'zone', 'blockers', 'background_audio', 'at', 'history'} or type(value.get('schema')) is not int or value['schema'] != 1 or value.get('reason') not in LABELS or value.get('readiness') not in READINESS:
            return None
        if type(value['revision']) is not int or not 0 <= value['revision'] <= 2147483647 or not isinstance(value['zone'], str) or len(value['zone']) > 64 or type(value['background_audio']) is not int or not 0 <= value['background_audio'] <= 64 or type(value['at']) not in (int, float) or not 0 <= value['at'] <= 9999999999:
            return None
        if not isinstance(value['blockers'], list) or len(value['blockers']) > 16 or any(not isinstance(s, str) or len(s) > 160 for s in value['blockers']):
            return None
        if not isinstance(value['history'], list) or len(value['history']) > 16 or any(not isinstance(h, dict) or set(h) != {'at', 'reason', 'revision'} or type(h['at']) not in (int, float) or not 0 <= h['at'] <= 9999999999 or h['reason'] not in LABELS or type(h['revision']) is not int for h in value['history']):
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


def observe(companion, journal, executor, reason, response):
    """Diagnostics never authorise, cancel, repair or retry an operation."""
    try:
        now = companion.clock(); data = journal.read()
        phase = executor.job['phase'] if executor.job else None
        if phase in ('healthy', 'failed', 'cancelled'):
            status = phase
        elif phase:
            status = 'previous_outcome_unknown' if phase == 'unknown' or phase.endswith('_dispatch') else 'update_in_progress'
        elif reason != 'ready':
            status = reason
        elif response is None:
            status = 'cloud_unavailable'
        elif not data or response.get('desired') != {k: data[k] for k in ('policy', 'paused', 'defer_until')}:
            status = 'settings_pending'
        elif response.get('authority_current') is not True:
            status = 'permission_unverified'
        elif response.get('executor_ready') is not True:
            status = 'controls_busy'
        elif not response.get('approved'):
            status = 'approval_needed'
        else:
            status = evaluate(data['policy'], datetime.fromtimestamp(now, timezone.utc),
                estimate_seconds=1800, locally_enabled=True, authority_current=True,
                service_ready=True, active_use=False, paused=data['paused'],
                defer_until=datetime.fromisoformat(data['defer_until']) if data['defer_until'] else None,
                last_occurrence=data['last_occurrence'],
                last_started_at=datetime.fromisoformat(data['last_started_at']) if data['last_started_at'] else None,
                inflight=data['inflight'])['reason']
        if status not in LABELS:
            status = 'readiness_unavailable'
        detail = getattr(executor, 'activity', {})
        blockers = (detail.get('scripts', []) + detail.get('players', []))[:16]
        if any(not isinstance(s, str) or len(s) > 160 for s in blockers):
            blockers = []
        current = {'schema': 1, 'reason': status, 'readiness': reason,
                   'revision': data['policy']['revision'] if data else 0,
                   'zone': data['policy']['zone'] if data else 'UTC',
                   'blockers': blockers, 'background_audio': int(detail.get('background_audio', 0))}
        previous = read_observation(companion.directory)
        comparison = {k: previous.get(k) for k in current} if previous else None
        changed = comparison != current
        if not changed and 0 <= now - previous.get('at', 0) < 300:
            return
        history = previous.get('history', [])[-15:] if previous else []
        if changed:
            history.append({'at': now, 'reason': status, 'revision': current['revision']})
        current.update(at=now, history=history[-16:])
        atomic(companion.directory / 'update-check.json', current)
        if changed:
            print('System update check: ' + status + '.', flush=True)
    except Exception:
        pass  # Optional diagnostics cannot stop ordinary controls or recovery.
