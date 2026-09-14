from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_residual_completion as r
from campaign_continuous_execute import validate_request


class ResidualTests(unittest.TestCase):
    def setUp(self):
        self.request=dict(schema='continuous_campaign_residual_v1',rule_sha=r.RULE_SHA,
            parent_request_id=r.PARENT,items=r.ITEMS.copy(),excluded_items=r.EXCLUDED.copy())

    def test_registered_via_existing_entry(self):
        self.assertEqual(validate_request(self.request),'畔色木作')

    def test_coworker_exclusion_cannot_be_removed(self):
        self.request['excluded_items']=[]
        with self.assertRaisesRegex(ValueError,'exact_residual'):validate_request(self.request)

    def test_price_selectors_or_paths_cannot_be_injected(self):
        for key in ('price','selector','command','template_path','url'):
            q=dict(self.request,**{key:'anything'})
            with self.assertRaises(ValueError):validate_request(q)

    def test_scope_cannot_expand(self):
        self.request['items'].append('793052650673')
        with self.assertRaises(ValueError):validate_request(self.request)

    def test_frozen_rule_cannot_change(self):
        self.request['rule_sha']='0'*64
        with self.assertRaises(ValueError):validate_request(self.request)

    def test_transport_rejects_peer_product_before_action(self):
        t=object.__new__(r.BookTransport)
        with self.assertRaisesRegex(ValueError,'outside_exact_book_scope'):
            t.execute('signup','action',{'items':['793052650673']})

    def test_transport_preserves_success_and_unknown(self):
        t=object.__new__(r.BookTransport);t.authority=Mock()
        t.prepared={'page':{'start':'a','end':'b'}}
        for status in ('success','unknown'):
            t.authority.blocked.return_value={r.BOOK:status}
            with self.assertRaisesRegex(ValueError,'protected'):
                t.execute('signup','action',{'items':[r.BOOK]})

    def test_scope_keeps_full_export_count(self):
        t=object.__new__(r.BookTransport)
        t.prepared={'scope':{'observed_item_count':59,'platform_rows':[{'item':'other','on_sale':True}]},
            'snapshot':{'resolved_price_version_sha256':'v'},
            'report':{'source_terminal':'source','batch':'831931699','errors':[]}}
        result=t.step_scope('a',{},None)
        self.assertEqual(result['observed_item_count'],59)
        self.assertEqual(result['erp_sellable'],[r.BOOK])
        self.assertEqual(result['prior_outcomes'],{r.BOOK:'failed'})


if __name__=='__main__':unittest.main()
