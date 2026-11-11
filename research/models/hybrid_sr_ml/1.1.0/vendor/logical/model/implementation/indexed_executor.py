"""Indexed execution of the same bounded relational algebra on full immutable batches."""
import json
from indexed_relations import RelationIndex
import typed_plan as base
from discovery_runtime import execute_event

class IndexedExecutor:
    def __init__(self,case):self.case=case;self.indexes={}
    def matches(self,source,relation):
        key=json.dumps(relation,sort_keys=True,separators=(",",":"))
        if key not in self.indexes:self.indexes[key]=RelationIndex(self.case,relation)
        return self.indexes[key].matches(source)
    def execute(self,plan,requires_complete):
        if plan["kind"]=="unexplained_event":return execute_event(self.case,plan,requires_complete,matches_fn=self.matches)
        if plan["kind"]!="duplicate_key":return base.execute_plan(self.case,plan,requires_complete,matches_fn=self.matches)
        table=plan["select"]["table"];complete=self.case["capture"]["entity_family_complete"];out=[]
        relation={"select":plan["select"],"join":[{"source":k,"target":k}for k in plan["keys"]]}
        for i,row in enumerate(self.case["records"][table]):
            selected=base.filt(row,plan["select"]["filter"]);support=[]
            if selected is not True:value=selected
            elif requires_complete and not complete:value=None
            elif any(row.get(k)is None for k in plan["keys"]):value=None
            else:
                found,unknown=self.matches(row,relation);support=[{"table":t,"row_index":j}for t,j,r in found]
                value=True if len(found)>1 else None if unknown or not complete else False
            out.append({"source_table":table,"source_row_index":i,"source_record":row,"value":value,"support_rows":support})
        return out
