"""Report and test only the explicitly paired satellite, using public HA APIs."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from homeassistant.components import assist_pipeline
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval
from .const import DOMAIN

VERSION = '0.2.0'

class SatelliteConnection:
    def __init__(self, hass, entry, client):
        self.hass, self.entry, self.client = hass, entry, client
        self.events = []
        self.test_id = None
        self.watcher = None
        self.initial = None
        self.cancel_interval = None
        self.lock = asyncio.Lock()
        self.closed = False

    def satellite(self):
        registry = er.async_get(self.hass)
        entries = [r for r in er.async_entries_for_device(registry, self.client.microphone)
            if r.domain == 'assist_satellite' and not r.disabled_by]
        component = self.hass.data.get('assist_satellite')
        return component.get_entity(entries[0].entity_id) if component and len(entries) == 1 else None

    def pipeline_ready(self):
        pipeline = next((p for p in assist_pipeline.async_get_pipelines(self.hass)
            if p.id == self.entry.data.get('pipeline_id')), None)
        if not pipeline or pipeline.prefer_local_intents:
            return False
        registry = er.async_get(self.hass)
        for kind in ('stt','conversation','tts'):
            expected = registry.async_get_entity_id(kind, DOMAIN, self.client.endpoint + '_' + kind)
            if not expected or getattr(pipeline, kind + '_engine') != expected:
                return False
        states = [self.hass.states.get(r.entity_id) for r in er.async_entries_for_device(registry, self.client.microphone)
            if r.domain == 'select' and not r.disabled_by]
        candidates = [s for s in states if s and pipeline.name in s.attributes.get('options', [])]
        return len(candidates) == 1 and candidates[0].state == pipeline.name

    def runtime(self):
        satellite = self.satellite()
        words, active, supported = [], None, False
        if satellite and satellite.available:
            try:
                config = satellite.async_get_configuration()
                words = [{'id':w.id,'wake_word':w.wake_word} for w in config.available_wake_words][:16]
                active = config.active_wake_words[0] if len(config.active_wake_words) == 1 else None
                supported = callable(getattr(satellite, 'async_intercept_wake_word', None))
            except (NotImplementedError, AttributeError, ValueError):
                pass
        return {'version':VERSION,'pipeline_ready':self.pipeline_ready(),
            'test_supported':supported,'wake_words':words,'active_wake_word':active}

    async def select_word(self, word):
        satellite = self.satellite()
        if not satellite or not satellite.available:
            return False
        config = satellite.async_get_configuration()
        # A label, remote URL or uninstalled Hey Belle model can never become a model ID.
        if word not in {w.id for w in config.available_wake_words}:
            return False
        if config.active_wake_words != [word]:
            await satellite.async_set_configuration(replace(config, active_wake_words=[word]))
        return True

    async def poll(self, _now=None):
        if self.closed or self.lock.locked():
            return
        async with self.lock:
            event = self.events[0] if self.events else None
            try:
                response = await self.client.sync(self.runtime(), event)
            except Exception:
                # No audio, tokens, transcripts or provider responses in logs.
                return
            if event and self.events and self.events[0] is event:
                self.events.pop(0)
            test = response.get('test')
            if self.watcher and not self.watcher.done() and (not test or test.get('id') != self.test_id):
                self.watcher.cancel()
            word = response.get('wake_word')
            if word and not test:
                try:
                    await self.select_word(word)
                except Exception:
                    pass
            if test and test.get('status') == 'waiting' and test.get('id') != self.test_id:
                self.test_id = test['id']
                self.watcher = asyncio.create_task(self.watch(test))

    async def watch(self, test):
        intercept = None
        try:
            if not self.pipeline_ready():
                self.events.append({'test_id':test['id'],'type':'failed','error':'pipeline_mismatch'})
                return
            satellite = self.satellite()
            if not satellite or not satellite.available:
                self.events.append({'test_id':test['id'],'type':'failed','error':'satellite_unavailable'})
                return
            # This consumes the FIRST physical wake. The app then asks for a
            # second wake + 'test Belle', which follows the real Belle pipeline.
            intercept = asyncio.create_task(satellite.async_intercept_wake_word())
            await asyncio.sleep(0)
            if intercept.done() and intercept.exception():
                raise intercept.exception()
            self.events.append({'test_id':test['id'],'type':'armed'})
            await self.poll()
            word = await asyncio.wait_for(intercept, timeout=45)
            if not self.pipeline_ready():
                self.events.append({'test_id':test['id'],'type':'failed','error':'pipeline_mismatch'})
            elif isinstance(word, str) and word:
                self.events.append({'test_id':test['id'],'type':'wake','wake_word':word[:80]})
            else:
                self.events.append({'test_id':test['id'],'type':'failed','error':'wake_timeout'})
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self.events.append({'test_id':test['id'],'type':'failed','error':'wake_timeout'})
        except Exception:
            self.events.append({'test_id':test['id'],'type':'failed','error':'satellite_unavailable'})
        finally:
            if intercept and not intercept.done():
                intercept.cancel()
            if not self.closed:
                await self.poll()

    def start(self):
        self.cancel_interval = async_track_time_interval(self.hass, self.poll, timedelta(seconds=8))
        self.initial = asyncio.create_task(self.poll())

    async def close(self):
        self.closed = True
        if self.cancel_interval:
            self.cancel_interval()
        tasks = [t for t in (self.initial,self.watcher) if t and not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.events.clear()
