"""Generic source panels. Game modules declare bindings; each install owns its UI.

No provider selects the active game, installs callbacks into browser drawing, or
shares mutable selections with another install. Network reads remain dataset calls.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from .. import host as host_port
from ..bridge import session, pythonnet_bridge, cabmap_state
from . import command, layout, state, view

STATE = "ruri_remote_sources"
PAGE_SIZE = 20
VERSION_PAGE_SIZE = 12
_request_lock = threading.Lock()
RESULTS_GRID = layout.declare_thumbnail_grid("online_sources", __name__)


def panels():
    """Source controls live on the existing install sessions, not a second registry."""
    return tuple(one.source_ui for one in cabmap_state.SESSIONS.values() if one.source_ui is not None)

def _format_bytes(num):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(num) < 1024.0:
            return f'{num:3.1f} {unit}'
        num /= 1024.0
    return f'{num:.1f} TB'

def _rows(dataset, cancellation, **args):
    table = session.table(dataset, cancellation=cancellation.token, **args)
    try:
        return [table.row(index) for index in range(table.row_count)]
    finally:
        table.close()

def _json(dataset, cancellation, payload=None, **args):
    encoded = None if payload is None else json.dumps(payload).encode('utf-8')
    try:
        return json.loads(session.blob(dataset, payload=encoded, cancellation=cancellation.token, **args))
    finally:
        if payload is not None:
            payload.clear()
        encoded = None

def _flag(value):
    """Boolean numeric columns use the dataset's declared zero/one values."""
    return value == 1

def _downloadable(row):
    return _flag(row.get('downloadable', False))

def _metadata(value):
    return dict(value) if isinstance(value, dict) else json.loads(value) if value else {}

def _arg(key, kind=state.STRING, default=''):
    return state.Field(key, kind, default)

def _version_caption(version, platforms):
    platform = next((row.get('label', row['id']) for row in platforms if row['id'] == version.get('platform')), version.get('platform', 'UNKNOWN'))
    return '{0}  v{1}'.format(platform, version.get('version', '?'))


class SourcePanel:
    def __init__(self, declaration, panel, config):
        from . import browser
        self.declaration = declaration
        self.panel = panel
        self.owner = (config.key, config.decoder_id, browser.install_root_key(config.game_root))
        self._closed = False
        self.values = {}
        self._receipt = {}
        self._local_view = view.Bound('source:' + str(id(self)), search='local_search',
                                      index='local_index', rules='')
        self._local_refresh_scheduled = False
        self._local_map_rows = object()
        self._default_directory = None
        self._sources = []
        self._source = ''
        self._platforms = []
        self._platform = ''
        self._session = {}
        self._method = ''
        self._results = []
        self._versions = []
        self._selected = None
        self._world = ''
        self._offset = 0
        self._jobs = []
        self._ready_packages = {}
        self._pending_downloads = {}
        self._last_ready_package = ''
        self._last_download_job_id = ''
        self._message = 'Connecting to saved session...'
        self._busy = False
        self._reads_in_flight = 0
        self._preview_reads = {}
        self._foreground = False
        self._thumbnail_cancel = None
        self._thumbnail_interrupted = False
        self._cancel = None
        self._epoch = 0
        self._poll_generation = 0
        self._poll_scheduled = False
        self._last_error = {}
        self._opened = False
        self._opening_scheduled = False
        self._open_generation = 0
        self._account_expanded = False
        self._preview_keys = []
        self._thumbnail_queue = []
        self._thumbnail_scheduled = False
        self._thumbnail_fetching = False
        self._page_generation = 0
        self._version_page = 0

    def get_download_dir(self, context=None):
        bag = self.inputs(context)
        custom = bag.download_dir.strip()
        # Empty means the source resolves its own default from this profile's root.
        return host_port.current().absolute_path(custom) if custom else ''

    def display_download_dir(self):
        """A rendered answer never becomes an explicit directory override."""
        return self.get_download_dir() or (self._default_directory or {}).get('path', '')

    def draw_download_dir(self, body, bag):
        body.prop(bag, 'download_dir')
        if not self.get_download_dir() and self._default_directory is not None:
            error = self._default_directory.get('error_message')
            if error:
                body.label(text=error, icon='ERROR')

    def _consume_download(self, context, package):
        if not self.declaration.import_on_download or not self.owns(context):
            return
        if package.get('generation') != self._epoch:
            return
        try:
            yield from self._open_and_import(context, package, JOBS.id)
        except Exception as error:
            self._message = 'Import stopped: ' + str(error)
        finally:
            host_port.current().redraw()

    def inputs(self, context=None):
        return Inputs(self)

    def _invalidate(self):
        self._epoch += 1
        self._clear_previews()
        self._results, self._versions, self._selected, self._world, self._offset = ([], [], None, '', 0)

    def _clear_previews(self):
        if host_port.supports(host_port.ThumbnailPreviews):
            host_port.current().release_thumbnails(tuple(self._preview_keys))
        self._preview_keys, self._thumbnail_queue, self._thumbnail_scheduled = ([], [], False)
        self._page_generation += 1

    def request_open(self, force=False):
        """Drawing requests scheduling only. One attempt per lifecycle, no retry storm."""
        if not host_port.supports(host_port.CommandDispatch):
            return
        if force:
            self._opened = False
        if self._opened or self._opening_scheduled:
            return
        self._opening_scheduled = True
        stamp = self._open_generation

        def start():
            if stamp != self._open_generation:
                return None
            if not self.owns():
                self._opening_scheduled = False
                return None
            if not self.reader_idle(None):
                return 0.2
            self._invoke(OPEN)
            return None
        host_port.current().schedule(0.05, start)

    def _open(self, context, _args):
        self._opened = True
        self._opening_scheduled = False
        if not self._sources:
            yield from self._discover(context, {})
        if not self._sources:
            return
        if not self._source:
            chosen = next((row for row in self._sources if _flag(row['enabled'])), self._sources[0])
            yield from self._choose(context, {'source': chosen['id']})
            return
        descriptor = self._descriptor()
        snapshot = yield from self._read(lambda token: _rows(descriptor['session'], token))
        if snapshot:
            if self._session.get('user') != snapshot[0].get('user'):
                self._invalidate()
            self._session = snapshot[0]
        if self._source and self._session.get('state') != 'authenticated':
            yield from self._auth(context, {'action': 'resume'})
        if self._results and not self._preview_keys:
            self._prepare_thumbnails()

    def _descriptor(self):
        return next((row for row in self._sources if row['id'] == self._source), {})

    def _read(self, fn, quiet=False, background=False, preview=False):
        """One cancellable read; exceptions are reduced before host error logging.

        Transport diagnostics can contain request material. The bridge projects
        only errors whose public type explicitly promises sanitized fields.
        """
        if not self.owns() or (self._busy and not background):
            return None
        stamp = self._epoch
        root = host_port.current().absolute_path(self.owner[2])
        from . import browser
        options = browser.source_options(browser.active_config(self.panel))
        token = pythonnet_bridge.Cancellation()
        done = threading.Event() if preview else None
        if preview:
            self._thumbnail_cancel = token
            self._preview_reads[token] = done
        elif not background and self._thumbnail_cancel is not None:
            self._thumbnail_cancel.cancel()
        self._reads_in_flight += 1
        if not background:
            self._busy, self._cancel = (True, token)
            host_port.current().redraw()
        directory = None

        def pin():
            nonlocal directory
            try:
                with _request_lock:
                    if token.cancelled:
                        return (False, {'error_code': 'cancelled', 'error_message': 'Request cancelled.', 'retry_after': -1})
                    bridge = session.ensure_session()
                    bridge.select_source_install(self.owner[0], root, self.owner[1], options)
                    if self._default_directory is None and self.declaration.directory_dataset:
                        try:
                            rows = _rows(self.declaration.directory_dataset, token, path='', root=root)
                            directory = {'path': str(rows[0]['path'])}
                        except Exception as error:
                            if token.cancelled:
                                raise
                            # A display error must not prevent an independent sign-in action.
                            directory = pythonnet_bridge.source_error(error)
                    return (True, None)
            except Exception as exc:
                return (False, pythonnet_bridge.source_error(exc))

        def work():
            try:
                with _request_lock:
                    return (True, fn(token))
            except Exception as exc:
                return (False, pythonnet_bridge.source_error(exc))
            finally:
                if done is not None:
                    done.set()
        try:
            # Only the short profile transition excludes other UI commands. The
            # independent transport runs after this lease has been released.
            if background:
                # Thumbnails and transfer receipts are reader-independent. They
                # never select an install or change its options/map/decoder.
                pinned, failure = True, None
            elif command.exclusive_active():
                pinned, failure = yield command.Read(pin, cancel=token.cancel)
            else:
                with command.exclusive('ruri.source_pin', allowed=(STOP.id,)):
                    pinned, failure = yield command.Read(pin, cancel=token.cancel)
            if stamp != self._epoch or not self.owns():
                return None
            if not pinned:
                self._apply_error(failure)
                return None
            if directory is not None:
                self._default_directory = directory
            ok, answer = (yield command.Read(work, cancel=token.cancel))
            if stamp != self._epoch or not self.owns():
                close = getattr(answer, 'close', None)
                if ok and close is not None:
                    close()
                return None
            if not ok:
                if stamp == self._epoch and (not quiet or answer.get('error_code') in ('expired', 'signed_out')):
                    self._apply_error(answer)
                return None
            return answer
        finally:
            if preview and self._thumbnail_cancel is token:
                self._thumbnail_cancel = None
            if preview:
                self._preview_reads.pop(token, None)
                done.set()
            token.close()
            self._reads_in_flight -= 1
            if not background:
                self._busy, self._cancel = (False, None)
                host_port.current().redraw()

    def _apply_error(self, error):
        self._last_error = dict(error)
        code = error.get('error_code', 'unexpected')
        self._message = '{0}: {1}'.format(code, error.get('error_message', 'Request failed.'))
        retry = int(error.get('retry_after', -1))
        if retry >= 0:
            self._message += ' Retry after {0}s.'.format(retry)
        if code in ('expired', 'signed_out'):
            self._invalidate()
            self._session = dict(error, state='expired' if code == 'expired' else 'signed_out')

    def _discover(self, _context, _args):
        stamp = self._epoch
        answer = (yield from self._read(lambda token: _rows('core.sources', token)))
        if stamp != self._epoch:
            return
        if answer is None:
            self._message = 'Online sources need a reader exposing core.sources. Update the reader DLLs.'
            return
        answer = [row for row in answer if row['id'] == self.declaration.source]
        self._sources = answer
        if not any((row['id'] == self._source for row in answer)):
            self._source = ''
            self._invalidate()
            self._reset_poll()
        self._message = 'Choose a source.' if answer else 'This reader publishes no online sources.'

    def _choose(self, context, args):
        source = args['source']
        if not any((row['id'] == source for row in self._sources)):
            return
        if source == self._source and self._platforms and self._session:
            return
        self._invalidate()
        self._reset_poll()
        self._jobs = []
        self._source, self._platforms, self._platform, self._session, self._method = (source, [], '', {}, '')
        bag = self.inputs(context)
        bag.username = bag.password = bag.code = ''
        descriptor = dict(self._descriptor())
        stamp = self._epoch

        def connect(token):
            _json('core.sources.control', token, {'action': 'enable', 'source': source})
            platforms = _rows(descriptor['platforms'], token) if descriptor.get('platforms') else []
            sessions = _rows(descriptor['session'], token) if descriptor.get('session') else []
            return (platforms, sessions)
        answer = (yield from self._read(connect))
        if answer is None or stamp != self._epoch:
            return
        self._platforms, sessions = answer
        self._session = sessions[0] if sessions else {}
        methods = [part for part in str(self._session.get('methods', '')).split(',') if part]
        self._method = methods[0] if methods else ''
        chosen = next((row for row in self._platforms if _flag(row['default'])), self._platforms[0] if self._platforms else {})
        self._platform = str(chosen.get('id', ''))
        self._message = 'Source ready. Sign in or refresh its session.'
        if self._session.get('state') not in ('authenticated', 'challenge'):
            yield from self._auth(context, {'action': 'resume'})

    def _auth(self, context, args):
        action = args['action']
        descriptor = dict(self._descriptor())
        bag = self.inputs(context)
        previous_user = self._session.get('user', '')
        payload = {'action': action}
        if action == 'login':
            payload.update(username=bag.username, password=bag.password)
        elif action == 'verify':
            payload.update(challenge_id=self._session.get('challenge_id', ''), method=self._method, code=bag.code)
        if action in ('login', 'verify', 'clear'):
            if bag.username:
                bag.username = ''
            bag.password = bag.code = ''
            self._invalidate()
        stamp = self._epoch
        self._session = {'state': 'authenticating'}
        try:
            answer = (yield from self._read(lambda token: _json(descriptor['auth'], token, payload)))
        finally:
            payload.clear()
        if stamp != self._epoch:
            return
        if answer is None:
            self._session = dict(self._last_error, state='error')
            return
        self._session = answer
        if previous_user and previous_user != answer.get('user', '') or answer.get('state') in ('expired', 'signed_out'):
            self._invalidate()
        if answer.get('state') == 'authenticated':
            self._account_expanded = False
        methods = [part for part in str(answer.get('methods', '')).split(',') if part]
        self._method = methods[0] if methods else ''
        self._message = str(answer.get('error_message') or answer.get('state', 'Session updated'))
        if action == 'clear':
            self._reset_poll()
            self._jobs = []
            bag.username = ''

    def _platform_choose(self, _context, args):
        if any((row['id'] == args['platform'] for row in self._platforms)):
            self._platform = args['platform']
            self._invalidate()

    def _method_choose(self, _context, args):
        if args['method'] in str(self._session.get('methods', '')).split(','):
            self._method = args['method']

    def _search(self, context, args):
        bag = self.inputs(context)
        query = bag.query
        offset = max(0, self._offset + args['page'] * PAGE_SIZE) if args['page'] else 0
        self._invalidate()
        descriptor, platform, stamp = (dict(self._descriptor()), self._platform, self._epoch)
        answer = (yield from self._read(lambda token: _rows(descriptor['search'], token, search=query, offset=offset, count=PAGE_SIZE, platform=platform)))
        if answer is None or stamp != self._epoch or bag.query != query:
            return
        self._results, self._offset, self._versions, self._selected, self._world = (answer, offset, [], None, '')
        self._message = '{0} result(s), page {1}'.format(len(answer), offset // PAGE_SIZE + 1)
        self._prepare_thumbnails()
        if answer:
            yield from self._select(context, {'index': 0, 'generation': self._epoch})
        self._arm_thumbnails()

    def _prepare_thumbnails(self):
        self._preview_keys = ['online:{0}:{1}:{2}'.format(id(self), self._page_generation, index) for index in range(len(self._results))]
        if self._descriptor().get('thumbnail') and host_port.supports(host_port.ThumbnailPreviews):
            self._thumbnail_queue = [(key, str(row.get('thumbnail', ''))) for key, row in zip(self._preview_keys, self._results) if row.get('thumbnail')]

    def _arm_thumbnails(self):
        if not self._thumbnail_queue or self._thumbnail_scheduled or (not host_port.supports(host_port.CommandDispatch)):
            return
        stamp, page = (self._epoch, self._page_generation)
        self._thumbnail_scheduled = True

        def next_image():
            if stamp != self._epoch or page != self._page_generation:
                return None
            if not self.owns():
                self._thumbnail_scheduled = False
                return None
            if not self._available(None) or not self.reader_idle(None) or self._thumbnail_fetching:
                return 0.15
            self._thumbnail_scheduled = False
            self._invoke(THUMBNAIL)
            return None
        host_port.current().schedule(0.15, next_image)

    def _thumbnail(self, _context, _args):
        if not self._thumbnail_queue or not self._descriptor().get('thumbnail'):
            return
        key, image = self._thumbnail_queue.pop(0)
        stamp, page, endpoint = (self._epoch, self._page_generation, self._descriptor()['thumbnail'])
        self._thumbnail_fetching = True
        self._thumbnail_interrupted = False
        try:
            encoded = (yield from self._read(lambda token: session.blob(endpoint, image=image, cancellation=token.token), quiet=True, background=True, preview=True))
            if encoded and stamp == self._epoch and (page == self._page_generation):
                host_port.current().publish_thumbnail(key, encoded)
                host_port.current().redraw()
            elif self._thumbnail_interrupted and stamp == self._epoch and page == self._page_generation:
                self._thumbnail_queue.insert(0, (key, image))
            encoded = None
        finally:
            self._thumbnail_fetching = False
        self._arm_thumbnails()

    def _select(self, context, args):
        if args.get('generation', -1) not in (-1, self._epoch):
            return
        index = args['index']
        if not 0 <= index < len(self._results):
            return
        world, platform, stamp = (self._results[index]['world'], self._platform, self._epoch)
        self._versions, self._selected, self._world = ([], None, world)
        descriptor = dict(self._descriptor())
        answer = (yield from self._read(lambda token: _rows(descriptor['versions'], token, world=world, platform=platform)))
        if answer is None or stamp != self._epoch:
            return
        self._versions, self._selected, self._world = (answer, None, world)
        self._version_page = 0
        self._selected = next((dict(row) for row in answer if _downloadable(row)), None)
        self._message = 'Select a published platform/version tuple.'

    def _version_choose(self, _context, args):
        index = args['index']
        if 0 <= index < len(self._versions):
            row = self._versions[index]
            if args.get('world', '') not in ('', self._world):
                return
            if _downloadable(row):
                self._selected = dict(row)

    def _version_turn(self, _context, args):
        pages = max(1, (len(self._versions) + VERSION_PAGE_SIZE - 1) // VERSION_PAGE_SIZE)
        self._version_page = min(max(0, self._version_page + args['page']), pages - 1)

    def _account_toggle(self, _context, _args):
        self._account_expanded = not self._account_expanded

    def _download(self, context, _args):
        row = dict(self._selected or {})
        if not row or not _downloadable(row):
            self._message = 'Choose a published version.'
            return
        descriptor = dict(self._descriptor())
        stamp = self._epoch
        plan_endpoint = descriptor.get('download_plan', '')
        if not plan_endpoint:
            self._message = 'This source does not publish a download plan. Update the reader.'
            return
        directory = self.get_download_dir(context)
        selected = dict(world=row['world'], platform=row['platform'], file=row['file'], version=int(row['version']))
        plan_args = dict(selected, download_dir=directory)
        if self.declaration.import_latest:
            plan_args['latest'] = True
        if self.declaration.in_place:
            plan_args['root'] = host_port.current().absolute_path(self.owner[2])
        self._message = 'Checking the current published package and local cache...'
        plans = (yield from self._read(lambda token: _rows(plan_endpoint, token, **plan_args)))
        if not plans or stamp != self._epoch:
            return
        plan = plans[0]
        destination = str(plan['destination'])
        package = dict(destination=destination, metadata=_metadata(plan.get('metadata')), descriptor=descriptor, generation=stamp)
        if _flag(plan.get('reuse', False)):
            self._last_ready_package = 'existing:' + destination
            self._ready_packages[self._last_ready_package] = package
            self._last_download_job_id = ''
            self._message = 'Verified current cache. Importing...' if self.declaration.import_on_download else 'Verified local package ready. Choose Open to browse it.'
            host_port.current().redraw()
            yield from self._consume_download(context, package)
            return
        kwargs = {key: plan.get(key, value) for key, value in selected.items()}
        kwargs['version'] = int(kwargs['version'])
        kwargs['destination'] = destination
        answer = (yield from self._read(lambda token: _json(descriptor['download_start'], token, **kwargs)))
        if answer is not None and stamp == self._epoch:
            if isinstance(answer, dict) and 'job' in answer:
                self._last_download_job_id = str(answer['job'])
                self._pending_downloads[self._last_download_job_id] = package
            self._message = 'Downloading; import follows while this install stays selected.' if self.declaration.import_on_download else 'Download started. The package opens only when you choose Open.'
            yield from self._jobs_refresh(context, {})

    def _jobs_refresh(self, context, _args):
        stamp = self._poll_generation
        answer = (yield from self._read(lambda token: _rows('core.jobs', token), quiet=True, background=True))
        if answer is not None and stamp == self._poll_generation:
            self._jobs = [job for job in answer if str(job.get('id', '')) in self._pending_downloads
                          or 'job:' + str(job.get('id', '')) in self._ready_packages]
            for job in self._jobs:
                job_id = str(job.get('id', ''))
                if job.get('state') in ('failed', 'cancelled', 'canceled') and job_id in self._pending_downloads:
                    package = self._pending_downloads.pop(job_id)
                    if package.get('generation') == self._epoch:
                        self._message = str(job.get('error_message') or job['state'])
                if job.get('state') == 'succeeded' and job_id in self._pending_downloads:
                    if not self.reader_idle(context):
                        continue
                    package = dict(self._pending_downloads.pop(job_id))
                    package['destination'] = str(job.get('result_path') or package['destination'])
                    metadata = _metadata(job.get('metadata'))
                    if metadata:
                        package['metadata'] = metadata
                    if job.get('sha256'):
                        package['metadata'].setdefault('prepare_args', {})['sha256'] = str(job['sha256'])
                    self._last_ready_package = 'job:' + job_id
                    self._ready_packages[self._last_ready_package] = package
                    yield from self._consume_download(context, package)
            host_port.current().redraw()
            self._arm_poll()

    def reader_idle(self, context=None):
        """No reader work remains, even if a finished preview awaits its UI callback."""
        if not self.owns(context) or any(
                p._reads_in_flight > sum(done.is_set() for done in p._preview_reads.values())
                for p in panels()) or command.exclusive_active():
            return False
        from . import browser
        panel = browser.state_of(context)
        return not bool(getattr(panel, 'loading', False))

    def prepare_package(self, context, endpoint, destination, metadata, owner, source_id=''):
        """Prepare/adopt under a lease, including a completed user-requested import."""
        if not self.reader_idle(context):
            self._message = 'Wait for the current reader operation to finish.'
            return
        from . import browser
        panel = browser.state_of(context)
        with command.exclusive(owner, allowed=(STOP.id,)):
            panel.loading = True
            stamp = self._epoch
            try:
                if not endpoint:

                    def activate(token):
                        sources = _rows('core.sources', token)
                        descriptor = next((one for one in sources if one['id'] == source_id), None)
                        if descriptor is None or not descriptor.get('package_prepare'):
                            raise RuntimeError('This source publishes no package preparation capability.')
                        _json('core.sources.control', token, {'action': 'enable', 'source': source_id})
                        return descriptor['package_prepare']
                    endpoint = (yield from self._read(activate))
                    if not endpoint or stamp != self._epoch:
                        return
                arguments = dict(metadata.get('prepare_args', {}))
                arguments['file'] = destination
                if self.declaration.in_place:
                    arguments['install'] = self.owner[0]
                install_root = host_port.current().absolute_path(self.owner[2])

                def prepare(token):
                    if self.declaration.in_place:
                        session.ensure_session().select_source_install(
                            self.owner[0], install_root, self.owner[1])
                    previous_keys = {str(row['key']) for row in _rows('core.installs', token)}
                    return (previous_keys, _json(endpoint, token, **arguments))
                prepared = (yield from self._read(prepare))
                if not prepared:
                    return
                previous_keys, answer = prepared
                if not answer or not answer.get('key'):
                    return
                existing = next((one for one in panel.games if one.key == str(answer['key'])), None)
                matches = (str(answer.get('decoder', '')) in self.declaration.decoders
                           and (not self.declaration.in_place or str(answer['key']) == self.owner[0])
                           and (existing is None or self.declaration.matches(existing)))
                if stamp != self._epoch or not self.owns(context) or not matches:
                    bridge = session.ensure_session()
                    key = str(answer['key'])
                    if bridge is not None and key not in previous_keys and (key not in bridge.maps_by_key):
                        bridge.close_session(key)
                    if not matches:
                        self._message = 'Prepared package does not belong to this source decoder.'
                    return
                browser.adopt_install(context, str(answer['key']))
                target = _current(context)
                if target is not None:
                    target._receipt = dict(answer)
                    target._message = 'Package indexed in this profile.'
                self._message = 'Package opened.'
                return answer
            finally:
                panel.loading = False
                host_port.current().redraw()

    def _open_package(self, context, args):
        package = self._ready_packages.get(args['package'])
        if package is None:
            return
        endpoint = package['descriptor'].get('package_prepare', '')
        if not endpoint:
            return
        yield from self._open_and_import(context, package, OPEN_PACKAGE.id)

    def _reset_poll(self):
        self._poll_generation += 1
        self._poll_scheduled = False

    def _arm_poll(self):
        if self._poll_scheduled or not self._source or (not host_port.supports(host_port.CommandDispatch)) or (not any((row.get('state') in ('queued', 'running') or (row.get('state') == 'succeeded' and str(row.get('id', '')) in self._pending_downloads) for row in self._jobs))):
            return
        stamp = self._poll_generation
        self._poll_scheduled = True

        def tick():
            if stamp != self._poll_generation:
                return None
            if not self.owns():
                self._poll_scheduled = False
                return None
            if not self._available(None):
                return 0.5
            self._poll_scheduled = False
            self._invoke(JOBS)
            return None
        host_port.current().schedule(0.5, tick)

    def _job_control(self, context, args):
        if not any(str(job['id']) == args['job'] for job in self._jobs):
            return
        yield from self._read(lambda token: _json('core.jobs.control', token, {'action': args['action'], 'id': args['job']}))
        yield from self._jobs_refresh(context, {})

    def _stop(self, context, _args):
        if self._cancel is not None:
            self._cancel.cancel()
        for key in ('username', 'password', 'code'):
            self.values.pop(key, None)
        self._epoch += 1
        self._thumbnail_queue, self._thumbnail_scheduled = ([], False)
        if self._session.get('state') == 'authenticating':
            self._session = {'state': 'signed_out'}

    def _available(self, context):
        if not self.owns(context) or any(p._foreground or p._busy
                                       for p in panels()) or command.exclusive_active():
            return False
        from . import browser
        return not bool(getattr(browser.state_of(context), 'loading', False))

    def draw_sources(self, body, _context):
        form = body.column()
        form.enabled = not self._busy
        form.operator(OPEN.id, text='Retry saved session', icon='FILE_REFRESH')
        form.operator(DISCOVER.id, text='Refresh sources')
        for descriptor in self._sources:
            selected = descriptor['id'] == self._source
            form.operator(CHOOSE.id, text=str(descriptor.get('label', descriptor['id'])), icon='CHECKMARK' if selected else 'NONE', depress=selected).source = descriptor['id']

    def draw_platforms(self, body, _context):
        body.label(text='Platform (choose one)')
        for platform in self._platforms:
            row = body.row()
            row.enabled = not self._busy
            selected = platform['id'] == self._platform
            row.operator(PLATFORM.id, text=str(platform.get('label', platform['id'])), icon='CHECKBOX_HLT' if selected else 'CHECKBOX_DEHLT', depress=selected).platform = platform['id']

    def draw_versions(self, body, _context):
        indices = sorted(range(len(self._versions)), key=lambda index: not _downloadable(self._versions[index]))
        eligible = sum((1 for version in self._versions if _downloadable(version)))
        body.label(text='{0} downloadable / {1} history records'.format(eligible, len(self._versions)))
        start = self._version_page * VERSION_PAGE_SIZE
        for index in indices[start:start + VERSION_PAGE_SIZE]:
            version = self._versions[index]
            available = _downloadable(version)
            row = body.row()
            row.enabled = available and (not self._busy)
            caption = _version_caption(version, self._platforms)
            if not available:
                caption += ' | {0} / {1}'.format(version.get('available', 'UNKNOWN'), version.get('platformEvidence', 'UNKNOWN'))
            button = row.operator(VERSION.id, text=caption, icon='CHECKMARK' if version == self._selected else 'NONE')
            button.index, button.world = (index, self._world)
        pages = max(1, (len(indices) + VERSION_PAGE_SIZE - 1) // VERSION_PAGE_SIZE)
        if pages > 1:
            row = body.row(align=True)
            previous = row.row()
            previous.enabled = self._version_page > 0 and (not self._busy)
            previous.operator(VERSION_PAGE.id, text='', icon='TRIA_LEFT').page = -1
            row.label(text='{0}/{1}'.format(self._version_page + 1, pages))
            following = row.row()
            following.enabled = self._version_page + 1 < pages and (not self._busy)
            following.operator(VERSION_PAGE.id, text='', icon='TRIA_RIGHT').page = 1

    def draw_transfers(self, body, _context):
        form = body.column()
        form.enabled = not self._busy
        form.operator(JOBS.id)
        for job in self._jobs:
            box = form.box()
            received, total = (int(job.get('received', 0)), int(job.get('total', -1)))
            box.label(text='{0}: {1}'.format(job.get('owner', ''), job.get('state', '')))
            box.progress(factor=min(1.0, received / total) if total > 0 else 0.0, text='{0} / {1} bytes'.format(received, total if total >= 0 else '?'))
            if job.get('result_path'):
                box.label(text=str(job['result_path']))
            if job.get('error_code'):
                box.label(text=str(job['error_code']) + ': ' + str(job.get('error_message', '')))
            retry = int(job.get('retry_after', -1))
            if retry >= 0:
                box.label(text='Retry after {0}s'.format(retry))
            active = job.get('state') in ('queued', 'running')
            button = box.operator(JOB.id, text='Cancel' if active else 'Forget')
            button.job, button.action = (str(job['id']), 'cancel' if active else 'forget')
            package_id = 'job:' + str(job['id'])
            if package_id in self._ready_packages:
                box.operator(OPEN_PACKAGE.id, text='Open package', icon='FILE_FOLDER').package = package_id

    def draw(self, body, context):
        if not host_port.supports(host_port.CommandDispatch):
            body.label(text='This host does not expose optional command dispatch.', icon='INFO')
            return
        self.request_open()
        self._arm_poll()
        self._arm_thumbnails()
        bag = host_port.current().panel_state(context, STATE)
        descriptor = self._descriptor()
        header = body.row(align=True)
        header.label(text=str(descriptor.get('label', 'Online Sources')), icon='URL')
        header.popover(SOURCES_PANEL, text='', icon='PREFERENCES')
        active = sum((1 for job in self._jobs if job.get('state') in ('queued', 'running')))
        header.popover(TRANSFERS_PANEL, text='Transfers ({0})'.format(active), icon='IMPORT')
        if self._busy:
            progress = body.row(align=True)
            progress.label(text='Working...', icon='TIME')
            progress.operator(STOP.id, text='', icon='X')
        if self._message:
            body.label(text=self._message)
        if not self._source:
            if self._opened and (not self._busy):
                body.operator(OPEN.id, text='Retry connection', icon='FILE_REFRESH')
            return
        form = body.column()
        form.enabled = not self._busy
        authenticated = self._session.get('state') == 'authenticated'
        auth = form.box()
        if authenticated:
            identity = auth.row(align=True)
            identity.label(text=str(self._session.get('display_name') or self._session.get('user') or 'Signed in'), icon='CHECKMARK')
            identity.operator(ACCOUNT.id, text='', icon='TRIA_DOWN' if self._account_expanded else 'TRIA_RIGHT')
            if self._account_expanded:
                auth.label(text="Saved session protected for this computer's user.")
                auth.label(text='Source session is shared; profiles separate content, not accounts.')
                row = auth.row(align=True)
                row.operator(AUTH.id, text='Refresh session').action = 'refresh'
                row.operator(AUTH.id, text='Sign out and forget').action = 'clear'
        elif self._session.get('state') == 'challenge':
            auth.label(text='Verification required', icon='LOCKED')
            choices = auth.row(align=True)
            for method in str(self._session.get('methods', '')).split(','):
                if method:
                    choices.operator(METHOD.id, text=method, depress=method == self._method).method = method
            auth.prop(bag, 'code')
            auth.operator(AUTH.id, text='Verify').action = 'verify'
            auth.operator(AUTH.id, text='Cancel sign-in').action = 'clear'
        elif self._session.get('state') == 'authenticating' or (self._busy and (not authenticated)):
            auth.label(text='Logging in / restoring saved session...', icon='TIME')
        else:
            auth.label(text='Sign in / restore saved session')
            if str(descriptor.get('auth_mode', 'credentials')) == 'credentials':
                auth.prop(bag, 'username')
                auth.prop(bag, 'password')
                auth.operator(AUTH.id, text='Sign in').action = 'login'
            auth.operator(AUTH.id, text='Retry saved session').action = 'resume'
            auth.label(text='Only session tokens are saved; password/code stay transient.')
        retry = self._session.get('retry_after', -1)
        if retry not in (-1, '-1', None, ''):
            auth.label(text='Retry after: {0}s'.format(retry))
        if not authenticated:
            return
        toolbar = form.row(align=True)
        toolbar.prop(bag, 'query', text='', icon='VIEWZOOM')
        toolbar.operator(SEARCH.id, text='', icon='VIEWZOOM')
        platform = next((row for row in self._platforms if row['id'] == self._platform), {})
        toolbar.popover(PLATFORMS_PANEL, text=str(platform.get('label', 'Platform')), icon='FILTER')
        if self._world:
            chosen = next((row for row in self._results if row.get('world') == self._world), {})
            detail = form.box()
            detail.label(text=str(chosen.get('label', self._world)), icon='WORLD')
            detail.label(text=str(chosen.get('author', '')))
            description = str(chosen.get('description', ''))
            if description:
                detail.label(text=description[:140])
            detail.popover(VERSIONS_PANEL, text=_version_caption(self._selected, self._platforms) if self._selected else 'Choose version', icon='DOWNARROW_HLT')
            if self.declaration.import_latest:
                detail.label(text='Import checks the latest published version of this package.')
            self.draw_download_dir(detail, bag)
            active_job = None
            if self._last_download_job_id:
                active_job = next((j for j in self._jobs if str(j.get('id')) == str(self._last_download_job_id)), None)
            if not active_job:
                active_job = next((j for j in self._jobs if j.get('state') in ('queued', 'running')), None)
            if active_job:
                prog_box = detail.box()
                st = str(active_job.get('state', 'queued'))
                received = int(active_job['received'])
                total = int(active_job['total'])
                factor = min(1.0, max(0.0, received / total)) if total > 0 else 0.0
                pct = f' ({int(factor * 100)}%)' if total > 0 else ''
                prog_text = f'{_format_bytes(received)} / {_format_bytes(total)}{pct}' if total > 0 else f'{_format_bytes(received)} / ?'
                p_row = prog_box.row(align=True)
                p_row.label(text=f'Status: {st.capitalize()}', icon='TIME' if st in ('queued', 'running') else 'CHECKMARK' if st == 'succeeded' else 'ERROR')
                is_active = st in ('queued', 'running')
                btn = p_row.operator(JOB.id, text='Cancel' if is_active else 'Dismiss', icon='CANCEL' if is_active else 'X')
                btn.job, btn.action = (str(active_job.get('id', '')), 'cancel' if is_active else 'forget')
                prog_box.progress(factor=factor, text=prog_text)
                destination = str(active_job.get('result_path') or _metadata(active_job.get('metadata')).get('destination', ''))
                if destination:
                    prog_box.label(text='File: ' + destination, icon='FILE')
                if active_job.get('error_code'):
                    prog_box.label(text='{0}: {1}'.format(active_job['error_code'], active_job['error_message']), icon='ERROR')
            download = detail.row()
            is_job_running = bool(active_job and active_job.get('state') in ('queued', 'running'))
            download.enabled = bool(self._selected) and (not self._busy) and (not is_job_running)
            download.operator(DOWNLOAD.id, text='Downloading...' if is_job_running else
                              'Import' if self.declaration.import_on_download else 'Download', icon='IMPORT')
            if self._last_ready_package in self._ready_packages:
                ready = detail.row()
                ready.enabled = self._available(context)
                ready.operator(OPEN_PACKAGE.id, text='Open downloaded package', icon='FILE_FOLDER').package = self._last_ready_package
        if self._results:
            entries = [dict(key=key, label=str(result.get('label', result.get('world', ''))), detail=str(result.get('author', '')), values={'index': index, 'generation': self._epoch}) for index, (key, result) in enumerate(zip(self._preview_keys, self._results))]
            selected_key = next((key for key, result in zip(self._preview_keys, self._results) if result.get('world') == self._world), '')
            form.thumbnail_grid(RESULTS_GRID, entries, selected_key, SELECT.id, columns=4)
            page = form.row(align=True)
            previous = page.row()
            previous.enabled = self._offset > 0 and (not self._busy)
            previous.operator(SEARCH.id, text='', icon='TRIA_LEFT').page = -1
            page.label(text='Page {0} �� {1} worlds'.format(self._offset // PAGE_SIZE + 1, len(self._results)))
            following = page.row()
            following.enabled = len(self._results) == PAGE_SIZE and (not self._busy)
            following.operator(SEARCH.id, text='', icon='TRIA_RIGHT').page = 1
        else:
            form.label(text='Search to browse covers.', icon='IMAGE_DATA')


    def owns(self, context=None):
        from . import browser
        panel = browser.state_of(context)
        config = browser.active_config(panel)
        return (not self._closed and panel == self.panel and config is not None
                and (config.key, config.decoder_id, browser.install_root_key(config.game_root)) == self.owner
                and self.declaration.matches(config))

    def suspend(self):
        """A tab switch invalidates callbacks without discarding its selection."""
        for token in (self._cancel, self._thumbnail_cancel):
            if token is not None:
                token.cancel()
        self._epoch += 1
        self._open_generation += 1
        self._opening_scheduled = False
        self._opened = False
        self._reset_poll()
        self._clear_previews()

    def close(self):
        self._closed = True
        self._local_view.close()
        if self._local_view.table is not None:
            self._local_view.table.close()
            self._local_view.table = None
        for token in (self._cancel, self._thumbnail_cancel):
            if token is not None:
                token.cancel()
        self.values.clear()
        self._epoch += 1
        self._open_generation += 1
        self._reset_poll()
        self._clear_previews()

    def _invoke(self, operation):
        # A scheduled callback belongs to its originating install, not the current tab.
        if self.owns():
            host_port.current().invoke_command(operation.id, {'owner': str(id(self))})

    def _open_and_import(self, context, package, owner, reset_scene=False):
        from . import browser
        stamp = self._epoch
        answer = yield from self.prepare_package(context,
            package['descriptor'].get('package_prepare', ''), package['destination'],
            package['metadata'], owner, source_id=self.declaration.source)
        if not answer or not answer.get('seed') or stamp != self._epoch or not self.owns(context):
            return
        panel = browser.state_of(context)
        config = browser.active_config(panel)
        if (panel != self.panel or config is None or config.key != str(answer['key'])
                or config.decoder_id not in self.declaration.decoders):
            return
        if self.declaration.local_dataset:
            yield from self._local_refresh(context, {})
            if stamp != self._epoch or not self.owns(context):
                return
            bag = host_port.current().panel_state(context, STATE)
            if self._local_view.view is not None:
                # The receipt supplies the canonical source identity, not a Python seed convention.
                bag.local_index = self._local_view.at(str(answer.get('source_path', '')))
            if self.declaration.local_tab:
                panel.active_tab = self.declaration.local_tab
        # All materialisation goes through the existing load command. A declaration
        # can request temporary options; unrelated installs keep their defaults.
        yield from self._load_seed(context, answer['seed'], owner, reset_scene)

    def _load_seed(self, context, seed, owner, reset_scene):
        from . import browser
        panel = browser.state_of(context)
        with command.exclusive(owner, allowed=(STOP.id, browser.LOAD.id)):
            previous = {key: getattr(panel, key) for key, _value in self.declaration.import_options}
            try:
                for key, value in self.declaration.import_options:
                    setattr(panel, key, value)
                yield from browser.LOAD.run(context, {'seeds': seed, 'reset_scene': reset_scene})
            finally:
                for key, value in previous.items():
                    setattr(panel, key, value)
        self._message = str(panel.status)
        target = _current(context)
        if target is not None:
            target._message = self._message

    def _local_refresh(self, context, _args):
        if self.declaration.local_dataset:
            self._local_map_rows = cabmap_state.ROWS
            root = host_port.current().absolute_path(self.owner[2])
            directory = self.get_download_dir(context)
            def read(token):
                session.ensure_session().select_source_install(self.owner[0], root, self.owner[1])
                return session.table(self.declaration.local_dataset, cancellation=token.token,
                                     path=directory, root=root)
            with command.exclusive(LOCAL_REFRESH.id, allowed=(STOP.id,)):
                table = yield from self._read(read)
            if table is not None:
                if not self.owns(context):
                    table.close()
                    return
                old = self._local_view.table
                self._local_view.open(table, host_port.current().panel_state(context, STATE))
                if old is not None:
                    old.close()

    def _local_open(self, context, args):
        if self.declaration.local_dataset:
            if self._local_map_rows is not cabmap_state.ROWS:
                self._message = 'Refresh this profile before importing its selection.'
                return
            bag = host_port.current().panel_state(context, STATE)
            seed = self._local_view.payload(bag)
            if seed:
                yield from self._load_seed(context, seed, LOCAL_OPEN.id, bag.reset_scene)
            else:
                path = self._local_view.value(bag, 'path')
                if path:
                    yield from self._open_and_import(context, dict(descriptor={},
                        destination=path, metadata={}), LOCAL_OPEN.id, bag.reset_scene)

    def draw_local(self, body, context):
        if self.declaration.local_dataset:
            self._arm_poll()
            from . import browser
            bag = host_port.current().panel_state(context, STATE)
            if self._local_map_rows is not cabmap_state.ROWS and not self._local_refresh_scheduled:
                self._local_refresh_scheduled = True
                def refresh():
                    if not self.owns():
                        self._local_refresh_scheduled = False
                        return None
                    if not self.reader_idle():
                        return 0.5
                    self._local_refresh_scheduled = False
                    self._invoke(LOCAL_REFRESH)
                    return None
                host_port.current().schedule(0.01, refresh)
            form = body.column()
            form.enabled = self._available(context) and self._local_map_rows is cabmap_state.ROWS
            self.draw_download_dir(form, bag)
            view.draw_head(self._local_view, form, bag, LOCAL_REFRESH.id)
            view.draw_list(self._local_view, form, bag,
                [self._local_view.role_column(view.LABEL, icon='WORLD'),
                 self._local_view.role_column(view.DETAIL, align='RIGHT')],
                'source_worlds', rows=10)
            form.label(text=self._message)
            browser.draw_import_options(form, context)
            form.prop(bag, 'reset_scene')
            seed = self._local_view.payload(bag)
            action = form.row()
            action.enabled = bool(seed or self._local_view.value(bag, 'path'))
            # Ownership is checked again on click, then forwarded to the native load command.
            action.operator(LOCAL_OPEN.id, text='Import', icon='IMPORT')


@dataclass(frozen=True)
class Panel:
    """Only UI bindings: owner, eligible decoders, datasets and requested controls.

    The game contributes this value to GameTab; it never edits browser drawing.
    Shared execution and state lifetime belong to the frontend.
    """
    source: str
    product: str
    decoders: tuple
    local_dataset: str = ''
    import_on_download: bool = False
    import_options: tuple = ()
    in_place: bool = False
    import_latest: bool = False
    local_tab: str = ''
    directory_dataset: str = ''

    def matches(self, config):
        return (config.decoder_id in self.decoders
                and (not config.game_name or config.game_name.casefold() == self.product.casefold()))

    def __call__(self, body, context):
        panel = _current(context, declaration=self)
        if panel is not None:
            panel.draw(OwnedLayout(body, panel), context)

    def local(self, body, context):
        panel = _current(context, declaration=self)
        if panel is not None:
            panel.draw_local(OwnedLayout(body, panel), context)


class OwnedLayout:
    """Stamp the originating instance onto every generated command button."""
    def __init__(self, body, panel):
        object.__setattr__(self, 'body', body)
        object.__setattr__(self, 'panel', panel)

    def __getattr__(self, name):
        value = getattr(self.body, name)
        if name in ('row', 'column', 'box', 'split'):
            return lambda *args, **kwargs: OwnedLayout(value(*args, **kwargs), self.panel)
        return value

    def __setattr__(self, name, value):
        setattr(self.body, name, value)

    def operator(self, operation, **kwargs):
        button = self.body.operator(operation, **kwargs)
        if operation.startswith('ruri.source_'):
            button.owner = str(id(self.panel))
        return button

    def thumbnail_grid(self, grid, entries, selected, operation, **kwargs):
        owned = [dict(entry, values=dict(entry['values'], owner=str(id(self.panel)))) for entry in entries]
        return self.body.thumbnail_grid(grid, owned, selected, operation, **kwargs)


def _current(context=None, declaration=None):
    from . import browser
    from ... import Game
    panel = browser.state_of(context)
    config = browser.active_config(panel)
    if config is None:
        return None
    declarations = [game.source_ui for game in Game.modules()
                    if isinstance(game.source_ui, Panel) and game.source_ui.matches(config)]
    if declaration is None:
        declaration = next(iter(declarations), None)
    if declaration not in declarations:
        return None
    install = cabmap_state.session_for(config.key)
    cached = install.source_ui
    owner = (config.key, config.decoder_id, browser.install_root_key(config.game_root))
    if cached is not None and (cached.panel != panel or cached.owner != owner or cached.declaration != declaration):
        cached.close()
        cached = None
    if cached is None:
        cached = SourcePanel(declaration, panel, config)
        install.source_ui = cached
    return cached


class Inputs:
    """Per-instance values used by running operations, including after a tab switch."""
    def __init__(self, panel):
        object.__setattr__(self, 'panel', panel)

    def __getattr__(self, key):
        return self.panel.values.get(key, '')

    def __setattr__(self, key, value):
        self.panel.values[key] = value
        if key == 'username':
            self.panel._invalidate()
        elif key == 'download_dir':
            self.panel._local_map_rows = object()


def _get_input(key):
    def get(_bag):
        panel = _current()
        if key == 'download_dir' and panel is not None:
            return panel.display_download_dir()
        return panel.values.get(key, '') if panel is not None else ''
    return get


def _set_input(key):
    def set_value(_bag, value):
        panel = _current()
        if panel is not None:
            if key == 'download_dir' and value == panel.display_download_dir():
                # A directory picker/editor can write its unchanged displayed value
                # on focus loss. That is not an explicit override of the source default.
                return
            setattr(Inputs(panel), key, value)
            if key == 'local_search' and panel._local_view.table is not None:
                panel._local_view.open(panel._local_view.table,
                    host_port.current().panel_state(None, STATE))
    return set_value


_input_fields = (('username', 'Username'), ('password', 'Password'),
                 ('code', 'Verification code'), ('query', 'Search'),
                 ('download_dir', 'Download folder'), ('local_search', 'Search worlds'))
SCHEMA = state.Schema('remote_sources', 'Per-install transient source inputs', tuple(
    state.Field(key, state.STRING, '', label=label, transient=True,
                sensitive=key in ('password', 'code'),
                subtype=state.DIRECTORY if key == 'download_dir' else '',
                getter='get_' + key, setter='set_' + key) for key, label in _input_fields) + (
    state.Field('rows', state.COLLECTION, element=view.VIEW_ROW),
    state.Field('local_index', state.INT, -1, getter='get_local_index', setter='set_local_index', transient=True),
    state.Field('reset_scene', state.BOOL, False, label='Reset Scene', transient=True),
))
HANDLERS = state.Handlers('source inputs',
    get_local_index=lambda _bag: int(_current().values.get('local_index', -1)) if _current() else -1,
    set_local_index=_set_input('local_index'), **{
    name: callback for key, _label in _input_fields
    for name, callback in (('get_' + key, _get_input(key)), ('set_' + key, _set_input(key)))})


def _define(name, label, method, arguments=(), steps=True, preview=False, background=False):
    def idle():
        # Stepped host drivers use send(), including when rejecting a stale button.
        yield from ()

    def poll(context):
        panel = _current(context)
        return panel is not None and panel._available(context) and (
            not preview or (not panel._thumbnail_fetching and panel.reader_idle(context)))

    def run(context, args):
        panel = _current(context)
        # Poll alone is not a gate: modal execution and scripted invocation can
        # arrive after the user has already switched the install/decoder.
        if panel is None or (args['owner'] and args['owner'] != str(id(panel))):
            return idle() if steps else None
        if not poll(context):
            return idle() if steps else None
        if not steps:
            return getattr(panel, method)(context, args)
        def start():
            # Recheck after a modal invoke, before the first timer tick.
            if not poll(context):
                return
            if preview or background:
                yield from getattr(panel, method)(context, args)
                return
            panel._foreground = True
            stamp = panel._epoch
            try:
                pending = [(one, token, done) for one in panels()
                           for token, done in tuple(one._preview_reads.items())]
                if pending:
                    # User actions outrank covers, but map preparation still waits
                    # for the actual worker exit, not merely a cancellation request.
                    with command.exclusive('ruri.source_' + name, allowed=(STOP.id,)):
                        for one, token, done in pending:
                            one._thumbnail_interrupted = True
                            token.cancel()
                        def drain():
                            for _, _, done in pending:
                                done.wait()
                        yield command.Read(drain)
                if panel.owns(context) and stamp == panel._epoch:
                    yield from getattr(panel, method)(context, args)
            finally:
                panel._foreground = False
                panel._arm_thumbnails()
                host_port.current().redraw()
        return start()

    return command.COMMANDS.define('ruri.source_' + name, label, run,
        arguments=tuple(arguments) + (_arg('owner'),), steps=steps, internal=True,
        poll=poll, cancel_message='Source request cancelled.', requires=host_port.CommandDispatch)


DISCOVER = _define('discover', 'Discover sources', '_discover')
OPEN = _define('open', 'Connect saved session', '_open')
CHOOSE = _define('choose', 'Connect', '_choose', (_arg('source'),))
AUTH = _define('auth', 'Session', '_auth', (_arg('action'),))
PLATFORM = _define('platform', 'Platform', '_platform_choose', (_arg('platform'),), False)
METHOD = _define('method', 'Method', '_method_choose', (_arg('method'),), False)
SEARCH = _define('search', 'Search', '_search', (_arg('page', state.INT, 0),))
SELECT = _define('select', 'Select result', '_select', (_arg('index', state.INT, 0), _arg('generation', state.INT, -1)))
VERSION = _define('version', 'Version', '_version_choose', (_arg('index', state.INT, 0), _arg('world')), False)
VERSION_PAGE = _define('version_page', 'Version page', '_version_turn', (_arg('page', state.INT, 0),), False)
ACCOUNT = _define('account', 'Account details', '_account_toggle', steps=False)
THUMBNAIL = _define('thumbnail', 'Loading cover', '_thumbnail', preview=True)
DOWNLOAD = _define('download', 'Download selected version', '_download')
OPEN_PACKAGE = _define('open_package', 'Import package', '_open_package', (_arg('package'),))
JOBS = _define('jobs', 'Refresh transfers', '_jobs_refresh', background=True)
JOB = _define('job', 'Transfer', '_job_control', (_arg('job'), _arg('action')))
LOCAL_REFRESH = _define('local_refresh', 'Refresh local packages', '_local_refresh')
LOCAL_OPEN = _define('local_open', 'Import local package', '_local_open')


def _stop(context, args):
    panel = _current(context)
    if panel is not None and (not args['owner'] or args['owner'] == str(id(panel))):
        panel._stop(context, args)


STOP = command.COMMANDS.define('ruri.source_stop', 'Cancel current request', _stop,
    internal=True, arguments=(_arg('owner'),))


def _surface(method):
    def draw(body, context):
        panel = _current(context)
        if panel is not None:
            getattr(panel, method)(OwnedLayout(body, panel), context)
    return draw


SOURCES_PANEL = layout.declare_popover('RURI_PT_source_settings', 'Source Settings', _surface('draw_sources'), width=20)
PLATFORMS_PANEL = layout.declare_popover('RURI_PT_source_platforms', 'Platform', _surface('draw_platforms'), width=16)
VERSIONS_PANEL = layout.declare_popover('RURI_PT_source_versions', 'Versions', _surface('draw_versions'), width=24)
TRANSFERS_PANEL = layout.declare_popover('RURI_PT_source_transfers', 'Transfers', _surface('draw_transfers'), width=28)


def register():
    host_port.current().register_state(STATE, SCHEMA, HANDLERS)


def unregister():
    for install in cabmap_state.SESSIONS.values():
        if install.source_ui is not None:
            install.source_ui.close()
            install.source_ui = None
    host_port.current().unregister_state(STATE)
