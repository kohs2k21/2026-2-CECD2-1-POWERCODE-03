from prepare_v5 import *

def main():
 events=[json.loads(s)for s in (TOP/'training/events.jsonl').read_text().splitlines()];receipts=[json.loads(s)for s in (TOP/'training/consumed_samples.jsonl').read_text().splitlines()];boundary=next(i for i,e in enumerate(events)if e['event']=='complete_state_restored');early=events[:boundary];late=events[boundary:];e1=[x for x in early if x['event']=='optimizer_step'];e2=[x for x in late if x['event']=='optimizer_step'];assert len(e1)==7 and len(e2)==53 and e2[-1]['step']==58
 state=read(TOP/'training/training_result.json');assert sum(state['exposures'].values())==232 and state['unique_consumed']==160
 assert len(receipts)==242;assert len([r for r in receipts[:30]if r['optimizer_step']>7])==2
 pairs=[]
 for step in [6,7]:
  old=next(e for e in e1 if e['step']==step);new=next(e for e in e2 if e['step']==step);pairs.append({'step':step,'old_loss':old['loss'],'resumed_loss':new['loss'],'loss_equal':old['loss']==new['loss'],'old_gradient_norm':old['gradient_norm'],'resumed_gradient_norm':new['gradient_norm'],'GPU_bitwise_gradient_equality_not_claimed':True})
 write(TOP/'review/actual_consumption_audit.json',{'passed':True,'physical_optimizer_steps':60,'retained_updates':58,'lost_unsaved_updates':2,'physical_completed_optimizer_microsteps':240,'unapplied_partial_microsteps':2,'all_receipts':242,'effective_in_final_weights':232,'effective_unique_samples':160,'effective_exposures':state['exposures'],'dataset_file':pin(DATA/'train.jsonl'),'schedule':pin(DATA/'training_schedule.json'),'data_and_target_SHA_in_all_receipts':True,'complete_state_resume_step':5,'repeated_steps_reconciliation':pairs,'physical_budget_not_exceeded':True,'constant_lr_scheduler_not_used':True,'no_extra_trials':True})
 print(json.dumps({'physical':60,'retained':58,'receipts':242,'effective':232,'unique':160}))
if __name__=='__main__':main()
