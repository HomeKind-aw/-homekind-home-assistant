import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'homekind_bridge'))
from voice_events import VoiceOutbox, VoiceError, phrase_intent, validate_event, receipt_status, event_from_ha, FEEDBACK


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1792224000.0  # 2026-10-17T08:00:00Z
        self.bridge, self.installation, self.endpoint, self.binding_id = [str(uuid4()) for _ in range(4)]
        self.binding = dict(protocol=1, bridge_id=self.bridge, installation_id=self.installation,
                            endpoint_id=self.endpoint, binding_id=self.binding_id, revision=1,
                            enabled=True, muted=False, consent_current=True,
                            expires_at='2026-10-17T08:01:00Z', intents=['help', 'room_lights_on', 'checkin_okay'])
        self.event = dict(protocol=1, event_id=str(uuid4()), endpoint_id=self.endpoint,
                          binding_id=self.binding_id, intent='help', occurred_at='2026-10-17T08:00:00Z')
        self.box = self.open_box()

    def open_box(self):
        return VoiceOutbox(self.temp.name, self.bridge, self.installation, clock=lambda: self.now)

    def tearDown(self):
        self.box.close()
        self.temp.cleanup()

    def receipt(self, event=None, revision=1, status='queued'):
        return dict(protocol=1, event_id=(event or self.event)['event_id'], binding_id=self.binding_id,
                    binding_revision=revision, status=status)

    def enqueue(self, event=None, binding=None):
        return self.box.enqueue(event or self.event, binding or self.binding)

    def send(self, fn=None):
        return self.box.exchange_one(fn or (lambda event, revision: self.receipt(event, revision)), lambda _: self.binding)

    def test_exact_local_grammar_rejects_negation_compounds_and_wake_word(self):
        for text, intent in [('I need a hand', 'help'), (' turn ON the lights ', 'room_lights_on'),
                             ("I'm okay", 'checkin_okay'), ('I’m okay', 'checkin_okay'), ('I am okay', 'checkin_okay')]:
            self.assertEqual(phrase_intent(text), intent)
        for text in ['I do not need a hand', 'I need a hand and turn on the lights', 'Hey HomeKind I need a hand',
                     'I am not okay', 'turn off the lights', 'someone said I need a hand', 'I need a hand\x00', None, ['help']]:
            self.assertIsNone(phrase_intent(text))

    def test_local_event_projection_is_stable_and_cannot_carry_speech_or_speaker_authority(self):
        local = {'event_type': 'homekind_voice_request', 'time_fired': self.event['occurred_at'],
                 'context': {'id': '01K7TCRZG20000000000000000', 'user_id': str(uuid4())},
                 'data': {k: self.event[k] for k in ['protocol', 'endpoint_id', 'binding_id', 'intent']}}
        projected = event_from_ha(local, self.now)
        self.assertEqual(projected, event_from_ha(local, self.now))
        self.assertNotIn('user_id', projected)
        self.assertEqual(set(projected), set(self.event))
        self.assertEqual(self.enqueue(projected), 'recognised')
        local['data']['intent'] = 'room_lights_on'
        with self.assertRaises(VoiceError): self.enqueue(event_from_ha(local, self.now))
        local['data']['transcript'] = 'private'
        with self.assertRaises(VoiceError): event_from_ha(local, self.now)

    def test_transport_excludes_text_authority_and_unknown_fields(self):
        for key, value in [('transcript', 'private speech'), ('actor_id', str(uuid4())), ('recipients', []),
                           ('service', 'lock.unlock'), ('url', 'https://example.test'), ('audio', 'bytes')]:
            with self.subTest(key=key), self.assertRaises(VoiceError):
                self.enqueue({**self.event, key: value})
        for change in [{'protocol': True}, {'protocol': 2}, {'intent': []}, {'event_id': 'bad'},
                       {'intent': 'unlock'}, {'occurred_at': '2026-02-30T08:00:00Z'}, {'occurred_at': '2026-10-17T08:00:00+00:00'}]:
            with self.subTest(change=change), self.assertRaises(VoiceError):
                self.enqueue({**self.event, **change})

    def test_stale_future_and_original_time_boundaries(self):
        for stamp in ['2026-10-17T07:59:29Z', '2026-10-17T08:00:06Z']:
            with self.assertRaises(VoiceError): self.enqueue({**self.event, 'occurred_at': stamp})
        self.assertEqual(validate_event({**self.event, 'occurred_at': '2026-10-17T07:59:30Z'}, self.now)['intent'], 'help')

    def test_unbound_muted_revoked_expired_and_changed_generation_rejected(self):
        for change in [{'enabled': False}, {'muted': True}, {'consent_current': False}, {'bridge_id': str(uuid4())},
                       {'installation_id': str(uuid4())}, {'endpoint_id': str(uuid4())}, {'binding_id': str(uuid4())},
                       {'expires_at': '2026-10-17T08:00:00Z'}, {'revision': True}, {'intents': ['room_lights_on']}]:
            with self.subTest(change=change), self.assertRaises(VoiceError):
                self.enqueue(binding={**self.binding, **change})

    def test_duplicate_is_stable_and_changed_content_fails_closed(self):
        self.assertEqual(self.enqueue(), 'recognised')
        self.assertEqual(self.enqueue(), 'recognised')
        for change in [{'intent': 'room_lights_on'}, {'occurred_at': '2026-10-17T08:00:01Z'}]:
            with self.assertRaises(VoiceError): self.enqueue({**self.event, **change})
        with self.assertRaises(VoiceError): self.enqueue(binding={**self.binding, 'revision': 2})
        calls = []
        self.send(lambda e, r: calls.append(e) or self.receipt(e, r))
        self.assertEqual(self.send(), None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.enqueue(), 'queued')

    def test_restart_preserves_original_id_and_does_not_send_on_open(self):
        self.enqueue()
        self.box.close()
        self.box = self.open_box()
        self.assertEqual(self.box.status(self.event['event_id']), 'recognised')
        calls = []
        self.send(lambda e, r: calls.append(e) or self.receipt(e, r))
        self.assertEqual(calls, [self.event])
        self.assertEqual(self.box.path.stat().st_mode & 0o777, 0o600)

    def test_second_worker_and_other_home_cannot_reuse_spool(self):
        with self.assertRaises(VoiceError): self.open_box()
        self.box.close()
        try:
            with self.assertRaises(VoiceError):
                VoiceOutbox(self.temp.name, str(uuid4()), self.installation, clock=lambda: self.now)
        finally:
            self.box = self.open_box()

    def test_corrupt_storage_fails_closed_preserves_bytes_and_releases_owner_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'voice-events.sqlite3'
            path.write_bytes(b'not a database')
            for _ in range(2):
                with self.assertRaises(VoiceError) as caught:
                    VoiceOutbox(directory, self.bridge, self.installation, clock=lambda: self.now)
                self.assertNotIn('Another voice transport', str(caught.exception))
                self.assertEqual(path.read_bytes(), b'not a database')

    def test_timeout_retries_same_event_with_current_binding(self):
        self.enqueue()
        calls = []
        def timeout(e, r):
            calls.append(e)
            raise TimeoutError()
        self.assertEqual(self.send(timeout)['status'], 'pending')
        self.assertEqual(self.send(lambda e, r: calls.append(e) or self.receipt(e, r))['status'], 'queued')
        self.assertEqual(calls, [self.event, self.event])

    def test_no_replay_after_revocation_or_revision_change(self):
        for change in [{'muted': True}, {'consent_current': False}, {'revision': 2}, {'installation_id': str(uuid4())}]:
            event = {**self.event, 'event_id': str(uuid4())}
            self.enqueue(event)
            result = self.box.exchange_one(lambda *_: self.fail('must not send'), lambda _: {**self.binding, **change})
            self.assertEqual(result['status'], 'failed')

    def test_lost_binding_connection_does_not_invent_revocation(self):
        self.enqueue()
        def unavailable(_): raise OSError()
        self.assertEqual(self.box.exchange_one(lambda *_: self.fail('must not send'), unavailable)['status'], 'pending')
        self.assertEqual(self.send()['status'], 'queued')

    def test_expiry_before_send_fails_but_after_ambiguous_send_is_uncertain(self):
        self.enqueue()
        self.now += 31
        self.assertEqual(self.box.status(self.event['event_id']), 'failed')
        self.now -= 31
        self.event['event_id'] = str(uuid4())
        self.enqueue()
        def timeout(*_): raise TimeoutError()
        self.send(timeout)
        self.now += 31
        self.assertEqual(self.box.status(self.event['event_id']), 'uncertain')
        self.assertIsNone(self.send())

    def test_revocation_after_uncertain_send_does_not_claim_failure(self):
        self.enqueue()
        def timeout(*_): raise TimeoutError()
        self.send(timeout)
        self.binding['muted'] = True
        self.assertEqual(self.send()['status'], 'uncertain')

    def test_wrong_or_invented_receipts_never_claim_delivery(self):
        self.enqueue()
        for change in [{'event_id': str(uuid4())}, {'binding_id': str(uuid4())}, {'binding_revision': 2},
                       {'protocol': True}, {'status': 'lights_confirmed'}, {'status': 'sent'}, {'heard': True}]:
            with self.subTest(change=change):
                self.assertEqual(self.send(lambda *_: {**self.receipt(), **change})['status'], 'pending')
        self.assertEqual(self.send(lambda *_: self.receipt(status='notification_accepted'))['status'], 'notification_accepted')
        self.assertIn('not confirmed', FEEDBACK['notification_accepted'])

    def test_receipt_evidence_is_scoped_to_each_request_type(self):
        for intent, status in [('help', 'family_acknowledged'), ('room_lights_on', 'lights_confirmed'), ('checkin_okay', 'checkin_recorded')]:
            event = {**self.event, 'intent': intent}
            self.assertEqual(receipt_status(self.receipt(event, status=status), event, 1), status)
            with self.assertRaises(VoiceError): receipt_status(self.receipt(event, status='sent'), event, 1)

    def test_bounded_queue_fails_explicitly_and_retains_no_speech(self):
        for _ in range(20): self.enqueue({**self.event, 'event_id': str(uuid4())})
        with self.assertRaises(VoiceError): self.enqueue()
        rows = self.box.db.execute('select event from events').fetchall()
        self.assertEqual(len(rows), 20)
        self.assertEqual(set(json.loads(rows[0][0])), set(self.event))
        self.now += 86401
        self.box.prune()
        self.assertEqual(self.box.db.execute('select count(*) from events').fetchone()[0], 0)

    def poll(self, status='family_acknowledged', calls=None):
        def fetch(ident, binding, revision):
            if calls is not None: calls.append((ident, binding, revision))
            return self.receipt(status=status)
        return self.box.poll_receipt(fetch, lambda _: self.binding)

    def test_receipt_recovery_never_resends_and_stops_after_actual_acknowledgement(self):
        self.enqueue(); self.send()
        calls=[]
        self.assertEqual(self.poll(calls=calls)['status'], 'family_acknowledged')
        self.assertEqual(calls, [(self.event['event_id'], self.binding_id, 1)])
        self.now += 6
        self.assertIsNone(self.poll(calls=calls)); self.assertEqual(len(calls), 1)
        self.assertIsNone(self.send())

    def test_unchanged_receipts_emit_no_repeated_feedback_and_are_rate_bounded(self):
        self.enqueue(); self.send(); calls=[]
        self.assertIsNone(self.poll('queued', calls)); self.assertIsNone(self.poll('queued', calls))
        self.assertEqual(len(calls), 1)
        self.now += 5
        self.assertEqual(self.poll('notification_accepted', calls)['status'], 'notification_accepted')
        self.now += 5
        self.assertIsNone(self.poll('queued', calls))
        self.assertEqual(self.box.status(self.event['event_id']), 'notification_accepted')

    def test_ambiguous_expired_send_can_read_receipt_but_cannot_send_again(self):
        self.enqueue()
        def timeout(*_): raise TimeoutError()
        self.send(timeout); self.now += 31
        self.assertIsNone(self.send())
        self.assertEqual(self.box.status(self.event['event_id']), 'uncertain')
        self.assertEqual(self.poll()['status'], 'family_acknowledged')

    def test_changed_consent_revision_or_installation_ends_receipt_recovery_without_fetch(self):
        for change in [{'muted': True}, {'revision': 2}, {'installation_id': str(uuid4())}]:
            event={**self.event,'event_id':str(uuid4())}; self.enqueue(event)
            self.box.exchange_one(lambda e,r:self.receipt(e,r),lambda _:self.binding)
            result=self.box.poll_receipt(lambda *_:self.fail('must not fetch'),lambda _:{**self.binding,**change})
            self.assertEqual(result['status'],'uncertain')
            self.now += 5
            self.assertIsNone(self.box.poll_receipt(lambda *_:self.fail('ended'),lambda _:self.binding))

    def test_receipt_network_failure_and_wrong_identity_preserve_existing_evidence(self):
        self.enqueue(); self.send()
        def unavailable(*_): raise TimeoutError()
        self.assertIsNone(self.box.poll_receipt(unavailable,lambda _:self.binding))
        self.now += 5
        self.assertIsNone(self.box.poll_receipt(lambda *_:{**self.receipt(),'event_id':str(uuid4())},lambda _:self.binding))
        self.assertEqual(self.box.status(self.event['event_id']),'queued')
        self.now += 5
        self.assertEqual(self.poll()['status'],'family_acknowledged')

    def test_receipt_polling_survives_restart_and_retains_no_new_speech_or_credentials(self):
        self.enqueue();self.send();self.poll('queued');self.box.close();self.box=self.open_box()
        self.assertIsNone(self.poll())
        self.now += 5
        self.assertEqual(self.poll()['status'],'family_acknowledged')
        self.assertEqual(set(json.loads(self.box.db.execute('select event from events').fetchone()[0])),set(self.event))

    def test_old_spool_schema_upgrade_preserves_events_and_original_attempts(self):
        self.enqueue();self.send();self.box.close()
        import sqlite3
        db=sqlite3.connect(Path(self.temp.name)/'voice-events.sqlite3')
        db.execute('alter table events drop column last_receipt_check');db.execute('alter table events drop column receipt_ended');db.commit();db.close()
        self.box=self.open_box()
        self.assertEqual(self.box.status(self.event['event_id']),'queued')
        self.assertEqual(self.poll()['status'],'family_acknowledged')

    def test_revocation_during_dispatch_recovery_permanently_ends_later_receipt_reads(self):
        self.enqueue()
        def timeout(*_): raise TimeoutError()
        self.send(timeout);self.binding['muted']=True
        self.assertEqual(self.send()['status'],'uncertain')
        self.binding['muted']=False
        self.assertIsNone(self.box.poll_receipt(lambda *_:self.fail('revoked event'),lambda _:self.binding))


if __name__ == '__main__': unittest.main()
