from copy import deepcopy
from datetime import timedelta,datetime,timezone
from decimal import Decimal as D
import json,tempfile,unittest
from pathlib import Path
from sniper import evaluate,ScanPolicy
from sniper_cycle import run
from sniper_intraday import deadline
from tests.test_sniper import evidence,NOW

def fresh(payload,at):
    p=deepcopy(payload);p['generated_at_utc']=at.isoformat();p['underlying']['updated_at']=at.isoformat()
    for row in p['contracts']:row['quote']['updated_at']=at.isoformat()
    return p

def narrow(expiry='2026-11-11'):
    p=evidence(expiry=expiry)
    p['contracts'][0]['quote']['ask_price']='3.92'
    p['contracts'][1]['quote']['bid_price']='4.43'
    return p

class ExpirationTests(unittest.TestCase):
    def test_zero_dte_preferred_over_better_one_dte_return(self):
        p=narrow();tomorrow=deepcopy(p['contracts'])
        for row in tomorrow:
            row['expiration_date']='2026-11-12';row['instrument_id']+='-tomorrow';row['quote']['instrument_id']=row['instrument_id']
        tomorrow[1]['quote']['ask_price']='4.20'
        p['contracts']+=tomorrow
        r=evaluate(p,NOW)
        self.assertEqual(r['dte'],0);self.assertEqual(r['expiration'],'2026-11-11')
    def test_one_dte_fallback_allowed(self):
        r=evaluate(narrow('2026-11-12'),NOW)
        self.assertEqual(r['status'],'CANDIDATE');self.assertEqual(r['dte'],1)
    def test_two_and_december_expirations_rejected(self):
        for exp in ['2026-11-13','2026-12-04']:
            self.assertEqual(evaluate(narrow(exp),NOW)['reason'],'NO_VERIFIED_0_TO_1_DTE_SPREAD')
    def test_custom_policy_cannot_restore_long_dte(self):
        with self.assertRaises(ValueError):ScanPolicy(min_dte=14,max_dte=30)

class IntradayExitTests(unittest.TestCase):
    def open(self,root,expiry='2026-11-11'):
        p=narrow(expiry);run(root,p,NOW)
        at=NOW+timedelta(seconds=11);p=fresh(p,at);r=run(root,p,at)
        self.assertEqual(len(r['open']),1)
        return p,r
    def test_zero_and_one_dte_positions_stay_open_until_exit_condition(self):
        for expiry in ['2026-11-11','2026-11-12']:
            with tempfile.TemporaryDirectory() as d:
                p,r=self.open(d,expiry)
                r=run(d,fresh(p,NOW+timedelta(seconds=30)),NOW+timedelta(seconds=30))
                self.assertEqual(len(r['open']),1)
                self.assertEqual(r['open'][0]['mandatory_exit_at'],'2026-11-11T20:20:00Z')
    def test_both_expirations_close_same_day_at_1420_ct(self):
        for expiry in ['2026-11-11','2026-11-12']:
            with tempfile.TemporaryDirectory() as d:
                p,r=self.open(d,expiry)
                at=NOW.replace(hour=20,minute=20)
                r=run(d,fresh(p,at),at)
                self.assertFalse(r['open']);self.assertEqual(r['completed'][0]['exit_reason'],'INTRADAY_TIME_EXIT')
    def test_stale_exit_quotes_mark_pending_without_invented_sale(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=self.open(d);at=NOW.replace(hour=20,minute=20)
            r=run(d,p,at)
            self.assertTrue(r['open'][0]['exit_pending']);self.assertFalse(r['completed'])
            self.assertEqual(r['open'][0]['requested_exit_reason'],'INTRADAY_TIME_EXIT')
    def test_legacy_december_trade_closed_and_excluded_without_deleting_audit(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=self.open(d);r['open'][0]['expiration']='2026-12-04'
            path=Path(d)/'state/sniper_paper_ledger.json';path.write_text(json.dumps(r))
            at=NOW+timedelta(seconds=30);r=run(d,fresh(p,at),at)
            self.assertFalse(r['open']);self.assertTrue(r['completed'][0]['excluded_from_strategy_statistics'])
            self.assertEqual(r['completed'][0]['exit_reason'],'DTE_POLICY_MIGRATION_EXIT')
            self.assertEqual(r['performance']['closed_verified'],0)
            self.assertEqual(len([e for e in r['events'] if e['action']=='PAPER_BUY']),1)
    def test_early_close_moves_exit_before_verified_session_end(self):
        s=evidence()['session'];s['close']='2026-11-11T18:00:00Z'
        self.assertEqual(deadline(NOW,s).isoformat(),'2026-11-11T17:50:00+00:00')
    def test_no_entry_without_verified_session(self):
        with tempfile.TemporaryDirectory() as d:
            p=narrow();p.pop('session');r=run(d,p,NOW)
            self.assertFalse(r['open']);self.assertFalse(r['pending_candidates'])
    def test_no_overnight_continuation(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=self.open(d,'2026-11-12');at=NOW+timedelta(days=1)
            r=run(d,fresh(p,at),at)
            self.assertFalse(r['open']);self.assertEqual(r['completed'][0]['exit_reason'],'OVERNIGHT_POSITION_EXIT_REQUIRED')

    def test_management_close_all_exits_even_if_scheduled_worker_starts_early(self):
        with tempfile.TemporaryDirectory() as d:
            p,r=self.open(d,'2026-11-12');at=NOW.replace(hour=20,minute=17)
            r=run(d,fresh(p,at),at,close_all=True)
            self.assertFalse(r['open']);self.assertFalse(r['pending_candidates'])
            self.assertEqual(r['completed'][0]['exit_reason'],'INTRADAY_MANAGEMENT_CLOSE')
