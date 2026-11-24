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
            provider_online = True
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
                        self.assertNotEqual(method, 'forget', 'forget must never request callbacks')
                        frame = state['callback']
                        if frame['method']=='source.read':
                            answer = {'foundation':'Synthetic private continuity.','messages':messages,'recallHistory':[]}
                        else:
                            self.assertTrue(provider_online, 'reflection provider is offline')
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

            # Later contributions may contain derived impressions/corrections.
            for episode_id, day in (('removed-scene','2024-06-02'), ('derived-scene','2024-06-03')):
                run('when', {'episodeId':episode_id,'position':day})
                self.assertEqual(run('memorize', {'episodeId':episode_id,'modelKey':'reverie-reflection'})['status'], 'published')
            provider_online = False
            forgotten = run('forget', {'episodeId':'removed-scene'})
            self.assertEqual(forgotten['episodeId'], 'removed-scene')
            self.assertEqual(forgotten['invalidatedEpisodes'], 2)
            episodes = {x['id']:x for x in run('status', {})['episodes']}
            self.assertNotIn('removed-scene', episodes)
            self.assertIsNone(episodes['derived-scene']['published_end'])
            self.assertIsNotNone(episodes['synthetic-episode']['published_end'])
            self.assertEqual(run('forget', {'episodeId':'removed-scene'}),
                             {'episodeId':'removed-scene','invalidatedEpisodes':0})
            recall = run('retrieve', {'episodeId':'independent-scene','at':dated['now']+4*86400000,
                'cue':'compass harbor','recallHistory':[]})
            self.assertTrue(all(m['episodeId'] not in ('removed-scene','derived-scene') for m in recall['memories']))
            self.assertIn('synthetic-episode', {m['episodeId'] for m in recall['memories']})
            # Recalling the surviving contribution works offline; new formation
            # still uses the normal provider path.
            provider_online = True
            self.assertEqual(run('memorize', {'episodeId':'derived-scene','modelKey':'reverie-reflection'})['status'], 'published')
