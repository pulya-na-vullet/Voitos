from __future__ import annotations

from django.test import TestCase

from database.models import (
    AdminTask,
    AdminTaskKind,
    AdminTaskStatus,
    BotUser,
    ProfileStatus,
)
from services.address_overlap import (
    extract_house_parts,
    find_address_matches,
    heuristic_candidates,
    same_household_parts,
    scan_all_addresses,
    stable_group_id,
)


class HousePartsTests(TestCase):
    def test_different_houses_in_same_quarter(self):
        a = extract_house_parts("Д. Куюки, ул. 24 квартал, дом 1")
        b = extract_house_parts("Куюки, 24 квартал, 2")
        self.assertEqual(a["quarter"], "24")
        self.assertEqual(b["quarter"], "24")
        self.assertEqual(a["house"], "1")
        self.assertEqual(b["house"], "2")
        self.assertIs(same_household_parts(a, b), False)

    def test_same_house_different_spelling(self):
        a = extract_house_parts("Д. Куюки, ул. 24 квартал, дом 1")
        b = extract_house_parts("Куюки, ул. 24 квартал, д. 1")
        self.assertEqual(a["house"], "1")
        self.assertEqual(b["house"], "1")
        self.assertIs(same_household_parts(a, b), True)


class AddressScanTests(TestCase):
    def setUp(self) -> None:
        self.dmitry = BotUser.objects.create(
            max_user_id="d1",
            real_name="Дмитрий",
            locality="Куюки",
            address="Д. Куюки, ул. 24 квартал, дом 1",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.elena = BotUser.objects.create(
            max_user_id="e1",
            real_name="Елена",
            locality="Куюки",
            address="Куюки, ул. 24 квартал, д. 1",
            profile_status=ProfileStatus.VERIFIED,
        )
        self.anton = BotUser.objects.create(
            max_user_id="a1",
            real_name="Антон",
            locality="Куюки",
            address="Куюки, 24 квартал, 2",
            profile_status=ProfileStatus.VERIFIED,
        )

    def test_neighbors_not_heuristic_candidates(self):
        cands = heuristic_candidates(self.dmitry)
        ids = {u.id for u in cands}
        self.assertIn(self.elena.id, ids)
        self.assertNotIn(self.anton.id, ids)

    def test_scan_without_ai_skips_neighbors(self):
        groups = scan_all_addresses(use_ai=False)
        for g in groups:
            self.assertNotIn(self.anton.id, g["user_ids"])
            self.assertTrue(
                set(g["user_ids"]) <= {self.dmitry.id, self.elena.id}
                or self.anton.id not in g["user_ids"]
            )
        # Dmitry+Elena should appear
        self.assertTrue(
            any(
                set(g["user_ids"]) == {self.dmitry.id, self.elena.id}
                for g in groups
            )
        )

    def test_resolved_overlap_not_recreated(self):
        member_ids = sorted([self.dmitry.id, self.elena.id])
        source_id = stable_group_id(member_ids)
        AdminTask.objects.create(
            kind=AdminTaskKind.ADDRESS_OVERLAP,
            title="done",
            status=AdminTaskStatus.DONE,
            source_model="AddressOverlap",
            source_id=source_id,
            meta={"user_ids": member_ids},
            user=self.dmitry,
        )
        groups = scan_all_addresses(use_ai=False)
        self.assertEqual(groups, [])
        # Task stays done, not reopened
        task = AdminTask.objects.get(source_id=source_id, kind=AdminTaskKind.ADDRESS_OVERLAP)
        self.assertEqual(task.status, AdminTaskStatus.DONE)

    def test_find_matches_heuristic_only(self):
        matches = find_address_matches(self.dmitry, use_ai=False)
        ids = {m.candidate.id for m in matches}
        self.assertIn(self.elena.id, ids)
        self.assertNotIn(self.anton.id, ids)
