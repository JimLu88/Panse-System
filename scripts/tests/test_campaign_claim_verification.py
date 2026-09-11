from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_submission_gate import verify_claim, consume_claim


class ClaimVerificationTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:',isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE attempts(id,bundle_id,campaign,phase,start,end,item,status)')
        self.claim='a'*32
        self.db.execute('CREATE TABLE claim_transports(claim_id,transport,state,job_id)')
        self.db.execute('INSERT INTO claim_transports VALUES(?,?,?,NULL)',(self.claim,'dedicated_edge_v1','claimed_not_dispatched'))
        self.db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?)',
                        (self.claim+':1','bundle','1/2/3','signup','start','end','1','unknown'))
        self.authority=SimpleNamespace(db=self.db)
        self.body={'campaign':'1/2/3','start':'start','end':'end','signup_rows':[{'item':'1'}]}

    def tearDown(self):
        self.db.close()

    def test_revalidates_without_new_claim(self):
        with patch('campaign_submission_gate.validated_body',return_value=(self.body,{'path':'test','sha256':'hash'})) as validation:
            result=verify_claim(self.authority,self.claim)
            validation.assert_called_once_with(self.authority,'bundle','signup')
        self.assertTrue(result['verified_claim'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],1)

    def test_terminal_and_invalid_claim_rejected(self):
        self.db.execute("UPDATE attempts SET status='success'")
        for claim in (self.claim,'%', 'missing'):
            with self.assertRaises(ValueError): verify_claim(self.authority,claim)

    def test_incomplete_scope_rejected(self):
        self.body['signup_rows'].append({'item':'2'})
        with patch('campaign_submission_gate.validated_body',return_value=(self.body,{})):
            with self.assertRaisesRegex(ValueError,'incomplete'):
                verify_claim(self.authority,self.claim)

    def test_old_unknown_claim_has_no_new_transport_authority(self):
        self.db.execute('DELETE FROM claim_transports')
        with self.assertRaisesRegex(ValueError,'do_not_replay'):
            verify_claim(self.authority,self.claim)

    def test_dispatch_consumed_once_even_with_new_job(self):
        with patch('campaign_submission_gate.validated_body',return_value=(self.body,{})):
            result=consume_claim(self.authority,self.claim,'b'*64)
            self.assertTrue(result['dispatch_consumed'])
            for job in ('b'*64,'c'*64):
                with self.assertRaisesRegex(ValueError,'do_not_replay'):
                    consume_claim(self.authority,self.claim,job)
