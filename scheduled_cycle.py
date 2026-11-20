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
from datetime import datetime,timedelta,timezone
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

REFRESH_OVERDUE_AFTER=timedelta(minutes=2)


def ensure_refresh_request(state:Path,now:datetime,reason:str)->dict:
    """Durable authenticated-collector handoff on every stale/missing batch.

    A request is not a fresh quote. Preserve its timestamp until a genuinely
    newer provider batch arrives; otherwise a repeated watchdog tick can
    forever outrun the collector by moving the acceptance cutoff forward.
    """
    path=state/'refresh_request.json'
    current=load(path,{})
    if current.get('status')=='PENDING_CONNECTOR_REFRESH':
        try:
            originally=timestamp(current['requested_at_utc'])
        except (KeyError,ValueError,TypeError,AttributeError):
            originally=now
        if originally<=now:
            # A pending request is safely idempotent. Record each watchdog
            # retry but never grant permission to replay old provider data.
            data=dict(current)
            data['last_retry_at_utc']=iso(now)
            data['retry_count']=int(current.get('retry_count',0))+1
            data['last_observed_failure']=reason
            data['paper_only']=True
            data['broker_submissions']=0
            atomic(path,data)
            return data
    data={'status':'PENDING_CONNECTOR_REFRESH','requested_at_utc':iso(now),
        'last_retry_at_utc':iso(now),'retry_count':1,
        'reason':reason,'last_observed_failure':reason,
        'collector_action':'FETCH_NEW_AUTHENTICATED_ROBINHOOD_READ_ONLY_BATCH_AND_RETRY',
        'target':'state/incoming_robinhood_batch.json',
        'technical_plan':'state/technical_collection_plan.json',
        'contract_coverage_plan':'state/directional_collection_request.json',
        'second_quote_handoff':'state/second_quote_request.json',
        'required_symbols':['SPX','XSP'],
        'required_option_rights':['put','call'],
        'requires_original_provider_timestamps':True,
        'requires_newer_than_request':True,
        'max_quote_age_seconds':60,
        'next_action':'PUBLISH_GENUINELY_NEW_PROVIDER_BATCH_THEN_INVOKE_REFRESH_RECOVERY_IMMEDIATELY',
        'paper_only':True,'live_trading_enabled':False,
        'broker_execution_enabled':False,'order_submission_enabled':False,
        'broker_submissions':0}
    atomic(path,data)
    return data


def coordinate(root:Path,now:datetime,*,run_recovery,run_scan):
    root=Path(root);state=root/'state';state.mkdir(parents=True,exist_ok=True)
    request=load(state/'refresh_request.json',{})
    prior=load(state/'latest.json',{})
    pending=(request.get('status')=='PENDING_CONNECTOR_REFRESH' or
             prior.get('status')=='PENDING_CONNECTOR_REFRESH')
    inbound=state/INBOX
    consumed=load(state/'last_consumed_provider_batch.json',{})
    def report(status,reason,action):
        data={'status':status,'reason':reason,'action':action,
              'at_utc':iso(now),'paper_only':True,'broker_submissions':0,
              'scan_started':False if status!='SCAN_COMPLETED' else True}
        if status in ('PENDING_CONNECTOR_REFRESH','WAITING_FOR_FRESH_CONNECTOR_BATCH'):
            refresh=ensure_refresh_request(state,now,reason)
            data['refresh_requested']=True
            data['refresh_request_path']='state/refresh_request.json'
            data['refresh_attempt_count']=refresh.get('retry_count')
            data['refresh_requested_at_utc']=refresh.get('requested_at_utc')
            data['collector_action']=refresh.get('collector_action','FETCH_NEW_AUTHENTICATED_ROBINHOOD_READ_ONLY_BATCH_AND_RETRY')
            # The provider fetch must happen outside this uncredentialed server
            # by the connected collector. Never claim it was fetched here.
            data['provider_fetch_status']='AUTHENTICATED_COLLECTOR_REQUIRED'
            # Don't let "pending" conceal collector failure for an hour.
            # Keep the original request and recheck new genuinely timestamped
            # batches at the next watchdog. This is an explicit ERROR state,
            # not a claim that the server has Robinhood credentials.
            try:
                elapsed=now-timestamp(refresh['requested_at_utc'])
            except (KeyError,ValueError,TypeError,AttributeError):
                elapsed=REFRESH_OVERDUE_AFTER
            data['refresh_age_seconds']=max(0,int(elapsed.total_seconds()))
            if elapsed>=REFRESH_OVERDUE_AFTER:
                data['status']='REFRESH_OVERDUE_AUTHENTICATED_COLLECTOR'
                data['reason']='AUTHENTICATED_ROBINHOOD_COLLECTOR_NOT_DELIVERING_NEW_BATCH'
                data['collector_alert']=True
                data['required_action']='CONNECTED_COLLECTOR_MUST_FETCH_PUBLISH_AND_TRIGGER_RECOVERY'
                provider=load(state/'server_collector_health.json',{})
                if provider.get('status')=='PROVIDER_EVIDENCE_INCOMPLETE':
                    data['reason']='SERVER_PROVIDER_EVIDENCE_INCOMPLETE'
                    data['server_provider_blockers']=list(provider.get('blockers') or [])
                    data['server_provider_checked_at_utc']=provider.get('generated_at_utc')
                    data['required_action']='FIX_PROVIDER_ACCESS_AND_ADD_VERIFIED_CASH_INDEX_SPOT_BEFORE_NEW_SCAN'
            else:
                data['collector_alert']=False
        atomic(state/'scheduled_cycle_latest.json',data)
        return data
    if pending:
        # If prior reported pending but file is missing, preserve the request
        # across restarts. Never silently discard a pending refresh obligation.
        if request.get('status')!='PENDING_CONNECTOR_REFRESH':
            request=ensure_refresh_request(state,now,prior.get('feed_status','PRIOR_CYCLE_REQUIRES_REFRESH'))
        ready,why=input_fresh_since_request(inbound,request,now)
        if not ready:
            return report('PENDING_CONNECTOR_REFRESH',why,'FETCH_FRESH_READ_ONLY_ROBINHOOD_DATA_BEFORE_NEXT_SCAN')
        result=run_recovery(inbound)
        if result.get('status')!='RESUMED':
            return report('PENDING_CONNECTOR_REFRESH',result.get('reason','REFRESH_NOT_VERIFIED'),
                          'RETRY_NEW_CONNECTOR_DATA_NO_SCAN')
        return report('SCAN_COMPLETED','FRESH_DATA_RECOVERY_COMPLETED','RECOVERY_SCANNED_ONCE')
    # Collection-driven, not timer-driven. The timer is a watchdog, not a
    # substitute for a new authenticated provider observation.
    ready,why=input_fresh_since_request(inbound,{},now)
    if not ready:
        return report('PENDING_CONNECTOR_REFRESH',why,'REQUEST_AUTHENTICATED_PROVIDER_REFRESH_AND_RETRY')
    batch=load(inbound,{})
    if consumed.get('generated_at_utc') and timestamp(batch['generated_at_utc'])<=timestamp(consumed['generated_at_utc']):
        return report('PENDING_CONNECTOR_REFRESH','CONNECTOR_BATCH_ALREADY_CONSUMED',
                      'REQUEST_AUTHENTICATED_PROVIDER_REFRESH_AND_RETRY')
    result=run_recovery(inbound)
    if result.get('status')!='RESUMED':
        return report('PENDING_CONNECTOR_REFRESH',result.get('reason','RECOVERY_NOT_VERIFIED'),
                      'FETCH_FRESH_READ_ONLY_ROBINHOOD_DATA_BEFORE_NEXT_SCAN')
    return report('SCAN_COMPLETED','NEW_PROVIDER_BATCH_EVALUATED','RECOVERY_SCANNED_ONCE')

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
    return 0 if result['status'] in ('SCAN_COMPLETED','PENDING_CONNECTOR_REFRESH','WAITING_FOR_FRESH_CONNECTOR_BATCH','REFRESH_OVERDUE_AUTHENTICATED_COLLECTOR') else 66

if __name__=='__main__':raise SystemExit(main())
