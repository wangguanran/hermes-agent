import unittest
from datetime import datetime
from cron.local_report_footer import replace_report_footer


class TestLocalReportFooter(unittest.TestCase):
    def test_replaces_existing_footer(self):
        result = replace_report_footer('报告\n⚡ 执行耗时：旧\n🕘 上报时间：旧', 1.234, datetime(2026, 10, 29, 11, 0))
        self.assertEqual(result, '报告\n\n⚡ 执行耗时：1.234 秒\n🕘 上报时间：2026-10-29 11:00:00')

    def test_special_and_silent_untouched(self):
        for s in ('[SILENT]', '', '不显示页脚的专用报告'):
            self.assertEqual(replace_report_footer(s, 0.2), s)

    def test_body_mention_not_a_footer(self):
        s = '报告\n⚡ 执行耗时：这是业务字段\n后续正文'
        self.assertEqual(replace_report_footer(s, .5), s)

    def test_millisecond_time(self):
        result = replace_report_footer('报告\n⚡ 执行耗时：旧', .5, datetime(2026, 10, 29))
        self.assertIn('⚡ 执行耗时：500 毫秒\n🕘', result)

if __name__ == '__main__':
    unittest.main()
