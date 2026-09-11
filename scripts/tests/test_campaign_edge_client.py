from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_edge_client import EdgeClient, EdgeJobError


def response(value):
    return Mock(status_code=200, json=Mock(return_value=value))


class ClientTests(unittest.TestCase):
    def test_full_export_wait_budget_does_not_strand_multiple_pages(self):
        client=EdgeClient('test-only',session=Mock())
        client.submit=Mock(return_value={'state':'running','job_id':'id'})
        client.wait=Mock(return_value={'state':'finished'})
        client.run('product_export',{})
        self.assertEqual(client.wait.call_args.kwargs['timeout'],1800)
        client.run('signup',{})
        self.assertEqual(client.wait.call_args.kwargs['timeout'],300)

    def test_post_once_then_only_exact_status(self):
        session=Mock()
        session.post.side_effect=[response({'ok':True,'job_id':'id','state':'running'}),
                                  response({'ok':True,'job_id':'id','state':'finished','result':{'state':'terminal'}})]
        client=EdgeClient('test-only',session=session)
        self.assertEqual(client.run('template',{})['state'],'finished')
        calls=session.post.call_args_list
        self.assertEqual([c.kwargs['json']['operation'] for c in calls],['program_job_submit','program_job_status'])
        self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in calls))

    def test_timeout_does_not_retry_submission(self):
        session=Mock()
        session.post.side_effect=requests.Timeout()
        with self.assertRaises(EdgeJobError): EdgeClient('test-only',session=session).run('signup',{})
        self.assertEqual(session.post.call_count,1)

    def test_wrong_job_and_unknown_not_success(self):
        session=Mock()
        session.post.return_value=response({'ok':True,'job_id':'wrong','state':'finished'})
        with self.assertRaises(EdgeJobError): EdgeClient('test-only',session=session).wait('id')
        session.post.return_value=response({'ok':True,'job_id':'id','state':'unknown'})
        self.assertEqual(EdgeClient('test-only',session=session).wait('id')['state'],'unknown')
