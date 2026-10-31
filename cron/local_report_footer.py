"""Format existing cron report footers without changing special report policy."""
from datetime import datetime
import re

_DURATION = re.compile(r'^\s*⚡\s*执行耗时\s*[:：]')
_TIME = re.compile(r'^\s*🕘\s*(?:上报时间|投递时间)\s*[:：]')


def replace_report_footer(text, elapsed, when=None):
    if not text or text.strip() == '[SILENT]':
        return text
    lines = text.rstrip('\n').split('\n')
    end = len(lines)
    while end and (not lines[end - 1].strip() or _TIME.match(lines[end - 1])):
        end -= 1
    if not end or not _DURATION.match(lines[end - 1]):
        return text
    body = '\n'.join(lines[:end - 1]).rstrip('\n')
    if not body:
        return text
    seconds = max(0.0, float(elapsed))
    duration = f'{max(1, round(seconds * 1000))} 毫秒' if seconds < 1 else f'{seconds:.3f} 秒'
    when = when or datetime.now().astimezone()
    return f'{body}\n\n⚡ 执行耗时：{duration}\n🕘 上报时间：{when:%Y-%m-%d %H:%M:%S}'
