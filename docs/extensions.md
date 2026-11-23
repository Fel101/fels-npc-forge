# Local extensions

Install a trusted extension by copying its folder into `local/extensions/` and restarting NPC Forge. Each extension has a
`register(host)` entry point. The server loads it at startup and rolls back all its registrations if loading fails.

Extensions can register pages under `/ext/`, HTTP endpoints under `/api/ext/`, and controls in the existing page. The
extension's own installation instructions say where its controls appear.

Extension portrait engines must name their own registered setting. Forge applies its minimum-age check to those settings
and validates the character when the portrait hook runs. An extension guard can refuse a request without altering the
character passed to it.

A body extension can register build steps with `host.add_body_build_step`. The `before_clothes` stage sees the neutral
or shaped model before fitting. The `body_finished` stage sees the animated rig after the base model and its hair and
cloth are finished. Both stages participate in the extension registration transaction.

Separate add-ons for adults are available privately and follow the same core safety rules.
