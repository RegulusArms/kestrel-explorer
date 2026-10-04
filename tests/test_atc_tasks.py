"""The shared task list: every window's status bar shows the operations running in other windows and Kestrels."""
import os
import signal
import subprocess
import sys
import time

from common import A, ROOT, FakeFlight, check, finish, setup_app, spin, start_radio, wait_for

from kestrel import atc, fileops


def label(w):
    return w.task_panel.label.text()


app = setup_app()
home = os.path.expanduser("~")

# a tower and another Kestrel with a running (admin) task are already there
subprocess.Popen([sys.executable, os.path.join(ROOT, "kes"), "--atc"], start_new_session=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fake = FakeFlight()
check(wait_for(lambda: fake.tower_pid() != 0, 8000), "tower up")
fake.check_in()
fake.report({"type": "tasks", "keep": True, "tasks": [{"id": "9", "title": "Copying", "text": "3 of 7", "fraction": 0.5,
                                                         "cancellable": True, "cancelling": False, "admin": True}]})

start_radio()
w = A.open_window([home])
check(wait_for(lambda: "Copying (in another window): 3 of 7" in label(w)), "a new window shows another Kestrel's running task")
me = atc.radio().flight()
check("🛡" in label(w), "...marked as an admin task")
check(w.task_panel.isVisible() and "more" not in label(w), "...on its own: " + label(w))
w.task_panel.stop.click()
check(wait_for(lambda: any(f == me and m.get("type") == "cancel" and m.get("flight") == fake.name() and m.get("task") == "9"
                           for f, m in fake.heard)), "✕ asks the other Kestrel to cancel it")
check("cancelling" in label(w), "...and shows it cancelling")

state = {"cancelled": False}


def work(t):
    i = 0
    while True:
        t.report(i % 1000, 1000, "working")
        t.check()
        time.sleep(0.005)
        i += 1


job = fileops.run_job(w, "Test job", work, lambda _r: state.__setitem__("cancelled", job.was_cancelled))
jid = job.id
check(wait_for(lambda: fake.heard and fake.heard[-1][0] == me and fake.last_of("tasks").get("keep")
               and (fake.last_of("tasks").get("tasks") or [{}])[0].get("title") == "Test job"),
      "a task here is reported (kept state)")
check(wait_for(lambda: label(w).startswith("Test job") and "+1 in other windows" in label(w)),
      "this window's task comes first, the other is counted: " + label(w))
before = fake.count_type("tasks", me)
spin(2000)
n = fake.count_type("tasks", me) - before
check(2 <= n <= 6, f"progress reports are throttled ({n} in 2 s)")
w2 = A.open_window([home])
check(wait_for(lambda: "Test job (in another window)" in label(w2) and "+1 in other windows" in label(w2)),
      "a second window shows the first window's task")
fake.report({"type": "cancel", "flight": me, "task": jid})
check(wait_for(lambda: state["cancelled"]), "another Kestrel's ✕ cancels our task")
check(wait_for(lambda: fake.heard[-1][0] == me and fake.last_of("tasks").get("tasks") == []),
      "...and the empty list is reported")
check(wait_for(lambda: label(w).startswith("🛡 Copying (in another window)")), "back to showing only theirs")
fake.disconnect_bus()
check(wait_for(lambda: not w.task_panel.isVisible() and not w2.task_panel.isVisible()),
      "a Kestrel that leaves takes its tasks with it")

# tower restart: our kept state is sent to the new tower
fake2 = FakeFlight()


def loop(t):
    while True:
        t.check()
        time.sleep(0.02)


job2 = fileops.run_job(w, "Second job", loop)
job2.admin = True
spin(300)
os.kill(fake2.tower_pid(), signal.SIGKILL)
spin(300)
check(wait_for(lambda: "Second job" in (fake2.call("Kept") or "") and '"admin":true' in (fake2.call("Kept") or ""), 8000),
      "a new tower gets our running tasks again")
job2.cancel()
spin(300)
finish()
