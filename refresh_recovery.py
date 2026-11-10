"""OPTION-001-style connector refresh and automatic sandbox paper-cycle recovery.

The connected Robinhood read-only agent owns fetching current prices. This
module does not access broker credentials, place orders, or forge timestamps.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from forward_paper import atomic,load
from replay import CT,iso,ENTRY_CUTOFF,ENTRY_START,number
from scan_paper import option_bot_idle,choose_snapshot
from observe import frames_from_snapshot
from replay import quotes_at,spot_at

ROOT=Path(__file__).resolve().parent

def mode_at(now):
    local=now.astimezone(CT).time()
    return 'ENTRY_AND_MANAGEMENT' if ENTRY_START <= local < ENTRY_CUTOFF else 'MANAGEMENT_ONLY'

def refresh_request(root,now,reason):
    atomic(Path(root)/'state/refresh_request.json',{
        'status':'PENDING_CONNECTOR_REFRESH','requested_at_utc':iso(now),
        'reason':reason,'max_quote_age_seconds':60,
        'required_sequence':['FETCH_ACTUAL_ROBINHOOD_DATA','VERIFY_INSTRUMENT_METADATA',
             'ATOMIC_PUBLISH','VALIDATE_FRESHNESS','RUN_SANDBOX_PAPER_CYCLE'],
        'paper_only':True,'broker_submissions':0})

def recover_cycle(root,*,now,publish,validate,run):
    """Publish -> validate -> run. Never retry with the same stale batch."""
    root=Path(root)
    state=root/'state'
    state.mkdir(parents=True,exist_ok=True)
    result={'status':'REFRESH_RETRY_REQUIRED','requested_at_utc':iso(now),
        'mode':mode_at(now),'paper_only':True,'broker_submissions':0,
        'order_submission_enabled':False,'strategy_id':'SANDBOX_STRATEGY_B'}
    def finish(status,reason):
        result.update(status=status,reason=reason,completed_at_utc=iso(datetime.now(timezone.utc)))
        atomic(state/'refresh_recovery.json',result)
        return result
    if not option_bot_idle():
        return finish('OPTION_BOT_PRIORITY_DEFERRED','OPTION_001_LOCK_BUSY_OR_UNAVAILABLE')
    try:
        publish()
    except (ValueError,OSError,TypeError,KeyError,subprocess.CalledProcessError) as exc:
        refresh_request(root,now,'PUBLISH_FAILED:'+str(exc))
        return finish('REFRESH_RETRY_REQUIRED','PUBLISH_FAILED:'+str(exc))
    valid,reason=validate()
    if not valid:
        refresh_request(root,now,reason)
        return finish('REFRESH_RETRY_REQUIRED',reason)
    output=run()
    if output.get('broker_submissions',0)!=0 or output.get('order_submission_enabled',False):
        raise ValueError('BROKER_ORDER_SAFETY_VIOLATION')
    if output.get('status')=='OPTION_BOT_PRIORITY_DEFERRED':
        return finish('OPTION_BOT_PRIORITY_DEFERRED','OPTION_001_LOCK_BUSY')
    stamp=output.get('generated_at_utc')
    current_report=False
    if stamp:
        try:
            from replay import timestamp
            age=(datetime.now(timezone.utc)-timestamp(stamp)).total_seconds()
            # A test/historical clock may precede the live wall clock;
            # the report must be no older than this recovery invocation.
            current_report=timestamp(stamp)>=now and age<=60
        except (ValueError,TypeError,AttributeError):
            current_report=False
    if output.get('status')!='PAPER_ACTIVE' or output.get('feed_status')!='FRESH' or not current_report:
        refresh_request(root,now,output.get('feed_status') or output.get('status') or 'SCAN_FAILED')
        return finish('REFRESH_RETRY_REQUIRED','STALE_DURING_CYCLE')
    result.update(paper_entries=output.get('paper_entries',0),
                  new_paper_events=output.get('new_paper_events',0),
                  feed_status=output.get('feed_status'))
    return finish('RESUMED','FRESH_DATA_RETRY_COMPLETED')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path,help='Newly fetched actual connector batch JSON')
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--session',type=Path,default=Path('/home/tradebot/Trade-PMCC-Bot/inputs/alpaca_pmcc_research.json'))
    args=parser.parse_args()
    root=args.root.resolve()
    now=datetime.now(timezone.utc)
    snapshot=root/'state/robinhood_research.json'
    def publish():
        command=[sys.executable,str(root/'publish_snapshot.py'),
            '--input',str(args.input),'--session',str(args.session),'--output',str(snapshot)]
        env=dict(os.environ)
        module_path='/home/tradebot/Trade-Option-Bot/src'
        env['PYTHONPATH']=module_path+(':'+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        proc=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=35,env=env)
        if proc.returncode!=0:raise ValueError('SNAPSHOT_PUBLICATION_FAILED:'+proc.stderr[-600:])
    def validate():
        current=datetime.now(timezone.utc)
        s=choose_snapshot([load(snapshot,{})],current)
        if s is None:return False,'STALE_RESEARCH_SNAPSHOT'
        session=load(args.session,{}).get('session',{})
        frames,status=frames_from_snapshot(s,session,current)
        if status!='FRESH':return False,status
        if not frames or not any(quotes_at(f,current) and spot_at(f,current) is not None for f in frames):
            return False,'NO_VERIFIED_QUOTES_OR_SPOT'
        return True,'CURRENT'
    def run():
        process=subprocess.run([sys.executable,str(root/'scan_paper.py'),'--root',str(root),
            '--snapshot',str(snapshot),'--session',str(args.session)],
            cwd=root,capture_output=True,text=True,timeout=45)
        payload=load(root/'state/latest.json',{})
        if process.returncode not in (0,66):
            return {'status':'SCAN_FAILED','feed_status':'SCAN_EXIT_'+str(process.returncode),
                    'broker_submissions':0}
        return payload
    result=recover_cycle(root,now=now,publish=publish,validate=validate,run=run)
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status']=='RESUMED' else 66

if __name__=='__main__':raise SystemExit(main())
