"""Coordinated maintenance for the existing archive-worker.service.

No credentials or proxy values belong in this module or its output.
The watchdog uses the same control lock; systemd also checks the marker.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

SERVICE = 'archive-worker.service'
ROOT = Path(__file__).resolve().parent
NETWORK_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy',
                'SSL_CERT_FILE', 'SSL_CERT_DIR', 'GIT_SSL_CAINFO', 'CURL_CA_BUNDLE')


class ControlError(RuntimeError):
    pass


@contextmanager
def control_lock(root, timeout=10):
    path = Path(root) / '.worker-control.lock'
    with path.open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ControlError('Worker control busy; no state changed')
                time.sleep(.05)
        try:
            yield
        finally:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_private(path, data):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class WorkerControl:
    def __init__(self, root=ROOT, runner=None):
        self.root = Path(root)
        self.marker = self.root / '.worker-maintenance.json'
        self.runner = runner or subprocess.run

    def command(self, *args):
        try:
            result = self.runner(['systemctl', *args], capture_output=True, text=True, timeout=170)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ControlError('systemctl operation did not complete: '+type(exc).__name__) from exc
        if result.returncode:
            # Do not echo service output: it may include private environment.
            raise ControlError('systemctl '+args[0]+' failed (exit '+str(result.returncode)+')')
        return result.stdout

    def state(self):
        raw = self.command('show', SERVICE, '--property=ActiveState,SubState,MainPID,ControlPID')
        return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)

    def require_stopped(self):
        state = self.state()
        if (state.get('ActiveState') not in ('inactive', 'failed')
                or state.get('MainPID') != '0' or state.get('ControlPID') != '0'):
            raise ControlError('Service is not fully stopped; maintenance marker retained')
        return state

    def enter(self):
        with control_lock(self.root):
            if self.marker.exists():
                raise ControlError('Maintenance already active; inspect status and resume with its token')
            entry = {'token': uuid.uuid4().hex, 'entered_at': time.time(), 'service': SERVICE}
            atomic_private(self.marker, json.dumps(entry))
            # Marker survives interruption/stop failure. Watchdog must not undo it.
            self.command('stop', SERVICE)
            self.require_stopped()
            return entry

    def resume(self, token):
        with control_lock(self.root):
            try:
                entry = json.loads(self.marker.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                raise ControlError('Maintenance marker missing or invalid; no service start attempted') from exc
            if not token or entry.get('token') != token:
                raise ControlError('Maintenance token mismatch; no state changed')
            self.require_stopped()
            self.marker.unlink()
            try:
                self.command('start', SERVICE)
                state = self.state()
                if state.get('ActiveState') != 'active' or int(state.get('MainPID', '0')) <= 0:
                    raise ControlError('Service start did not produce an active main process')
            except (ControlError, ValueError):
                atomic_private(self.marker, json.dumps(entry))
                try:
                    self.command('stop', SERVICE)
                except ControlError:
                    pass
                raise
            return state

    def publication_health(self, now=None, grace=600, acknowledge_oldest=''):
        """Keep Git gates and add content-version evidence for new receipts."""
        receipt_ack = acknowledge_oldest.startswith('receipt:')
        phase_ack = acknowledge_oldest.startswith('phase:')
        review_ack = acknowledge_oldest.startswith('review:')
        input_ack = acknowledge_oldest.startswith('input:')
        git_result = self._git_publication_health(
            now=now, grace=grace,
            acknowledge_oldest='' if receipt_ack or phase_ack or review_ack or input_ack else acknowledge_oldest)
        if (self.root / '.pipeline-progress-worker.json').exists():
            progress = self._phase_publication_health(
                now=now, acknowledge_oldest=acknowledge_oldest if phase_ack else '')
            if phase_ack or (progress.get('alert') and not receipt_ack and not review_ack and not input_ack):
                return progress
        elif phase_ack:
            raise ControlError('Publication phase no longer available; acknowledgement not saved')
        if not (self.root / '.drive_poll_state.json').exists():
            if receipt_ack or review_ack or input_ack:
                raise ControlError('Publication receipt no longer available; acknowledgement not saved')
            return git_result
        from publication_evidence import EvidenceError
        from publication_tracker import check_receipts
        from review_notifications import check_reviews
        try:
            reviews = check_reviews(self.root, now=time.time() if now is None else now,
                                    acknowledge_oldest=acknowledge_oldest if review_ack else '')
            if review_ack or (reviews.get('alert') and not receipt_ack and not input_ack):
                return reviews
            source_observer = None
            if (self.root.parent / 'build_data.py').is_file():
                from input_publication import operating_inputs
                def source_observer():
                    try:
                        return operating_inputs(self.root.parent)
                    except Exception as exc:
                        raise EvidenceError('input_observation_unavailable') from exc
            receipts = check_receipts(
                self.root, Path.home() / 'workspace' / 'x-bookmarks' / 'posts.json',
                now=now, grace=grace,
                acknowledge_oldest=acknowledge_oldest if receipt_ack or input_ack else '',
                input_source=source_observer)
        except EvidenceError as exc:
            if str(exc) in ('tracker_busy', 'local_snapshot_changed') and not receipt_ack and not input_ack and not review_ack:
                return dict(git_result, receipt_observation='deferred_local_change')
            raise ControlError('Publication receipt observation failed; no acknowledgement saved') from exc
        except OSError as exc:
            raise ControlError('Publication receipt storage failed; no acknowledgement saved') from exc
        if receipts.get('alert') or receipt_ack or input_ack:
            return receipts
        return dict(git_result,
                    status=git_result['status'] if git_result.get('pending', 0) else receipts['status'],
                    receipt_counts=receipts.get('counts', {}),
                    tracked_receipts=receipts.get('tracked_receipts', 0),
                    receipt_observation=receipts['status'])

    def _phase_publication_health(self, now=None, acknowledge_oldest=''):
        from pipeline_progress import progress_health
        from publication_evidence import EvidenceError, digest
        try:
            result = progress_health(self.root, now=now)
            marker = self.root / '.worker-phase-alert.json'
            if not result.get('alert'):
                if acknowledge_oldest:
                    raise ControlError('Publication phase changed; acknowledgement not saved')
                marker.unlink(missing_ok=True)
                return result
            key = 'phase:' + digest([result['token'], result['status']])
            result = {k: v for k, v in result.items() if k != 'token'}
            result['oldest'] = key
            try:
                old = json.loads(marker.read_text(encoding='utf-8'))
            except FileNotFoundError:
                old = {}
            if not isinstance(old, dict):
                raise ControlError('Invalid phase acknowledgement state')
            result['alert'] = old.get('oldest') != key
            if acknowledge_oldest:
                if acknowledge_oldest != key:
                    raise ControlError('Publication phase changed; acknowledgement not saved')
                atomic_private(marker, json.dumps({'oldest': key, 'alerted_at': time.time() if now is None else now}))
                result['alert'] = False
            return result
        except (EvidenceError, OSError, ValueError) as exc:
            raise ControlError('Publication phase observation failed; no acknowledgement saved') from exc

    def _git_publication_health(self, now=None, grace=600, acknowledge_oldest=''):
        """Local Git evidence, no network or environment values. Alert once/incident."""
        now = time.time() if now is None else now
        repo = self.root.parent.parent
        def git(*args):
            result = self.runner(['git', '-C', str(repo), *args], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise ControlError('Publication health Git check failed')
            return result.stdout.strip()
        branch = git('symbolic-ref', '--short', 'HEAD')
        if branch != 'main':
            raise ControlError('Publication health requires the operating main branch')
        pending = git('rev-list', '--reverse', 'refs/remotes/origin/main..HEAD').split()
        incident = self.root / '.worker-publication-alert.json'
        if not pending:
            incident.unlink(missing_ok=True)
            return {'status': 'healthy', 'pending': 0, 'alert': False}
        age = max(0, int(now - int(git('show', '-s', '--format=%ct', pending[0]))))
        result = {'status': 'pending' if age < grace else 'publication_stalled',
                  'pending': len(pending), 'age_seconds': age, 'oldest': pending[0], 'alert': False}
        if age >= grace:
            try:
                old = json.loads(incident.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                old = {}
            if old.get('oldest') != pending[0]:
                result['alert'] = True
            if acknowledge_oldest:
                if acknowledge_oldest != pending[0]:
                    raise ControlError('Publication incident changed; acknowledgement not saved')
                atomic_private(incident, json.dumps({'oldest': pending[0], 'alerted_at': now}))
        return result


def write_network_environment(destination, environment):
    """Only the existing working proxy/CA settings; never print their values."""
    values = {key: environment[key] for key in NETWORK_KEYS if environment.get(key)}
    if not any(values.get(key) for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')):
        raise ControlError('No existing proxy configuration found; refusing to install an empty network environment')
    if any(any(ord(char) < 32 or ord(char) == 127 for char in value) for value in values.values()):
        raise ControlError('Network environment contains control characters')
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lines = []
    for key, value in values.items():
        escaped = value.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(key+'="'+escaped+'"')
    atomic_private(path, '\n'.join(lines)+'\n')
    if os.name != 'nt':
        os.chmod(path, 0o600)
    return sorted(values)  # names only


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('enter', 'resume', 'status', 'configure-network', 'publication-health'))
    parser.add_argument('--token', default='')
    parser.add_argument('--acknowledge-oldest', default='')
    args = parser.parse_args()
    control = WorkerControl()
    try:
        if args.action == 'enter': result = control.enter()
        elif args.action == 'resume': result = control.resume(args.token)
        elif args.action == 'configure-network':
            result = {'configured_names':write_network_environment('/home/hatch/.config/archive-worker/network.env', os.environ)}
        elif args.action == 'publication-health': result = control.publication_health(acknowledge_oldest=args.acknowledge_oldest)
        else:
            result = {'maintenance':control.marker.exists(), 'state':control.state()}
        print(json.dumps(result))
        return 0
    except ControlError as exc:
        print(str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
