"""Offline checks for the report-only pilot; these do not execute Docker."""
import copy
import hashlib
import json
import unittest

from compatibility import pilot


def evidence():
    return {'chats': {
        'ordinary': {'chat': 'ordinary', 'reply': 'o', 'completed': True, 'folder': None, 'record': False},
        'source': {'chat': 'source', 'reply': 's', 'completed': True, 'folder': 'folder',
                   'record': True, 'published': True, 'at': 1717200000000,
                   'memorySources': [], 'reflectionHash': 'saved-source-hash'},
        'later': {'chat': 'later', 'reply': 'l', 'completed': True, 'folder': 'folder',
                  'record': True, 'at': 1719792000000, 'memorySources': ['source']}},
        'fixture': [{'kind': 'reply', 'promptHash': hashlib.sha256(prompt.encode()).hexdigest(),
                     'hasCompassRecall': key == 'later', 'hasUncertainRecall': key == 'later',
                     'hasInspectorCopy': False} for key, prompt in pilot.PROMPTS.items()] + [
                         {'kind': 'reflection', 'sourceHash': 'saved-source-hash',
                          'messageIds': ['m1', 'm2'], 'reasoningOffRequested': True}]}


class PilotTests(unittest.TestCase):
    def test_complete_evidence_passes(self):
        self.assertEqual(pilot.evaluate(evidence())[0], 'tested/pass')

    def test_missing_evidence_never_passes(self):
        baseline = evidence()
        for key in baseline['chats']:
            value = copy.deepcopy(baseline)
            del value['chats'][key]
            self.assertEqual(pilot.evaluate(value)[0], 'indeterminate')
        for field in ('published', 'record', 'reflectionHash'):
            value = copy.deepcopy(baseline)
            del value['chats']['source'][field]
            self.assertEqual(pilot.evaluate(value)[0], 'indeterminate')
        for index in range(len(baseline['fixture'])):
            value = copy.deepcopy(baseline)
            del value['fixture'][index]
            self.assertEqual(pilot.evaluate(value)[0], 'indeterminate')

    def test_provider_reply_alone_cannot_establish_saved_completion(self):
        value = evidence()
        value['chats']['later']['completed'] = False
        self.assertEqual(pilot.evaluate(value)[0], 'indeterminate')

    def test_wrong_date_or_folder_is_incomplete_test(self):
        for field, wrong in (('at', 0), ('folder', 'other')):
            value = evidence()
            value['chats']['later'][field] = wrong
            self.assertEqual(pilot.evaluate(value)[0], 'indeterminate')

    def test_bound_completed_recall_mismatch_is_incompatible(self):
        for change in ('missing-memory', 'missing-provider-recall', 'missing-uncertainty', 'inspector-leaked'):
            value = evidence()
            if change == 'missing-memory':
                value['chats']['later']['memorySources'] = []
            elif change == 'missing-provider-recall':
                value['fixture'][2]['hasCompassRecall'] = False
            elif change == 'missing-uncertainty':
                value['fixture'][2]['hasUncertainRecall'] = False
            else:
                value['fixture'][2]['hasInspectorCopy'] = True
            self.assertEqual(pilot.evaluate(value)[0], 'incompatible', change)

    def test_ordinary_contamination_is_incompatible(self):
        value = evidence()
        value['fixture'][0]['hasCompassRecall'] = True
        self.assertEqual(pilot.evaluate(value)[0], 'incompatible')

    def test_reflection_from_other_branch_does_not_count(self):
        for key, wrong in (('sourceHash', 'other'), ('messageIds', ['m1']), ('reasoningOffRequested', False)):
            value = evidence()
            value['fixture'][-1][key] = wrong
            self.assertEqual(pilot.evaluate(value)[0], 'indeterminate')

    def test_manifest_has_only_scoped_resources_and_pinned_artifacts(self):
        manifest = pilot.compose()
        self.assertEqual(manifest['volumes'], {'ui_data': {}, 'engine_data': {}})
        self.assertEqual(manifest['networks'], {'private': {'internal': True}})
        for service in manifest['services'].values():
            self.assertEqual(service['platform'], 'linux/amd64')
            self.assertIn(service['image'], (pilot.UI, pilot.ADAPTER))
            self.assertEqual(service['pull_policy'], 'never')
            self.assertEqual(service['networks'], ['private'])
            self.assertNotIn('build', service)
            self.assertNotIn('env_file', service)
            self.assertNotIn('docker.sock', json.dumps(service))
            self.assertNotIn('container_name', service)
            self.assertNotIn('privileged', service)
            self.assertNotIn('ports', service)
        self.assertEqual(manifest['services']['adapter']['volumes'], ['engine_data:/data'])

    def test_browser_url_is_bound_to_the_disposable_ui_network(self):
        key = 'reverie-pilot-test_private'
        metadata = {'NetworkSettings': {'Networks': {key: {'IPAddress': '172.18.0.4'}}}}
        self.assertEqual(pilot.ui_url(metadata, 'reverie-pilot-test'), 'http://172.18.0.4:8080')
        for address in ('', '8.8.8.8', '127.0.0.1', '172.18.0.4/path'):
            metadata['NetworkSettings']['Networks'][key]['IPAddress'] = address
            with self.assertRaises(ValueError):
                pilot.ui_url(metadata, 'reverie-pilot-test')
        with self.assertRaises(ValueError):
            pilot.ui_url(metadata, 'a-different-project')

    def test_docker_environment_does_not_inherit_host_secrets(self):
        from pathlib import Path
        docker = pilot.Docker(Path('/tmp/empty-config'), 'project', Path('/tmp/compose.json'))
        self.assertEqual(set(docker.env), {'PATH', 'HOME'})
        self.assertEqual(docker.base[1:], ['--config', '/tmp/empty-config', '--context', 'default'])


if __name__ == '__main__':
    unittest.main()
