import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cloud', Path(__file__).with_name('cloud-feishu.py'))
cloud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cloud)


class CalendarTests(unittest.TestCase):
    def test_empty_snapshot_is_complete(self):
        with patch.object(cloud.caldav, 'discover_calendars', return_value=[{'name': '测试日历', 'href': '/test/'}]), patch.object(cloud.caldav, 'fetch_ics_texts', return_value=([], 'empty')):
            result = cloud.collect('test', 'test')
        self.assertTrue(result['meta']['complete'])
        self.assertEqual(result['events'], [])

    def test_failure_never_becomes_empty_snapshot(self):
        with patch.object(cloud.caldav, 'discover_calendars', return_value=[{'name': '测试日历', 'href': '/test/'}]), patch.object(cloud.caldav, 'fetch_ics_texts', side_effect=RuntimeError('network')):
            with self.assertRaises(RuntimeError):
                cloud.collect('test', 'test')

    def test_incomplete_batch_is_rejected(self):
        with patch.object(cloud.caldav, 'request', return_value=(207, 'xml')), patch.object(cloud.caldav, 'extract_calendar_data', return_value=['one']):
            with self.assertRaises(RuntimeError):
                cloud.strict_multiget('https://caldav.feishu.cn', ('test', 'test'), 'https://caldav.feishu.cn/test/', ['/a.ics', '/b.ics'])

    def test_other_hosts_are_rejected(self):
        with self.assertRaises(ValueError):
            cloud.safe_request('GET', 'https://example.com/secret')


if __name__ == '__main__':
    unittest.main()
