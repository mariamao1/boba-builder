"""Task 22: participant edit window after submitting."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from app import group_orders, runs


ORDER = {
    "person": "Alice",
    "drink": "Taro Slush",
    "size": "Large",
    "sugar": "50%",
    "toppings": ["Boba"],
    "quantity": 1,
}

ROOT = Path(__file__).resolve().parent.parent
ORGANIZER_JS = ROOT / "app" / "static" / "group-order-organizer.js"
PARTICIPANT_JS = ROOT / "app" / "static" / "group-order.js"
PARTICIPANT_CSS = ROOT / "app" / "static" / "group-order.css"


class EditWindowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.original_dir = group_orders.SESSION_DIR
        self.original_run_dir = runs.RUN_DIR
        group_orders.SESSION_DIR = Path(self.temporary.name) / "group-orders"
        runs.RUN_DIR = Path(self.temporary.name) / "runs"

    def tearDown(self):
        group_orders.SESSION_DIR = self.original_dir
        runs.RUN_DIR = self.original_run_dir
        self.temporary.cleanup()

    def test_name_match_alone_cannot_edit_but_token_can(self):
        room, organizer_token = group_orders.create()
        order, edit_token, _room = group_orders.add_order(room["id"], ORDER)

        # Same display name, wrong token: no access.
        with self.assertRaises(group_orders.Forbidden):
            group_orders.update_order(
                room["id"], order["id"], {"quantity": 2},
                order_token="wrong-token")
        with self.assertRaises(group_orders.Forbidden):
            group_orders.delete_order(
                room["id"], order["id"], order_token="wrong-token")

        # A second line under the same name gets its own token.
        second, second_token, _room = group_orders.add_order(
            room["id"], {**ORDER, "drink": "Matcha Milk"})
        self.assertNotEqual(edit_token, second_token)
        with self.assertRaises(group_orders.Forbidden):
            group_orders.update_order(
                room["id"], second["id"], {"quantity": 2},
                order_token=edit_token)

        updated, _room = group_orders.update_order(
            room["id"], order["id"], {"quantity": 2}, order_token=edit_token)
        self.assertEqual(updated["quantity"], 2)

        # The organizer token manages any line while the room is open.
        updated, _room = group_orders.update_order(
            room["id"], second["id"], {"notes": "extra straw"},
            organizer_token=organizer_token)
        self.assertEqual(updated["notes"], "extra straw")

    def test_tokens_never_appear_in_room_reads(self):
        room, _token = group_orders.create()
        _order, edit_token, _room = group_orders.add_order(room["id"], ORDER)

        payload = group_orders.get(room["id"])
        dumped = json.dumps(payload).lower()
        self.assertNotIn(edit_token, dumped)
        self.assertNotIn("token", dumped)
        self.assertNotIn("edit_token_hash", dumped)

    def test_edit_keeps_created_at_and_advances_updated_at(self):
        submitted = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.timezone.utc)
        edited = submitted + dt.timedelta(minutes=5)
        room, _token = group_orders.create(now=submitted)
        order, edit_token, _room = group_orders.add_order(
            room["id"], ORDER, now=submitted)

        updated, aggregate = group_orders.update_order(
            room["id"], order["id"], {"notes": "no straw"},
            order_token=edit_token, now=edited)

        self.assertEqual(updated["created_at"], order["created_at"])
        self.assertEqual(updated["updated_at"], "2026-09-01T12:05:00Z")
        self.assertNotEqual(updated["updated_at"], updated["created_at"])
        visible = [line for line in aggregate["orders"]
                   if line["id"] == order["id"]][0]
        self.assertEqual(visible["created_at"], order["created_at"])
        self.assertEqual(visible["updated_at"], "2026-09-01T12:05:00Z")

    def test_participant_changes_close_with_lock_deadline_and_finalize(self):
        now = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.timezone.utc)
        room, organizer_token = group_orders.create(now=now)
        order, edit_token, _room = group_orders.add_order(
            room["id"], ORDER, now=now)

        group_orders.set_status(room["id"], "locked", organizer_token, now=now)
        with self.assertRaises(group_orders.RoomNotOpen):
            group_orders.update_order(
                room["id"], order["id"], {"quantity": 2},
                order_token=edit_token, now=now)
        with self.assertRaises(group_orders.RoomNotOpen):
            group_orders.delete_order(
                room["id"], order["id"], order_token=edit_token, now=now)
        # Organizer moderation stays available while paused ...
        with self.assertRaises(group_orders.RoomNotOpen):
            group_orders.update_order(
                room["id"], order["id"], {"quantity": 2},
                organizer_token=organizer_token, now=now)
        aggregate = group_orders.delete_order(
            room["id"], order["id"], organizer_token=organizer_token, now=now)
        self.assertEqual(aggregate["orders"], [])

        # ... and past the deadline for the organizer only.
        room2, organizer_token2 = group_orders.create(
            expires_in_hours=1, now=now)
        order2, edit_token2, _room = group_orders.add_order(
            room2["id"], ORDER, now=now)
        late = now + dt.timedelta(hours=2)
        with self.assertRaises(group_orders.DeadlinePassed):
            group_orders.update_order(
                room2["id"], order2["id"], {"quantity": 2},
                order_token=edit_token2, now=late)
        with self.assertRaises(group_orders.DeadlinePassed):
            group_orders.delete_order(
                room2["id"], order2["id"], order_token=edit_token2, now=late)
        aggregate = group_orders.delete_order(
            room2["id"], order2["id"], organizer_token=organizer_token2,
            now=late)
        self.assertEqual(aggregate["orders"], [])

        # Finalizing closes the room for everyone, including the organizer.
        room3, organizer_token3 = group_orders.create(now=now)
        order3, edit_token3, _room = group_orders.add_order(
            room3["id"], ORDER, now=now)
        group_orders.finalize(room3["id"], organizer_token3, now=now)
        with self.assertRaises(group_orders.RoomNotOpen):
            group_orders.update_order(
                room3["id"], order3["id"], {"quantity": 2},
                order_token=edit_token3, now=now)
        with self.assertRaises(group_orders.RoomNotOpen):
            group_orders.delete_order(
                room3["id"], order3["id"], organizer_token=organizer_token3,
                now=now)

    def test_change_markers_are_wired_into_both_pages(self):
        organizer = ORGANIZER_JS.read_text(encoding="utf-8")
        self.assertIn("Edited", organizer)
        self.assertIn("order.updated_at", organizer)

        participant = PARTICIPANT_JS.read_text(encoding="utf-8")
        self.assertIn("edited-badge", participant)
        self.assertIn("order.updated_at", participant)
        # Edit/remove controls stay limited to owned lines while open.
        self.assertIn("ownership && session.accepting_orders", participant)

        css = PARTICIPANT_CSS.read_text(encoding="utf-8")
        self.assertIn(".edited-badge", css)


if __name__ == "__main__":
    unittest.main()
