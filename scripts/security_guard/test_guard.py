import datetime as dt
from pathlib import Path
import tempfile
import unittest
import os

from common import Journal, TZ, finding

DAY = dt.datetime(2026, 11, 12, 12, tzinfo=TZ).timestamp()


class Alerts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.temp.name) / 'journal.db')
        self.sent = []

    def tearDown(self):
        self.j.db.close()
        self.temp.cleanup()

    def sender(self, text, batch):
        self.sent.append(text)
        return 'om_test_receipt'

    def test_persistent_fault_once_recovery_and_recurrence(self):
        f = finding('x', 'fault', 'sample')
        self.j.observe('a', [f], DAY)
        self.assertTrue(self.j.dispatch(self.sender, DAY)['sent'])
        self.j.observe('a', [dict(f, detail='changed counts')], DAY + 3600)
        self.assertFalse(self.j.dispatch(self.sender, DAY + 3600)['sent'])
        self.j.observe('a', [], DAY + 7200)
        self.assertTrue(self.j.dispatch(self.sender, DAY + 7200)['sent'])
        self.j.observe('a', [f], DAY + 10800)
        self.assertTrue(self.j.dispatch(self.sender, DAY + 10800)['sent'])
        self.assertEqual(3, len(self.sent))

    def test_unknown_delivery_never_retried_even_after_restart(self):
        self.j.observe('a', [finding('x', 'fault', 'x')], DAY)
        def timeout(*args):
            raise TimeoutError()
        self.assertEqual('unknown', self.j.dispatch(timeout, DAY)['status'])
        self.j.db.close()
        self.j = Journal(Path(self.temp.name) / 'journal.db')
        self.assertFalse(self.j.dispatch(self.sender, DAY + 1)['sent'])
        self.j.observe('a', [], DAY + 2)
        self.assertFalse(self.j.dispatch(self.sender, DAY + 2)['sent'])

    def test_quiet_hours_critical_bypasses_and_resolved_warning_not_sent(self):
        night = dt.datetime(2026, 11, 12, 23, 10, tzinfo=TZ).timestamp()
        self.j.observe('a', [finding('x', 'warning', 'x')], night)
        self.assertFalse(self.j.dispatch(self.sender, night)['sent'])
        self.j.observe('a', [], night + 1)
        self.assertFalse(self.j.dispatch(self.sender, DAY + 86400)['sent'])
        self.j.observe('a', [finding('y', 'malware', 'x', 'critical')], night + 2)
        self.assertTrue(self.j.dispatch(self.sender, night + 2)['sent'])

    def test_escalation_not_every_severity_fluctuation(self):
        self.j.observe('a', [finding('x', 'x', 'x')], DAY)
        self.j.dispatch(self.sender, DAY)
        self.j.observe('a', [finding('x', 'x', 'x', 'critical')], DAY + 1)
        self.assertTrue(self.j.dispatch(self.sender, DAY + 1)['sent'])
        self.j.observe('a', [finding('x', 'x', 'x', 'critical')], DAY + 2)
        self.assertFalse(self.j.dispatch(self.sender, DAY + 2)['sent'])

    def test_daily_cap_counts_unknown_and_rolls_over(self):
        for i in range(2):
            self.j.observe('a', [finding(str(i), 'x', 'x')], DAY + i)
            self.j.dispatch(self.sender, DAY + i, daily_cap=1)
        self.assertEqual(1, len(self.sent))
        self.assertTrue(self.j.dispatch(self.sender, DAY + 86400, daily_cap=1)['sent'])

    def test_scope_loss_does_not_clear_other_scopes(self):
        self.j.observe('hourly', [finding('x', 'malware', 'x')], DAY)
        self.j.observe('availability', [], DAY + 1)
        self.assertEqual(1, self.j.summary()['active'])

    def test_commission_notice_is_not_an_active_fault(self):
        self.j.observe('commission', [finding('ready', 'enabled', 'x')], DAY)
        self.assertEqual(0, self.j.summary()['active'])
        self.assertTrue(self.j.dispatch(self.sender, DAY)['sent'])


@unittest.skipIf(os.name == 'nt', 'NAS collector uses Linux fcntl')
class BoundedCollector(unittest.TestCase):
    def test_symlink_is_recorded_not_followed(self):
        from nas_guard import bounded_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'secret').write_text('not read through link')
            (root/'link').symlink_to(root/'secret')
            result = bounded_snapshot([str(root/'link')])
            self.assertEqual(0, result['budget']['bytes'])
            self.assertIn('link', result['files'][str(root/'link')])

    def test_file_budget_is_explicitly_incomplete(self):
        from nas_guard import bounded_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            for n in range(3):
                (Path(tmp)/str(n)).write_text('x')
            result = bounded_snapshot([tmp], max_files=1)
            self.assertEqual(1, result['budget']['files'])
            self.assertIn('file_count_limit', result['errors'])

    def test_large_file_is_metadata_only(self):
        from nas_guard import bounded_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'large'
            p.write_bytes(b'x'*100)
            result = bounded_snapshot([str(p)], max_bytes=10)
            self.assertEqual(0, result['budget']['bytes'])
            self.assertTrue(result['files'][str(p)]['metadata_only'])

    def test_known_marker_found_without_executing_file(self):
        from nas_guard import bounded_snapshot
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'sample'
            p.write_text('# nonexecuting test kpx.lck')
            result = bounded_snapshot([str(p)])
            self.assertTrue(result['files'][str(p)]['known_marker'])



if __name__ == '__main__':
    unittest.main()
