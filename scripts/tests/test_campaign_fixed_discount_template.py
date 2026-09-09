from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campaign_discount_template as fixed
import campaign_generate_current_files as generator
from campaign_official_template import fill_single_discount_rows, read_rows
from verify_campaign_frozen_outcomes import CONTRACT, RECEIPT, verify


class FixedDiscountTemplateTest(unittest.TestCase):
    master = Path(__file__).resolve().parents[2] / "backend/app/assets/taobao_templates/single_item_discount_user_fixed_20260909.xlsx"

    def test_pinned_master_and_default_loader(self):
        raw = self.master.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), fixed.FIXED_SHA256)
        with patch.object(fixed, "FIXED_TEMPLATE", self.master):
            self.assertEqual(fixed.load_fixed_discount_template(), raw)
        self.assertEqual(fixed.load_fixed_discount_template(self.master), raw)

    def test_missing_or_changed_is_local_error_not_download(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.xlsx"
            with self.assertRaisesRegex(ValueError, "unavailable.*no_redownload"):
                fixed.load_fixed_discount_template(path)
            path.write_bytes(b"not the approved master")
            with self.assertRaisesRegex(ValueError, "changed_do_not_redownload"):
                fixed.load_fixed_discount_template(path)

    def test_real_master_examples_replaced_and_nondata_unchanged(self):
        raw = self.master.read_bytes()
        selected = [dict(item="999999990001", sku="999999990002", deduct="12.34")]
        output = fill_single_discount_rows(raw, selected)
        rows = read_rows(output, "Sheet1")
        self.assertEqual(set(rows), {1, 2})
        self.assertEqual((rows[2]["A"], rows[2]["B"], rows[2]["C"]), ("999999990001", "999999990002", "12.34"))
        self.assertEqual(rows[2].get("D", ""), "")
        self.assertEqual(rows[2].get("E", ""), "")
        with ZipFile(BytesIO(raw)) as before, ZipFile(BytesIO(output)) as after:
            self.assertEqual(before.namelist(), after.namelist())
            for name in before.namelist():
                if name != "xl/worksheets/sheet1.xml":
                    self.assertEqual(before.read(name), after.read(name))
        self.assertEqual(self.master.read_bytes(), raw)

    def test_previous_filled_batch_cannot_be_master(self):
        filled = fill_single_discount_rows(self.master.read_bytes(), [dict(item="999999990001", sku="999999990002", deduct="1")])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "previous.xlsx"
            path.write_bytes(filled)
            with self.assertRaisesRegex(ValueError, "use_filled_batch"):
                fixed.load_fixed_discount_template(path)

    def test_generator_defaults_but_keeps_activity_required(self):
        text = Path(generator.__file__).read_text(encoding="utf-8")
        self.assertIn("type=Path,default=FIXED_TEMPLATE", text)
        self.assertIn("'--activity-template',type=Path,required=True", text)
        self.assertIn("load_fixed_discount_template(discount_path)", text)
        self.assertNotIn("args.discount_template.read_bytes()", text)

    def test_contract_freezes_only_single_discount_exception(self):
        contract = json.loads(CONTRACT.read_text(encoding="utf-8-sig"))
        receipt = json.loads(RECEIPT.read_text(encoding="utf-8-sig"))
        self.assertEqual(verify(contract, receipt), [])
        self.assertTrue(contract["template"]["fresh_per_campaign"])
        for key in ("download_each_campaign", "ask_user_to_redownload", "automatic_redownload", "reuse_previous_filled_rows"):
            changed = deepcopy(contract)
            changed["single_discount_template"][key] = True
            self.assertIn("frozen_rule_changed:single_discount_template", verify(changed, receipt))


if __name__ == "__main__":
    unittest.main()
