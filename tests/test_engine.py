"""Opt-in real stdio integration. Skipped unless a locally provisioned engine is supplied."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

import adapter
from demo.fixture import reflection

ENGINE = os.environ.get('REVERIE_TEST_ENGINE')

@unittest.skipUnless(ENGINE, 'Set REVERIE_TEST_ENGINE to the pinned local v0.3.0 executable; no engine was provisioned by this suite.')
class EngineIntegrationTests(unittest.TestCase):
    def test_dated_source_and_string_reflection(self):
        with tempfile.TemporaryDirectory() as data:
            bridge = adapter.Bridge(ENGINE,data,timeout=20)
            self.addCleanup(bridge.close)
            messages = [{'id':'p','role':'user','text':'Synthetic compass at the harbor.'},
                        {'id':'r','role':'assistant','text':'Synthetic completed MAIN_BRANCH reply.'}]
            def run(method,params):
                job = bridge.get(bridge.start({'character':'synthetic','method':method,'params':params})['job'])
                deadline = time.monotonic()+25
                while time.monotonic()<deadline:
                    state = job.snapshot()
                    if state['state']=='done':
                        self.assertNotIn('error',state)
                        job.thread.join(2)
                        return state['result']
                    if state['state']=='callback':
                        frame = state['callback']
                        if frame['method']=='source.read':
                            answer = {'foundation':'Synthetic private continuity.','messages':messages,'recallHistory':[]}
                        else:
                            self.assertEqual(frame['params']['modelKey'],'reverie-reflection')
                            answer = reflection(frame['params']['request'])
                            self.assertIsInstance(answer,str)
                        job.reply({'callId':frame['callId'],'result':answer})
                    time.sleep(.01)
                self.fail('Real engine did not finish')
            run('ping',{})
            dated = run('when',{'episodeId':'synthetic-episode','position':'2024-06-01'})
            self.assertIsInstance(dated['now'],(int,float))
            formed = run('memorize',{'episodeId':'synthetic-episode','modelKey':'reverie-reflection'})
            self.assertEqual(formed.get('status'),'published')
            self.assertEqual(run('memorize',{'episodeId':'synthetic-episode','modelKey':'reverie-reflection'})['status'],'current')
            recall = run('retrieve',{'episodeId':'later-synthetic','at':dated['now']+86400000,'cue':'compass harbor','recallHistory':[]})
            self.assertIsInstance(recall['memories'],list)
