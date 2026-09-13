"""Dependency-free contract checks for the standalone Zotero bridge."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "integrations" / "zotero"
SCHEMAS = BRIDGE / "schemas"
FIXTURES = BRIDGE / "fixtures"
SOURCES = BRIDGE / "src"

ALLOWED_COMMANDS = {
    "collection.addItems",
    "collection.removeItems",
    "tag.add",
    "tag.remove",
    "note.createChild",
    "item.importPaper",
}


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


def walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def collect_command_literals(schema: dict[str, Any]) -> set[str]:
    commands: set[str] = set()
    for node in walk(schema):
        if not isinstance(node, dict):
            continue
        for value in node.get("enum", []):
            if isinstance(value, str) and "." in value:
                commands.add(value)
        value = node.get("const")
        if isinstance(value, str) and "." in value:
            commands.add(value)
    return commands


class ZoteroBridgeContractTests(unittest.TestCase):
    def test_all_schemas_are_json_schema_2020_12_and_refs_resolve(self) -> None:
        paths = sorted(SCHEMAS.glob("*.schema.json"))
        self.assertEqual(
            {path.name for path in paths},
            {
                "ack.schema.json",
                "command-batch.schema.json",
                "command.schema.json",
                "snapshot.schema.json",
            },
        )
        for path in paths:
            schema = load_json(path)
            self.assertEqual(
                schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
            )
            for node in walk(schema):
                if not isinstance(node, dict) or "$ref" not in node:
                    continue
                reference = node["$ref"]
                self.assertIsInstance(reference, str)
                if reference.startswith("#/"):
                    cursor: Any = schema
                    for component in reference[2:].split("/"):
                        cursor = cursor[component]
                elif not reference.startswith(("http://", "https://")):
                    self.assertTrue(
                        (SCHEMAS / reference.split("#", 1)[0]).is_file(),
                        f"Unresolved schema ref {reference} in {path.name}",
                    )

    def test_snapshot_fixture_covers_all_four_entity_kinds(self) -> None:
        snapshot = load_json(FIXTURES / "snapshot.json")
        schema = load_json(SCHEMAS / "snapshot.schema.json")
        self.assertEqual(snapshot["schemaVersion"], "1.0")
        self.assertTrue(set(schema["required"]).issubset(snapshot))
        for field in ("items", "collections", "tags", "attachments"):
            self.assertIsInstance(snapshot[field], list)
            self.assertGreater(len(snapshot[field]), 0)

        key = re.compile(r"^[A-Z0-9]{8}$")
        for field in ("items", "collections", "attachments"):
            keys = [entity["key"] for entity in snapshot[field]]
            self.assertEqual(keys, sorted(keys))
            self.assertTrue(all(key.fullmatch(value) for value in keys))
        for item in snapshot["items"]:
            for field in ("collectionKeys", "tags", "attachmentKeys"):
                self.assertEqual(item[field], sorted(set(item[field])))
        self.assertNotIn("path", json.dumps(snapshot).lower())

    def test_command_allowlist_matches_schema_source_and_fixture(self) -> None:
        schema = load_json(SCHEMAS / "command.schema.json")
        self.assertEqual(collect_command_literals(schema), ALLOWED_COMMANDS)

        protocol_source = (SOURCES / "protocol.ts").read_text(encoding="utf-8")
        source_commands = set(
            re.findall(
                r'"((?:collection|tag|note|item)\.[A-Za-z]+)"', protocol_source
            )
        )
        self.assertEqual(source_commands, ALLOWED_COMMANDS)

        batch = load_json(FIXTURES / "command-batch.json")
        self.assertEqual(batch["schemaVersion"], "1.0")
        self.assertTrue(batch["commands"])
        self.assertTrue(
            all(command["type"] in ALLOWED_COMMANDS for command in batch["commands"])
        )
        self.assertTrue(
            all(command.get("preconditions") for command in batch["commands"])
        )

    def test_ack_fixture_uses_closed_status_vocabulary(self) -> None:
        schema = load_json(SCHEMAS / "ack.schema.json")
        statuses = set(schema["properties"]["status"]["enum"])
        self.assertEqual(statuses, {"applied", "conflict", "rejected", "failed"})
        ack = load_json(FIXTURES / "ack.json")
        self.assertEqual(ack["schemaVersion"], "1.0")
        self.assertIn(ack["status"], statuses)

    def test_bridge_uses_zotero_api_not_direct_sqlite_access(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(SOURCES.glob("*.ts"))
        )
        for required_api in (
            "Zotero.Items",
            "Zotero.Collections",
            "Zotero.Libraries",
            "Zotero.Notifier.registerObserver",
            "Zotero.DB.executeTransaction",
        ):
            self.assertIn(required_api, source)
        for forbidden in (
            "better-sqlite",
            "sqlite3",
            "openDatabase(",
            "executeSQL(",
            "Zotero.DB.queryAsync",
            "Zotero.DB.valueQueryAsync",
        ):
            self.assertNotIn(forbidden, source)

    def test_transport_is_loopback_only_and_token_is_not_logged(self) -> None:
        protocol = (SOURCES / "protocol.ts").read_text(encoding="utf-8")
        transport = (SOURCES / "transport.ts").read_text(encoding="utf-8")
        bridge = (SOURCES / "bridge.ts").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1"', protocol)
        self.assertIn('"::1"', protocol)
        self.assertNotIn('"localhost"', protocol)
        self.assertIn("Authorization", protocol)
        self.assertNotIn("console.", protocol + transport + bridge)
        self.assertNotRegex(
            protocol + transport + bridge,
            r"logger\.(?:debug|info|warn|error)\([^\n]*pairingToken",
        )
        for fixture in FIXTURES.glob("*.json"):
            self.assertNotIn("pairingToken", fixture.read_text(encoding="utf-8"))

    def test_conflict_and_replay_rules_are_present(self) -> None:
        bridge = (SOURCES / "bridge.ts").read_text(encoding="utf-8")
        protocol = (SOURCES / "protocol.ts").read_text(encoding="utf-8")
        self.assertIn("entityVersionEquals", bridge)
        self.assertIn('status: "conflict"', bridge)
        self.assertIn("data-scholar-split-command-id", protocol)
        self.assertIn("getNotes()", bridge)
        self.assertLess(
            protocol.index("a.version !== b.version"),
            protocol.index("a.dateModified === b.dateModified"),
        )

    def test_directory_has_agpl_notice_and_integration_instructions(self) -> None:
        license_text = (BRIDGE / "LICENSE").read_text(encoding="utf-8")
        readme = (BRIDGE / "README.md").read_text(encoding="utf-8")
        self.assertIn("SPDX-License-Identifier: AGPL-3.0-or-later", license_text)
        self.assertIn("ScholarSplitZoteroBridge", readme)
        self.assertIn("bridge.start()", readme)
        self.assertIn("bridge.stop()", readme)


if __name__ == "__main__":
    unittest.main()
