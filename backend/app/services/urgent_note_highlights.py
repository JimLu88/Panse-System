"""Display-only note signals; never replace a confirmed shipping deadline."""
import re
from datetime import date

URGENT = re.compile(r"非常紧急|特别急|十万火急|加急|紧急|赶工|尽早发|尽快发|早点发")
TIME = re.compile(r"[一二三四五六七八九十两\d]+(?:个工作日|个?天)|今天|明天|后天|本周|下周|月底|月初|\d{1,2}[日号](?:前|发|到)")
NEGATIVE = re.compile(r"取消|不(?:用|需|必|再|着)?急|无需|不用|不必|不再|作废")
QUESTION = re.compile(r"能否|能不能|是否|可以吗|[?？]")
CALENDAR = re.compile(r"(?<![\d.])(?:(20\d{2})[-/.年])?(\d{1,2})([-/.月])(\d{1,2})(?:日|号)?(?![\d.])")
DEADLINE = re.compile(r"前|截止|最晚|最迟|交期|工期|发货|发出|发$|到货|送达|送到|做好|做完|制作完成|生产完成")


def calendar_requirements(clause):
    for match in CALENDAR.finditer(clause):
        try:
            due = date(int(match[1] or 2000), int(match[2]), int(match[4]))
        except ValueError:
            continue
        # Decimal furniture dimensions (1.8米 / 2.2*0.9) are not dates.
        if not match[1] and match[3] == "." and not re.match(r"[日号]|前", clause[match.end():]):
            continue
        # Audit timestamps such as "2026-11-25 用户核定配件" are not deadlines.
        if not DEADLINE.search(clause) and clause.strip() != match[0] and match[3] != "月":
            continue
        yield due if match[1] else None


def note_signal(notes, today):
    """Negated requests do not alert. Unanchored relative dates stay yellow."""
    level, excerpts = "", []
    for note in notes:
        for clause in re.split(r"[，,。；;\n]|但是|不过", note or ""):
            clause = clause.strip()
            if not clause or NEGATIVE.search(clause):
                continue
            calendar = list(calendar_requirements(clause))
            urgent, timed = URGENT.search(clause), TIME.search(clause) or bool(calendar)
            if not urgent and not timed:
                continue
            color = "yellow"
            if urgent and not QUESTION.search(clause):
                color = "red"
            for due in calendar:
                # A yearless note stays yellow instead of guessing its year.
                if due and (due - today).days <= 5 and not QUESTION.search(clause):
                    color = "red"
            if color == "red" or not level:
                level = color
            if clause not in excerpts:
                excerpts.append(clause)
    return level, "；".join(excerpts)
