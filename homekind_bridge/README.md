# HomeKind Home Connector

Selected sensor readings from your Home Assistant Green, in your HomeKind home view.

Install, start, then tap **Open Web UI** to paste your HomeKind pairing code and select sensors on your phone. The connector starts in read-only mode and uses your local Home Assistant session. Its private credentials stay on the Green.

Your HomeKind household controls who can see each room. Device registration, history, alerts and notifications are separate choices in HomeKind.

This pilot app supports Home Assistant OS on aarch64 and amd64. Physical Green and phone commissioning is required; see the Documentation tab.


## Optional light and plug controls (0.4.0)

After updating, open the connector Web UI and enable **Allow light and plug controls**, then save. Read-only is the default. Current pairing and selected devices are retained. In HomeKind, enable controls per device under **Display & alerts**. HomeKind groups can request all enabled lights in a room together; every member is authorised separately and its actual state is reported back. Only selected light and switch entities can be controlled.
