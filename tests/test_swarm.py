import calendar
import time

import pytest

from atlas.agents import Ctx
from atlas.agents.intake import Intake
from atlas.config import Config
from atlas.models import Inbound, Stage
from atlas.phone import to_e164
from atlas.store import Store
from atlas.swarm import Swarm

DAY = 86400
# 10:00 in Penang (UTC+8) -- inside sending hours.
T0 = calendar.timegm((2026, 10, 5, 2, 0, 0))


class FakeChannel:
    def __init__(self):
        self.sent = []
        self.fail = False

    def send(self, phone, text, typing_ms=0):
        if self.fail:
            raise ConnectionError("relay down")
        self.sent.append((phone, text))


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    store, ch, clock = Store(), FakeChannel(), Clock()
    # Pacing off: these tests are about the guard and the pipeline, not the clock.
    cfg = Config(daily_cap=2, human_pacing=False)
    return store, ch, clock, Swarm(store, ch, cfg, None, clock)


def test_phone_national_gets_country_code_and_international_is_untouched():
    assert to_e164("012-345 6789", "MY") == "+60123456789"
    assert to_e164("+447450225230", "MY") == "+447450225230"
    assert to_e164("0012025550123", "MY") == "+12025550123"
    assert to_e164("abc") is None
    assert to_e164("0123", "XX") is None


def test_first_message_carries_opt_out_and_stage_advances(env):
    store, ch, clock, swarm = env
    lid = store.add_lead("Aisha", "+60123456789")
    swarm.tick()
    assert len(ch.sent) == 1
    assert "STOP" in ch.sent[0][1]
    assert store.lead(lid).stage is Stage.CONTACTED


def test_followups_wait_for_their_gap_then_stop_after_two(env):
    store, ch, clock, swarm = env
    lid = store.add_lead("Aisha", "+60123456789")
    swarm.tick()
    clock.t += 1 * DAY
    swarm.tick()
    assert len(ch.sent) == 1                    # too early
    clock.t += 2 * DAY                          # 2 days + up to 1 day of per-lead jitter
    swarm.tick()
    assert store.lead(lid).stage is Stage.FOLLOWUP_1
    clock.t += 6 * DAY
    swarm.tick()
    assert store.lead(lid).stage is Stage.FOLLOWUP_2
    clock.t += 30 * DAY
    swarm.tick()
    assert len(ch.sent) == 3                    # swarm gives up politely


def test_reply_cancels_a_queued_followup(env):
    store, ch, clock, swarm = env
    lid = store.add_lead("Aisha", "+60123456789")
    swarm.tick()
    clock.t += 3 * DAY
    # Draft is written but the lead answers before it is sent.
    store.add_inbound(Inbound(lid, "Hi, how much?", clock.t))
    swarm.tick()
    assert store.lead(lid).stage is Stage.REPLIED
    assert len(ch.sent) == 1


def test_stop_is_final(env):
    store, ch, clock, swarm = env
    lid = store.add_lead("Aisha", "+60123456789")
    swarm.tick()
    store.add_inbound(Inbound(lid, "Please STOP", clock.t))
    clock.t += 10 * DAY
    swarm.tick()
    assert store.lead(lid).stage is Stage.DO_NOT_CONTACT
    assert len(ch.sent) == 1


def test_daily_cap_and_quiet_hours(env):
    store, ch, clock, swarm = env
    for i in range(3):
        store.add_lead(f"L{i}", f"+6012345678{i}")
    swarm.tick()
    assert len(ch.sent) == 2                    # cap is 2
    clock.t += 14 * 3600                        # 00:00 next day, Penang time
    swarm.tick()
    assert len(ch.sent) == 2                    # quiet hours
    clock.t += 9 * 3600                         # 09:00
    swarm.tick()
    assert len(ch.sent) == 3


def test_failed_send_leaves_lead_untouched_and_retries(env):
    store, ch, clock, swarm = env
    lid = store.add_lead("Aisha", "+60123456789")
    ch.fail = True
    swarm.tick()
    assert store.lead(lid).stage is Stage.NEW
    ch.fail = False
    swarm.tick()
    assert store.lead(lid).stage is Stage.CONTACTED
    assert len(ch.sent) == 1


def test_a_crashing_agent_does_not_stop_the_others(env):
    store, ch, clock, swarm = env
    store.add_lead("Aisha", "+60123456789")
    swarm.agents[0].run = lambda ctx: 1 / 0
    reports = swarm.tick()
    assert "crashed:ZeroDivisionError" in reports[0].skipped
    assert len(ch.sent) == 1


def test_intake_skips_bad_and_duplicate_numbers(tmp_path):
    f = tmp_path / "l.csv"
    f.write_text("name,phone,company\nA,012-3456789,X\nA again,0123456789,X\nB,nope,Y\n")
    r = Intake().run(Ctx(Store(), Config(), None, time.time), str(f))
    assert r.done == 1 and r.skipped == {"duplicate": 1, "bad_phone": 1}
