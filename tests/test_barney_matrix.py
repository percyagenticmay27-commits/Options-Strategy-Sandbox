import unittest
from barney_matrix import compare,MATRIX
class BarneyMatrixTest(unittest.TestCase):
 def test_no_implicit_fills_from_candidates(self):
  r=compare('BULLISH',bwb={'status':'CANDIDATE','reason':'PENDING_SECOND_NEWER_LEG_QUOTES','maximum_loss_usd':'95'},debit={'status':'CANDIDATE','maximum_loss_usd':'100'})
  self.assertFalse(r['order_submission_enabled'])
  self.assertEqual(r['broker_submissions'],0)
  self.assertEqual(r['execution_status'],'NO_PAPER_FILL')
  self.assertTrue(all(not x['paper_fill'] for x in r['candidates']))
  self.assertEqual(r['choices'][2],'PUT_BROKEN_WING')
 def test_unknown_direction_fails_closed(self):
  self.assertEqual(compare('MAYBE')['status'],'DATA_UNAVAILABLE')
 def test_no_research_is_not_a_synthetic_candidate(self):
  r=compare('BEARISH')
  self.assertTrue(all(x['status']=='DATA_UNAVAILABLE' for x in r['candidates']))
if __name__=='__main__':unittest.main()
