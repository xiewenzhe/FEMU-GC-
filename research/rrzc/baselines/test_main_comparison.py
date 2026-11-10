"""The selected main comparison must not promote the retained literal failure."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent / 'common'))
import verify_main_comparison as main


class MainSelectionTest(unittest.TestCase):
    def test_user_selection_keeps_literal_as_failed_supplement(self):
        plan = main.read_plan()
        self.assertIn('oaza-adapted', plan['main_arms'])
        self.assertNotIn('oaza-literal', plan['main_arms'])
        self.assertEqual(plan['retained_failure_arm'], 'oaza-literal')
        self.assertFalse(plan['formal_joint_model_comparison'])

    def test_literal_cannot_silently_replace_selected_main_arm(self):
        plan = deepcopy(main.read_plan()); plan['main_arms'][2] = 'oaza-literal'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'plan.json'; path.write_text(json.dumps(plan))
            with self.assertRaises(AssertionError): main.read_plan(path)

    def test_failed_execution_is_rejected_before_main_archive_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / '阶段归档.json').write_text(json.dumps({'业务成功': False}))
            with mock.patch.object(main, 'closed_archive', return_value=({}, None)):
                with self.assertRaises(AssertionError): main.archive_files(root)


if __name__ == '__main__': unittest.main()
