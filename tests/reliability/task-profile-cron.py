#!/usr/bin/env python3
"""Exercise pinned Hermes' multiplex profile cron execution path."""

import os
import sys
import tempfile
from pathlib import Path


HERMES = Path(os.environ.get("OPEN_CLOUD_HERMES_ROOT", Path.home() / ".hermes/hermes-agent"))


class OneCycleStop:
    def __init__(self): self.stopped = False
    def is_set(self): return self.stopped
    def wait(self, _interval): self.stopped = True; return True


def main():
    sys.path.insert(0, str(HERMES))
    from cron.scheduler_provider import InProcessCronScheduler
    import cron.jobs as jobs
    import cron.scheduler as scheduler
    from hermes_constants import get_hermes_home

    calls = []
    scheduler.tick = lambda **kwargs: calls.append(Path(get_hermes_home()).resolve())
    jobs.record_ticker_heartbeat = lambda *args, **kwargs: None
    jobs.clear_ticker_error = lambda *args, **kwargs: None
    jobs.record_ticker_error = lambda *args, **kwargs: None
    provider = InProcessCronScheduler()
    provider.recover_interrupted = lambda: 0

    with tempfile.TemporaryDirectory() as tmp:
        homes = [("example-one", Path(tmp) / "profiles/example-one"),
                 ("example-two", Path(tmp) / "profiles/example-two")]
        for _, home in homes: home.mkdir(parents=True)
        provider.start(OneCycleStop(), profile_homes=homes, interval=0)
        assert calls == [home.resolve() for _, home in homes]

    source = (HERMES / "gateway/run.py").read_text()
    assert 'cron_start_kwargs["profile_homes"] = profile_homes' in source
    print("PASS default multiplex gateway passes every served profile to cron")
    print("PASS each profile tick runs under its own HERMES_HOME")
    print("TASK_PROFILE_CRON_RELIABILITY: PASS")


if __name__ == "__main__":
    main()
