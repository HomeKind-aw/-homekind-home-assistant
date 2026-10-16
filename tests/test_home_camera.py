import importlib.util
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

MODULE = Path(__file__).resolve().parents[1] / "homekind_bridge" / "camera_relay.py"
spec = importlib.util.spec_from_file_location("homekind_camera_qa", MODULE)
camera = importlib.util.module_from_spec(spec)
spec.loader.exec_module(camera)


class CameraTests(unittest.TestCase):
    def connector(self, **kwargs):
        c = SimpleNamespace(live_cameras={"camera.qa"}, allowlist={"camera.qa"}, supervisor=True,
                            ha_token="private-local-test-token", credentials={"bridge_id":"11111111-1111-4111-8111-111111111111","token":"a"*64},
                            stopping=lambda:False, ha=lambda path:{"state":"idle"})
        c.__dict__.update(kwargs)
        return c

    def session(self, connector=None):
        messages=[]
        s=camera.CameraSession(connector or self.connector(),{"id":"11111111-1111-4111-8111-111111111111","entity_id":"camera.qa"},messages.append)
        return s,messages

    def test_offers_cannot_send_audio_or_video_into_the_home(self):
        valid = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=recvonly\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\n"
        self.assertTrue(camera.receive_only_offer(valid))
        self.assertFalse(camera.receive_only_offer(valid.replace("a=recvonly", "a=sendrecv", 1)))
        self.assertFalse(camera.receive_only_offer(valid.replace("a=recvonly", "a=sendonly", 1)))
        self.assertFalse(camera.receive_only_offer(valid.replace("a=recvonly\r\n", "")))
        self.assertFalse(camera.receive_only_offer("v=0\r\n"))

    def test_local_camera_opt_in_is_enforced_before_home_assistant_is_called(self):
        c=self.connector(live_cameras=set(),ha=lambda _:self.fail("HA must not be contacted"))
        s,messages=self.session(c);s.run()
        self.assertEqual(messages[0]["payload"]["code"],"local_selection_required")

    def test_unavailable_camera_does_not_open_a_socket(self):
        s,messages=self.session(self.connector(ha=lambda _: {"state":"off"}));s.run()
        self.assertEqual(messages[0]["payload"]["code"],"camera_unavailable")

    def test_ha_configuration_does_not_share_ha_ice_credentials(self):
        s,messages=self.session();s.handle({"type":"result","id":1,"success":True,"result":{"dataChannel":"test","configuration":{"iceServers":[{"credential":"local-only"}]}}})
        self.assertEqual(messages[0]["payload"],{"data_channel":"test"})
        self.assertNotIn("local-only",json.dumps(messages))

    def test_candidates_wait_for_the_exact_home_assistant_session(self):
        sent=[];s,messages=self.session();s.socket=SimpleNamespace(send=lambda value:sent.append(json.loads(value)))
        s.send_candidate({"candidate":"candidate:QA"});self.assertEqual(sent,[])
        s.handle({"type":"event","id":2,"event":{"type":"session","session_id":"ha-test-session"}})
        self.assertEqual(sent[0]["session_id"],"ha-test-session")
        self.assertEqual(sent[0]["entity_id"],"camera.qa")
        s.handle({"type":"event","id":2,"event":{"type":"answer","answer":"v=0\r\nQA"}})
        self.assertEqual(messages[0]["kind"],"answer")

    def test_native_errors_are_sanitised(self):
        s,messages=self.session();s.handle({"type":"event","id":2,"event":{"type":"error","message":"rtsp://private-password@home"}})
        self.assertNotIn("private-password",json.dumps(messages));self.assertEqual(messages[0]["payload"],{"code":"connection_failed"})

    def test_complete_home_assistant_signalling_and_unsubscribe_without_media_or_secrets(self):
        sent, outgoing = [], []
        frames = [
            {"type": "auth_required"}, {"type": "auth_ok"},
            {"type": "result", "id": 1, "success": True, "result": {}},
            {"type": "result", "id": 2, "success": True},
            {"type": "event", "id": 2, "event": {"type": "session", "session_id": "HA-LOCAL-SESSION"}},
            {"type": "event", "id": 2, "event": {"type": "answer", "answer": "v=0\r\nQA ANSWER"}},
            {"type": "event", "id": 2, "event": {"type": "candidate", "candidate": {"candidate": "candidate:QA", "sdpMid": "0", "sdpMLineIndex": 0}}},
        ]
        class Socket:
            closed = 0
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def recv(self, **_): return json.dumps(frames.pop(0))
            def send(self, value): sent.append(json.loads(value))
            def close(self): self.closed += 1
        socket = Socket()
        def emit(value):
            outgoing.append(value)
            if value["kind"] == "candidate": session.stop()
        session = camera.CameraSession(self.connector(), {"id": "11111111-1111-4111-8111-111111111111", "entity_id": "camera.qa"}, emit, connect=lambda *a, **k: socket)
        session.accept({"sequence": 1, "kind": "offer", "payload": {"sdp": "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\n"}})
        session.accept({"sequence": 2, "kind": "candidate", "payload": {"candidate": "candidate:VIEWER"}})
        session.run()
        self.assertEqual([x["kind"] for x in outgoing], ["config", "answer", "candidate"])
        self.assertEqual([x.get("type") for x in sent], ["auth", "camera/webrtc/get_client_config", "camera/webrtc/offer", "camera/webrtc/candidate", "unsubscribe_events"])
        self.assertEqual(sent[3]["session_id"], "HA-LOCAL-SESSION")
        self.assertNotIn("private-local-test-token", json.dumps(outgoing))
        self.assertEqual(socket.closed, 1)

    def test_cloud_cannot_request_an_unselected_camera(self):
        response={"sessions":[{"id":"11111111-1111-4111-8111-111111111111","entity_id":"camera.other","signals":[]}]}
        relay=camera.CameraRelay(self.connector(request=lambda *args:response))
        with self.assertRaises(ValueError):relay.exchange()
        self.assertEqual(relay.sessions,{})


if __name__ == "__main__":
    unittest.main()
