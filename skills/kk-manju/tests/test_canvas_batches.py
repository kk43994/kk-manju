import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from canvas_batches import preflight, verify


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.before = {'projectId':'p1', 'nodes':[{'id':'v1','type':'video','data':{'name':'R1','prompt':'p','url':['x']}}], 'edges':[]}
        self.plan = {'projectId':'p1', 'groups':[{'key':'r1','name':'试做 R1','nodes':['v1']}]}
        self.after = copy.deepcopy(self.before)
        self.after['nodes'][0]['parentId']='g1'
        self.after['nodes'].append({'id':'g1','type':'group','data':{'name':'试做 R1'}})
        self.receipts = [{'key':'r1','groupId':'g1'}]

    def test_preserves_media_allows_layout_and_grouping(self):
        self.after['nodes'][0]['position']={'x':12,'y':40}
        self.after['nodes'][0]['data']['_updatedAtMs']=45
        result = verify(self.before,self.after,self.plan,self.receipts)
        self.assertTrue(result['ok']); self.assertEqual(result['batches'][0]['count'],1)

    def test_rejects_project_duplicates_and_missing_nodes(self):
        for change in ('project','duplicate','unknown'):
            plan=copy.deepcopy(self.plan)
            if change=='project': plan['projectId']='p2'
            elif change=='duplicate': plan['groups'][0]['nodes'].append('v1')
            else: plan['groups'][0]['nodes']=['v2']
            with self.assertRaises(ValueError): preflight(self.before,plan)

    def test_detects_wrong_membership_prompt_and_edge_changes(self):
        for change in ('membership','prompt','edge','name','missing'):
            after=copy.deepcopy(self.after)
            if change=='membership': after['nodes'][0]['parentId']='g2'
            elif change=='prompt': after['nodes'][0]['data']['prompt']='changed'
            elif change=='edge': after['edges']=[{'id':'e1','source':'v1','target':'g1'}]
            elif change=='name': after['nodes'][1]['data']['name']='wrong'
            else: after['nodes'].pop(0)
            self.assertFalse(verify(self.before,after,self.plan,self.receipts)['ok'],change)
