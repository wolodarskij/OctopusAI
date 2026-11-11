"""nightmode.py - from 00:00 to 08:00 local time no ticket waits for the owner's input.

The rule is `core/rules/night-mode.md`. In night mode:

    the question guard (`night_guard.py`)   refuses `AskUserQuestion` in a ticket session
    `todo.py report <id> input`             refuses
    the scan of the sessions                types `NUDGE` into a ticket session whose state
                                            is `input` (`todo.night_nudge`)
    the `Stop` hold that asks the owner     gives the night order instead
    the manager's update                    leaves out the questions for the morning

A session decides in place of the owner and records each decision with `decide`, or collects
a question that only the owner can answer with `morning`, and goes on with the work.
"""

from __future__ import annotations

import datetime

# The night: from START o'clock up to, not including, END o'clock, local time.
START, END = 0, 8
# The section of a ticket that records each decision a session took in place of the owner.
DECISIONS = "Decisions in night mode"
# The section of a ticket that collects the questions that only the owner can answer.
MORNING = "Questions for the morning"
# The most times a night that the scan types the night order into one session.
NUDGES = 3


def is_night(now=None):
    """Whether `now` (a datetime, a timestamp, or None for now) is in night mode."""
    if now is None:
        now = datetime.datetime.now()
    elif not isinstance(now, datetime.datetime):
        now = datetime.datetime.fromtimestamp(float(now))
    return START <= now.hour < END


def night_of(now=None):
    """The date of the night that `now` belongs to, as `YYYY-MM-DD`: the date its 00:00 falls on."""
    if now is None:
        now = datetime.datetime.now()
    elif not isinstance(now, datetime.datetime):
        now = datetime.datetime.fromtimestamp(float(now))
    return now.strftime("%Y-%m-%d")


def window():
    """`00:00 to 08:00`."""
    return "%02d:00 to %02d:00" % (START, END)


def order(cli, tid, mode):
    """What a session does in night mode instead of a question to the owner."""
    return (
        "Night mode, %(window)s local time: the owner gives no answer now, and no ticket waits "
        "for the owner's input. Decide the question yourself from the rules, the ticket and the "
        "evidence, record it with `%(cli)s decide %(id)s --question \"<the question>\" "
        "--decision \"<what you decided, and why>\"`, and go on with the work. A question that "
        "only the owner can answer: `%(cli)s morning %(id)s --question \"<the question>\"`, then "
        "do all the work that does not need the answer. When no such work is left: "
        "`%(cli)s report %(id)s event --why \"the owner's answer in the morning: <the question>\" "
        "--mode %(mode)s`, and end the turn with no question."
        % {"window": window(), "cli": cli, "id": tid, "mode": mode})


def nudge(cli, tid, mode):
    """The one line the scan types into a ticket session that waits for the owner's input."""
    return " ".join(order(cli, tid, mode).split())
