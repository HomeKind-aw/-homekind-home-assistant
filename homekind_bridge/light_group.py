"""Inactive v1 light-group executor; requires an atomic cloud authoriser adapter.

Not wired into legacy poll/execute. Never emulate a group by dispatching its
members separately. A single HA action still needs hardware timing validation.
"""
import math
import re
import time
import uuid

PROTOCOL = "homekind.light-group.v1"
VOICE_PROTOCOL = "homekind.voice-light.v1"
LIGHT = re.compile(r"^light\.[a-z0-9_]{1,150}$")


class GroupRejected(ValueError):
    pass


def _command(value):
    if not isinstance(value, dict) or value.get("protocol") not in (PROTOCOL, VOICE_PROTOCOL):
        raise GroupRejected("Unsupported group protocol.")
    voice = value["protocol"] == VOICE_PROTOCOL
    identity = "binding_id" if voice else "group_id"
    for field in ("id", "lease_id", "household_id", identity):
        try:
            uuid.UUID(value[field])
        except (ValueError, KeyError, TypeError, AttributeError):
            raise GroupRejected("Invalid group identity.") from None
    entities = value.get("entities")
    if not isinstance(entities, list) or not (1 if voice else 2) <= len(entities) <= (20 if voice else 32) or any(not isinstance(e, str) or not LIGHT.fullmatch(e) for e in entities) or len(set(entities)) != len(entities):
        raise GroupRejected("Invalid light group.")
    if type(value.get("revision")) is not int or value["revision"] < 1 or value.get("desired") not in (("on",) if voice else ("on", "off")):
        raise GroupRejected("Invalid group action.")
    expiry = value.get("expires_at")
    if type(expiry) not in (float, int) or not math.isfinite(expiry):
        raise GroupRejected("Invalid group expiry.")
    # Copy the complete scope so a callback cannot silently mutate its targets.
    return {key: value[key] for key in ("protocol", "id", "lease_id", "household_id", identity, "revision", "desired", "expires_at")} | {"entities": tuple(entities)}


def execute_light_group(claim, *, read_states, locally_enabled, authorise,
                        dispatch, stopped=lambda: False, clock=time.monotonic,
                        wall_clock=time.time, sleep=time.sleep):
    """Authorise once, dispatch once, and require evidence for every member.

    authorise MUST atomically recheck whole-group membership/revision, requester
    session/permissions, bridge generation and expiry, persist dispatched status,
    and refuse a second dispatch lease. No callback is provided by the old API.
    read_states returns only requested local states and honours timeout seconds;
    no unrelated values leave. Adapter HTTP timeouts must bound each request.
    """
    command = _command(claim)
    entities = command["entities"]
    desired = command["desired"]
    started = clock()
    deadline = started + 3

    def live():
        if stopped() or wall_clock() >= command["expires_at"]:
            raise GroupRejected("Group request ended.")
        if any(locally_enabled(entity) is not True for entity in entities):
            raise GroupRejected("Group control is not enabled locally.")

    def snapshot(timeout=.5):
        live()
        values = read_states(entities, timeout=timeout)
        if not isinstance(values, dict):
            raise GroupRejected("Invalid group readings.")
        return {entity: values.get(entity) for entity in entities}

    def ready(values):
        return all(isinstance(values[e], dict) and values[e].get("entity_id") == e and values[e].get("state") in ("on", "off") for e in entities)

    # Bounded attempts also protect against a frozen/faulty monotonic clock.
    for _ in range(16):
        before = snapshot(timeout=min(.5, max(.001, deadline - clock())))
        if ready(before):
            break
        if clock() >= deadline:
            raise GroupRejected("The whole group is not ready.")
        sleep(min(.2, max(0, deadline - clock())))
    else:
        raise GroupRejected("The whole group is not ready.")
    live()
    lease = authorise({**command, "entities": list(entities)})
    if not isinstance(lease, dict) or lease.get("status") != "dispatched" or _command(lease) != command:
        raise GroupRejected("Group authorisation changed.")
    # Re-read the whole group after the asynchronous authorisation boundary.
    before = snapshot()
    if not ready(before):
        raise GroupRejected("The whole group is not ready.")
    live()
    if all(before[e]["state"] == desired for e in entities):
        return {"status": "reported", "dispatched": False, "members": {e: "reported" for e in entities}}
    try:
        # Fixed service, exact authorised entity list. No member-by-member calls.
        dispatch("light", "turn_" + desired, {"entity_id": list(entities)})
    except Exception:
        # The action may have happened. Never retry physical dispatch here.
        return {"status": "unknown", "dispatched": True, "members": {e: "unknown" for e in entities}}
    evidence = {e: "unconfirmed" for e in entities}
    for attempt in range(11):
        try:
            after = snapshot()
        except Exception:
            break
        for entity in entities:
            state = after[entity]
            if not isinstance(state, dict) or state.get("entity_id") != entity or state.get("state") != desired:
                evidence[entity] = "unconfirmed"
                continue
            context = state.get("context") if isinstance(state.get("context"), dict) else {}
            old_context = before[entity].get("context") if isinstance(before[entity].get("context"), dict) else {}
            attributes = state.get("attributes") if isinstance(state.get("attributes"), dict) else {}
            changed = state.get("last_updated") != before[entity].get("last_updated") or context.get("id") != old_context.get("id")
            assumed = attributes.get("assumed_state") is True
            evidence[entity] = "confirmed" if changed and not assumed else "reported"
        if all(value == "confirmed" for value in evidence.values()):
            return {"status": "confirmed", "dispatched": True, "members": evidence}
        if attempt < 10:
            sleep(.2)
    # A matching state without changed/non-assumed evidence is still not physical
    # proof. Keep the whole group unconfirmed until every member is confirmed.
    return {"status": "unconfirmed", "dispatched": True, "members": evidence}
