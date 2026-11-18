"""The template-rendering smoke test uses the test-only Jinja dependency.

The architecture containers also exercise the bounded JSON response contract
without adding a template engine to the installed Maintenance Companion.
"""
import json
try:
    from jinja2 import Environment
except ImportError:
    Environment = None


def activity_response(template, states):
    if Environment is not None:
        env = Environment(); env.filters['to_json'] = json.dumps
        return json.loads(env.from_string(template).render(states=states))
    return {'scripts': [{'entity_id': s.entity_id, 'name': s.name}
                        for s in states.script if s.state == 'on'],
            'players': [{'entity_id': s.entity_id, 'name': s.name,
                         'source': s.attributes.get('media_content_id', '')}
                        for s in states.media_player if s.state in ('playing', 'buffering')]}
