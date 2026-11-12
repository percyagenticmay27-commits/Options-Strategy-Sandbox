"""Scheduled refresh-first Sandbox coordinator (never calls brokerage APIs).

If a connector refresh is pending, NO strategy scan is permitted until a new,
read-only provider batch has been published and independently validated by
refresh_recovery.py. The authorized connector worker must deposit its newly
fetched batch atomically at state/incoming_robinhood_batch.json.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path
from forward_paper import atomic,load
from replay import iso,timestamp,recent

ROOT=Path(__file__).resolve().parent
INBOX='incoming_robinhood_batch.json'

def input_fresh_since_request(path:Path,request:dict,now:datetime)->tuple[bool,str]:
    """Validate acquisition chronology, not just a conveniently recent mtime."""
    batch=load(path,{})
    if not batch:return False,'NEW_CONNECTOR_BATCH_REQUIRED'
    try:
        # Raw input must originate from read-only Robinhood research. It must
        # contain real time-stamped quotes (publisher validates all individual legs).
        if batch.get('source')!='ROBINHOOD_MCP_RESEARCH' or batch.get('research_only') is not True or batch.get('order_submission_enabled') is not False:
            return False,'UNVERIFIED_CONNECTOR_BATCH'
        collected=timestamp(batch['generated_at_utc'])
        requested=request.get('requested_at_utc')
        if requested and collected<=timestamp(requested):
            return False,'NO_NEW_BATCH_AFTER_REFRESH_REQUEST'
        if not recent(iso(collected),now):
            return False,'STALE_CONNECTOR_BATCH'
        if not isinstance(batch.get('symbols'),dict) or any(s not in batch['symbols'] for s in ('SPX','XSP')):
            return False,'INCOMPLETE_CONNECTOR_BATCH'
    except (KeyError,TypeError,ValueError,AttributeError):
        return False,'UNVERIFIED_CONNECTOR_BATCH'
    return True,'NEW_PROVIDER_BATCH_AVAILABLE'

def coordinate(root:Path,now:datetime,*,run_recovery,run_scan):
    root=Path(root);state=root/'state';state.mkdir(parents=True,exist_ok=True)
    request=load(state/'refresh_request.json',{})
    prior=load(state/'latest.json',{})
    pending=(request.get('status')=='PENDING_CONNECTOR_REFRESH' or
             prior.get('status')=='PENDING_CONNECTOR_REFRESH')
    inbound=state/INBOX
    def report(status,reason,action):
        data={'status':status,'reason':reason,'action':action,
              'at_utc':iso(now),'paper_only':True,'broker_submissions':0,
              'scan_started':False if status!='SCAN_COMPLETED' else True}
        atomic(state/'scheduled_cycle_latest.json',data)
        return data
    if pending:
        # If prior reported pending but file is missing, preserve the request
        # across restarts. Never silently discard a pending refresh obligation.
        if request.get('status')!='PENDING_CONNECTOR_REFRESH':
            request={'status':'PENDING_CONNECTOR_REFRESH','requested_at_utc':iso(now),
                     'reason':prior.get('feed_status','PRIOR_CYCLE_REQUIRES_REFRESH'),
                     'paper_only':True,'broker_submissions':0}
            atomic(state/'refresh_request.json',request)
        ready,why=input_fresh_since_request(inbound,request,now)
        if not ready:
            return report('PENDING_CONNECTOR_REFRESH',why,'FETCH_FRESH_READ_ONLY_ROBINHOOD_DATA_BEFORE_NEXT_SCAN')
        result=run_recovery(inbound)
        if result.get('status')!='RESUMED':
            return report('PENDING_CONNECTOR_REFRESH',result.get('reason','REFRESH_NOT_VERIFIED'),
                          'RETRY_NEW_CONNECTOR_DATA_NO_SCAN')
        return report('SCAN_COMPLETED','FRESH_DATA_RECOVERY_COMPLETED','RECOVERY_SCANNED_ONCE')
    # No pending flag: a regular cycle may use only an independently verified
    # fresh provider snapshot. The scanner itself performs full validation.
    result=run_scan()
    if result.get('status')=='PENDING_CONNECTOR_REFRESH':
        return report('PENDING_CONNECTOR_REFRESH',result.get('feed_status','REFRESH_REQUIRED'),
                      'FETCH_FRESH_READ_ONLY_ROBINHOOD_DATA_BEFORE_NEXT_SCAN')
    if result.get('status')!='PAPER_ACTIVE':
        return report('DEFERRED',result.get('status','SCAN_NOT_COMPLETED'),'NO_TRADE')
    return report('SCAN_COMPLETED','FRESH_DATA_EVALUATED','SCAN_COMPLETED')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args()
    root=args.root.resolve()
    def recovery(batch):
        proc=subprocess.run([sys.executable,str(root/'refresh_recovery.py'),
            '--input',str(batch),'--root',str(root)],cwd=root,capture_output=True,text=True,timeout=95)
        if proc.returncode not in (0,66):return {'status':'REFRESH_RETRY_REQUIRED','reason':'RECOVERY_EXIT_'+str(proc.returncode)}
        return load(root/'state/refresh_recovery.json',{})
    def scan():
        proc=subprocess.run([sys.executable,str(root/'scan_paper.py'),'--root',str(root)],
            cwd=root,capture_output=True,text=True,timeout=60)
        if proc.returncode not in (0,66):return {'status':'SCAN_FAILED','feed_status':'SCAN_EXIT_'+str(proc.returncode)}
        return load(root/'state/latest.json',{})
    result=coordinate(root,datetime.now(timezone.utc),run_recovery=recovery,run_scan=scan)
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status'] in ('SCAN_COMPLETED','PENDING_CONNECTOR_REFRESH','DEFERRED') else 66

if __name__=='__main__':raise SystemExit(main())
