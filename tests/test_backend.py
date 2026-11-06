import concurrent.futures
import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

spec=importlib.util.spec_from_file_location('backend',Path(__file__).resolve().parents[1]/'server.py')
backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=backend.Store(Path(self.temp.name)/'test.sqlite')
        result,self.host=self.store.perform('create',{})
        self.room=result['room'];self.judges=[]
    def join(self,name):
        _,token=self.store.perform('join',{'room':self.room,'name':name});self.judges.append(token);return token
    def action(self,action,token=None,**values):
        return self.store.perform(action,{'room':self.room,**values},token or self.host)
    def state(self,token=None):return self.store.state(self.room,token or self.host)
    def test_lobby_totals_and_privacy(self):
        for i in range(18):self.join('Judge '+str(i))
        state=self.state();self.assertEqual(state['maxPoints'],1800)
        self.assertEqual(state['projections'],[1596.42,1615.50]);self.assertGreater(state['chances'][1],50)
        self.assertEqual(len(state['judges']),18)
        anon=self.store.state(self.room);self.assertNotIn('judges',anon);self.assertIsNone(anon['me'])
        self.assertNotIn('token',json.dumps(state))
    def test_empty_start_duplicate_names_and_rejoin(self):
        with self.assertRaises(backend.APIError):self.action('start')
        token=self.join('Alex')
        with self.assertRaises(backend.APIError):self.join('alex')
        self.action('join',token,name='Other name')
        self.assertEqual(self.state()['judgeCount'],1)
    def test_host_cannot_silently_join_as_a_judge(self):
        with self.assertRaises(backend.APIError) as ctx:self.action('join',name='Attempted judge')
        self.assertEqual(ctx.exception.status,409)
        self.assertIn('different browser',ctx.exception.message)
        self.assertEqual(self.state()['judgeCount'],0)
        judge=self.join('Alex')
        self.assertEqual(self.state()['judgeCount'],1)
        self.assertEqual(self.state(judge)['me']['role'],'judge')

    def test_authorization(self):
        judge=self.join('Alex')
        for action in ['start','close','next']:
            with self.assertRaises(backend.APIError) as ctx:self.action(action,judge)
            self.assertEqual(ctx.exception.status,403)
        with self.assertRaises(backend.APIError):self.action('vote',grades=[5]*4,round=0)
        with self.assertRaises(backend.APIError):self.store.perform('start',{'room':self.room},'fake')
    def test_validation_and_round_lock(self):
        judge=self.join('Alex')
        with self.assertRaises(backend.APIError):self.action('vote',judge,grades=[5]*4,round=0)
        self.action('start')
        late=self.join('Late')
        self.assertEqual(self.state(late)['me']['role'],'spectator')
        self.assertEqual(self.state()['judgeCount'],1)
        for grades in [[0,1,2,3],[1,2,3,6],[True,1,2,3],[1,2,3],[1.0,2,3,4],'bad']:
            with self.assertRaises(backend.APIError):self.action('vote',judge,grades=grades,round=0)
        with self.assertRaises(backend.APIError):self.action('vote',judge,grades=[5]*4,round=1)
    def test_live_totals_edits_missing_votes_and_close(self):
        a=self.join('Alex');b=self.join('Sam');self.action('start')
        self.action('vote',a,grades=[5,4,3,5],round=0)
        state=self.state();self.assertEqual(state['totals'],[9,8]);self.assertEqual(state['rounds'][0]['submitted'],1)
        self.assertEqual(state['rounds'][0]['projection'],[17.42,16.74])
        with self.assertRaises(backend.APIError):self.action('close')
        self.action('vote',a,grades=[1,1,5,5],round=0)
        self.assertEqual(self.state()['totals'],[2,10])
        self.action('vote',b,grades=[4]*4,round=0)
        self.assertEqual(self.state()['totals'],[10,18])
        self.assertEqual(self.state(a)['ownVotes'],{'0':[1,1,5,5]})
        self.assertNotIn('ownVotes',self.store.state(self.room))
        self.action('close')
        with self.assertRaises(backend.APIError):self.action('vote',a,grades=[5]*4,round=0)
        self.action('next');self.assertEqual(self.state()['activeRound'],1)
        with self.assertRaises(backend.APIError):self.action('next')
    def test_full_event_and_projection_sums(self):
        judge=self.join('Alex');self.action('start')
        for i in range(10):
            self.action('vote',judge,grades=[5,4,4,4],round=i)
            state=self.state()
            for p in range(2):self.assertAlmostEqual(state['projections'][p],sum(r['projection'][p] for r in state['rounds']))
            self.action('close');self.action('next')
        state=self.state();self.assertEqual(state['phase'],'finished');self.assertEqual(state['totals'],[90,80]);self.assertEqual(state['projections'],[90,80]);self.assertEqual(state['chances'],[100,0])
        with self.assertRaises(backend.APIError):self.action('vote',judge,grades=[5]*4,round=9)
    def test_restart_and_concurrent_submissions(self):
        judges=[self.join('Judge '+str(i)) for i in range(18)];self.action('start')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda token:self.action('vote',token,grades=[5]*4,round=0),judges))
        self.store=backend.Store(self.store.path)
        self.assertEqual(self.state()['totals'],[180,180]);self.assertEqual(self.state()['rounds'][0]['submitted'],18)
        self.assertEqual(self.state(judges[0])['me']['role'],'judge')
        self.action('close')
    def test_duplicate_removal_before_start(self):
        a=self.join('Alex');b=self.join('Alex duplicate')
        bid=self.state(b)['me']['id']
        with self.assertRaises(backend.APIError):self.action('remove-judge',a,member=bid,confirmed=True)
        with self.assertRaises(backend.APIError):self.action('remove-judge',member=bid)
        self.action('remove-judge',member=bid,confirmed=True)
        state=self.state();self.assertEqual(state['judgeCount'],1);self.assertEqual(state['maxPoints'],100)
        self.assertEqual(self.state(b)['me']['role'],'removed')
        self.action('join',b,name='Attempt to return')
        self.assertEqual(self.state(b)['me']['role'],'removed')
        self.action('start')
        with self.assertRaises(backend.APIError):self.action('vote',b,grades=[5]*4,round=0)
        self.assertEqual(len(self.state()['rosterChanges']),1)

    def test_withdraw_removes_history_and_current_votes(self):
        a=self.join('Alex');b=self.join('Sam');self.action('start')
        for round_index in [0,1]:
            self.action('vote',a,grades=[5,5,1,1],round=round_index)
            self.action('vote',b,grades=[2,2,4,4],round=round_index)
            self.action('close');self.action('next')
        self.action('vote',a,grades=[5,5,1,1],round=2)
        aid=self.state(a)['me']['id']
        with self.assertRaises(backend.APIError):self.action('close')
        self.action('withdraw-judge',member=aid,confirmed=True)
        state=self.state()
        self.assertEqual(state['judgeCount'],1);self.assertEqual(state['maxPoints'],100)
        self.assertEqual(state['totals'],[8,16])
        self.assertEqual(state['rounds'][0]['points'],[4,8]);self.assertEqual(state['rounds'][1]['points'],[4,8])
        self.assertEqual(state['rounds'][2]['submitted'],0)
        self.assertEqual(state['projections'],[79.41,87.89])
        for player in range(2):self.assertAlmostEqual(state['projections'][player],sum(r['projection'][player] for r in state['rounds']))
        self.assertEqual(self.state(a)['me']['role'],'withdrawn');self.assertEqual(self.state(a)['ownVotes'],{})
        self.assertEqual(state['rosterChanges'][0]['name'],'Alex')
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM votes WHERE member=?',(aid,)).fetchone()[0],0)
        with self.assertRaises(backend.APIError):self.action('vote',a,grades=[5]*4,round=2)
        with self.assertRaises(backend.APIError):self.action('withdraw-judge',member=aid,confirmed=True)
        self.action('vote',b,grades=[3]*4,round=2);self.action('close')
        self.store=backend.Store(self.store.path)
        self.assertEqual(self.state()['judgeCount'],1);self.assertEqual(self.state(a)['me']['role'],'withdrawn')

    def test_withdraw_last_judge_and_final_results_protected(self):
        a=self.join('Alex');aid=self.state(a)['me']['id'];self.action('start')
        with self.assertRaises(backend.APIError):self.action('withdraw-judge',member=aid,confirmed=True)
        for i in range(10):
            self.action('vote',a,grades=[4]*4,round=i);self.action('close');self.action('next')
        with self.assertRaises(backend.APIError):self.action('withdraw-judge',member=aid,confirmed=True)
        self.assertEqual(self.state()['totals'],[80,80])

    def test_spectators_and_competitors_do_not_change_scores(self):
        daniel=self.join('Daniel');joicee=self.join('Joicee')
        self.assertEqual(self.state()['judgeCount'],0)
        for token in [daniel,joicee]:self.assertEqual(self.state(token)['me']['role'],'spectator')
        judge=self.join('Alex');self.action('start')
        late=self.join('Late friend');before=self.state()
        self.assertEqual(before['judgeCount'],1);self.assertEqual(before['maxPoints'],100)
        self.assertEqual(self.state(late)['me']['role'],'spectator')
        for action in ['start','close','next']:
            with self.assertRaises(backend.APIError):self.action(action,late)
        with self.assertRaises(backend.APIError):self.action('vote',late,grades=[5]*4,round=0)
        self.action('vote',judge,grades=[5]*4,round=0);self.action('close')
        self.assertEqual(self.state(late)['totals'],[10,10])
        self.assertEqual(self.state()['projections'],self.state(late)['projections'])
        self.action('join',judge,name='New name')
        self.assertEqual(self.state(judge)['ownVotes'],{'0':[5,5,5,5]})

    def test_roster_changes_cannot_target_another_room(self):
        a=self.join('Alex');self.join('Sam');self.action('start')
        other,other_host=self.store.perform('create',{})
        _,other_judge=self.store.perform('join',{'room':other['room'],'name':'Other judge'})
        other_id=self.store.state(other['room'],other_judge)['me']['id']
        with self.assertRaises(backend.APIError):self.action('withdraw-judge',member=other_id,confirmed=True)
        with self.assertRaises(backend.APIError):self.action('withdraw-judge',a,member=self.state(a)['me']['id'],confirmed=True)
        self.assertEqual(self.state()['judgeCount'],2)

    def test_reset_is_host_only_confirmed_and_scoped(self):
        judge=self.join('Alex');self.join('Sam');self.action('start')
        self.action('vote',judge,grades=[5]*4,round=0)
        other,other_host=self.store.perform('create',{})
        for token in [judge,'invalid']:
            with self.assertRaises(backend.APIError):self.action('reset',token,confirmed=True,generation=0)
        with self.assertRaises(backend.APIError):self.action('reset',generation=0)
        with self.assertRaises(backend.APIError):self.action('reset',confirmed=True,generation=99)
        self.assertEqual(self.state()['totals'],[10,10])
        self.action('reset',confirmed=True,generation=0)
        state=self.state();self.assertEqual(state['phase'],'waiting');self.assertEqual(state['activeRound'],0);self.assertFalse(state['votingOpen']);self.assertEqual(state['generation'],1)
        self.assertEqual(state['judgeCount'],0);self.assertEqual(state['totals'],[0,0]);self.assertEqual(state['projections'],[0,0]);self.assertEqual(state['rosterChanges'],[]);self.assertEqual(state['me']['role'],'host')
        self.assertIsNone(self.store.state(self.room,judge)['me'])
        self.assertEqual(self.store.state(other['room'],other_host)['me']['role'],'host')
        with self.assertRaises(backend.APIError):self.action('vote',judge,grades=[5]*4,round=0)
        rejoined=self.join('Alex');self.assertNotEqual(judge,rejoined)
        with self.assertRaises(backend.APIError):self.action('reset',confirmed=True,generation=0)
        self.assertEqual(self.state()['judgeCount'],1)
        self.store=backend.Store(self.store.path);self.assertEqual(self.state()['generation'],1)

    def test_reset_clears_roster_history(self):
        a=self.join('Alex');b=self.join('Sam');bid=self.state(b)['me']['id']
        self.action('remove-judge',member=bid,confirmed=True)
        self.assertEqual(len(self.state()['rosterChanges']),1)
        self.action('reset',confirmed=True,generation=0)
        self.assertEqual(self.state()['rosterChanges'],[])
        self.assertEqual(self.state()['judgeCount'],0)

    def http_post(self,action,data,token='',origin='http://localhost:8040',content_type='application/json'):
        handler=backend.Handler.__new__(backend.Handler);handler.server=SimpleNamespace(store=self.store,secure_cookies=False)
        handler.headers=Message();handler.headers['Host']='localhost:8040';handler.headers['Origin']=origin;handler.headers['Content-Type']=content_type
        if token:handler.headers['Cookie']='wing_session='+token
        raw=json.dumps(data).encode();handler.headers['Content-Length']=str(len(raw));handler.rfile=io.BytesIO(raw);handler.wfile=io.BytesIO();handler.path='/api/'+action;handler.request_version='HTTP/1.1';handler.requestline='POST '+handler.path+' HTTP/1.1';handler.command='POST';handler.log_message=lambda *a:None
        handler.do_POST();return handler.wfile.getvalue().decode()
    def test_http_security_and_cookie(self):
        self.assertIn('403',self.http_post('create',{},origin='http://evil.example'))
        self.assertIn('415',self.http_post('create',{},content_type='text/plain'))
        response=self.http_post('join',{'room':self.room,'name':'Alex'})
        self.assertIn('200 OK',response);self.assertIn('HttpOnly',response);self.assertIn('SameSite=Lax',response)
        self.assertIn('403',self.http_post('start',{'room':self.room},token=self.join('Sam')))

if __name__=='__main__':unittest.main()
