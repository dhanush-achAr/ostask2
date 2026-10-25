"""
Readers-Writers problem: monitor-based solution (writer priority).

A monitor = one lock + condition variables + the shared state they protect.
Only one thread can be inside the monitor's methods at a time (the lock),
and a thread that cannot continue calls wait() on a condition variable, which
releases the lock and sleeps until another thread calls notify().

Monitor state
    active_readers   : number of readers currently reading
    writer_active    : True while a writer is writing
    waiting_writers  : number of writers that asked for access but are not in yet

Monitor objects
    lock            : threading.Lock         (mutual exclusion inside the monitor)
    can_read        : Condition(lock)        (readers sleep here)
    can_write       : Condition(lock)        (writers sleep here)

Policy: WRITER PRIORITY. A new reader waits if a writer is active OR waiting.
        This stops writers from starving, but readers can starve if writers
        keep arriving. See the report for the discussion.

Every state change is stored in an EventRecorder; verify_trace() checks the
rules and the same events are embedded in the generated HTML.

Run:
    python readers_writers_monitor.py
    python readers_writers_monitor.py --runs 5 --fast
"""

import argparse
import json
import os
import random
import sys
import threading
import time
from datetime import datetime

READER_NAMES = ["R1", "R2", "R3", "R4"]
WRITER_NAMES = ["W1", "W2"]
ROUNDS_PER_THREAD = 2


# ---------------------------------------------------------------------------
# Event recording
# ---------------------------------------------------------------------------
class EventRecorder:
    """Thread-safe list of events (its own lock gives unique sequence numbers)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events = []
        self._start_time = time.perf_counter()

    def record(self, thread_name, role, action, detail=""):
        with self._lock:
            event = {
                "seq": len(self._events) + 1,
                "time": round(time.perf_counter() - self._start_time, 4),
                "thread": thread_name,
                "role": role,          # "reader" or "writer"
                "action": action,      # WAITING, ENTER, EXIT, DONE, ERROR
                "detail": detail,
            }
            self._events.append(event)

    def events(self):
        with self._lock:
            return list(self._events)


# ---------------------------------------------------------------------------
# The shared resource
# ---------------------------------------------------------------------------
class SharedResource:
    """A counter. write() is a read-modify-write with a sleep in the middle, so
    overlapping writers would lose an update and the final value would be wrong."""

    def __init__(self):
        self.value = 0

    def read(self, scale):
        value = self.value
        time.sleep(random.uniform(0.2, 0.5) * scale)
        return value

    def write(self, scale):
        old_value = self.value
        time.sleep(random.uniform(0.2, 0.5) * scale)
        self.value = old_value + 1
        return self.value


# ---------------------------------------------------------------------------
# The monitor
# ---------------------------------------------------------------------------
class ReadersWriterMonitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.can_read = threading.Condition(self.lock)
        self.can_write = threading.Condition(self.lock)
        self.active_readers = 0
        self.writer_active = False
        self.waiting_writers = 0

    def start_read(self):
        with self.lock:
            # while (not if): after waking up we must test the condition again,
            # because another thread may have changed the state in between.
            while self.writer_active or self.waiting_writers > 0:
                self.can_read.wait()
            self.active_readers += 1

    def end_read(self):
        with self.lock:
            self.active_readers -= 1
            if self.active_readers == 0:
                self.can_write.notify()          # last reader out: wake one writer

    def start_write(self):
        with self.lock:
            self.waiting_writers += 1
            while self.writer_active or self.active_readers > 0:
                self.can_write.wait()
            self.waiting_writers -= 1
            self.writer_active = True

    def end_write(self):
        with self.lock:
            self.writer_active = False
            if self.waiting_writers > 0:
                self.can_write.notify()          # writer priority: next writer first
            else:
                self.can_read.notify_all()       # nobody is waiting to write: free all readers


# ---------------------------------------------------------------------------
# Thread bodies
# ---------------------------------------------------------------------------
def reader_task(name, monitor, resource, recorder, rounds, scale):
    try:
        for _ in range(rounds):
            time.sleep(random.uniform(0.05, 0.4) * scale)          # "thinking"
            recorder.record(name, "reader", "WAITING", "requesting read access")
            monitor.start_read()
            try:
                recorder.record(name, "reader", "ENTER", "entered critical section (reading)")
                value = resource.read(scale)
                # EXIT is recorded BEFORE end_read(), so log order equals real order.
                recorder.record(name, "reader", "EXIT", f"finished reading, value = {value}")
            finally:
                monitor.end_read()
        recorder.record(name, "reader", "DONE", "completed all rounds")
    except Exception as error:
        recorder.record(name, "reader", "ERROR", repr(error))


def writer_task(name, monitor, resource, recorder, rounds, scale):
    try:
        for _ in range(rounds):
            time.sleep(random.uniform(0.05, 0.4) * scale)
            recorder.record(name, "writer", "WAITING", "requesting write access")
            monitor.start_write()
            try:
                recorder.record(name, "writer", "ENTER", "entered critical section (writing)")
                new_value = resource.write(scale)
                recorder.record(name, "writer", "EXIT", f"finished writing, value = {new_value}")
            finally:
                monitor.end_write()
        recorder.record(name, "writer", "DONE", "completed all rounds")
    except Exception as error:
        recorder.record(name, "writer", "ERROR", repr(error))


# ---------------------------------------------------------------------------
# Running one simulation
# ---------------------------------------------------------------------------
def run_simulation(scale):
    recorder = EventRecorder()
    resource = SharedResource()
    monitor = ReadersWriterMonitor()

    threads = []
    for name in READER_NAMES:
        threads.append(threading.Thread(
            target=reader_task, name=name,
            args=(name, monitor, resource, recorder, ROUNDS_PER_THREAD, scale)))
    for name in WRITER_NAMES:
        threads.append(threading.Thread(
            target=writer_task, name=name,
            args=(name, monitor, resource, recorder, ROUNDS_PER_THREAD, scale)))

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
        if thread.is_alive():
            recorder.record(thread.name, "?", "ERROR", "thread did not finish (possible deadlock)")

    expected_writes = len(WRITER_NAMES) * ROUNDS_PER_THREAD
    return recorder.events(), resource.value, expected_writes


# ---------------------------------------------------------------------------
# Verification of the recorded trace
# ---------------------------------------------------------------------------
def verify_trace(events, final_value, expected_writes):
    """Replay the log and check the rules of the problem. Returns (problems, stats)."""
    problems = []
    readers_inside = set()
    writers_inside = set()
    max_readers_together = 0
    last_seq = 0

    for event in events:
        if event["seq"] != last_seq + 1:
            problems.append(f"sequence numbers not consecutive at #{event['seq']}")
        last_seq = event["seq"]

        who, role, action = event["thread"], event["role"], event["action"]
        if action == "ERROR":
            problems.append(f"#{event['seq']} {who} reported ERROR: {event['detail']}")
        elif action == "ENTER" and role == "reader":
            if writers_inside:
                problems.append(f"#{event['seq']} reader {who} entered while writer {sorted(writers_inside)} inside")
            readers_inside.add(who)
            max_readers_together = max(max_readers_together, len(readers_inside))
        elif action == "ENTER" and role == "writer":
            if readers_inside or writers_inside:
                problems.append(f"#{event['seq']} writer {who} entered while "
                                f"readers={sorted(readers_inside)} writers={sorted(writers_inside)} inside")
            writers_inside.add(who)
        elif action == "EXIT":
            (readers_inside if role == "reader" else writers_inside).discard(who)

    if final_value != expected_writes:
        problems.append(f"lost update: final value {final_value}, expected {expected_writes}")
    for name in READER_NAMES + WRITER_NAMES:
        if not any(e["thread"] == name and e["action"] == "DONE" for e in events):
            problems.append(f"{name} never reported DONE")

    stats = {"events": len(events), "max_readers_together": max_readers_together,
             "final_value": final_value, "expected_writes": expected_writes}
    return problems, stats


# ---------------------------------------------------------------------------
# HTML generation (the events are embedded as JSON and replayed by JavaScript)
# ---------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  body { font-family: Segoe UI, Arial, sans-serif; margin: 0; background: #f4f6f8; color: #1b2430; }
  header { background: #1f3a5f; color: #fff; padding: 14px 22px; }
  header h1 { margin: 0; font-size: 20px; }
  header p { margin: 4px 0 0; font-size: 13px; opacity: .85; }
  main { display: grid; grid-template-columns: 1fr 380px; gap: 16px; padding: 16px 22px; }
  .panel { background: #fff; border-radius: 8px; padding: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.15); margin-bottom: 14px; }
  .panel h2 { margin: 0 0 10px; font-size: 15px; color: #1f3a5f; }
  button, select { font-size: 14px; padding: 6px 14px; margin-right: 6px; border-radius: 5px; border: 1px solid #8a97a8; background: #fff; cursor: pointer; }
  button.primary { background: #1f3a5f; color: #fff; border-color: #1f3a5f; }
  #resource { text-align: center; padding: 18px; border-radius: 8px; border: 3px solid #9aa5b1; background: #eceff3; transition: all .25s; }
  #resource.reading { border-color: #2e8b57; background: #e3f4ea; }
  #resource.writing { border-color: #c0392b; background: #fbe6e3; }
  #resource.violation { border-color: #000; background: #ffb3b3; }
  #resource .big { font-size: 22px; font-weight: bold; }
  .row { display: flex; flex-wrap: wrap; gap: 10px; }
  .card { width: 118px; padding: 10px; border-radius: 8px; text-align: center; border: 2px solid #9aa5b1; background: #eceff3; transition: all .25s; }
  .card .name { font-weight: bold; font-size: 17px; }
  .card .state { font-size: 13px; margin-top: 4px; }
  .card .count { font-size: 11px; color: #555; margin-top: 4px; }
  .card.waiting { background: #fff3cd; border-color: #e0a800; }
  .card.reading { background: #d4f1e0; border-color: #2e8b57; }
  .card.writing { background: #f8d7d3; border-color: #c0392b; }
  .card.done { background: #d9dee5; border-color: #6c7a89; color: #555; }
  .card.error { background: #000; color: #fff; }
  #log { height: 430px; overflow-y: auto; font-family: Consolas, monospace; font-size: 12px; }
  #log div { padding: 3px 4px; border-bottom: 1px solid #eef0f3; }
  #log .WAITING { background: #fff8e1; } #log .ENTER { background: #e8f6ee; }
  #log .EXIT { background: #eef2f7; } #log .DONE { color: #666; } #log .ERROR { background: #ffb3b3; }
  .meta td { padding: 2px 10px 2px 0; font-size: 13px; }
  .ok { color: #1e7d46; font-weight: bold; } .bad { color: #b00020; font-weight: bold; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <p>__POLICY__ &mdash; replay of a real execution trace (__COUNT__ recorded events)</p>
</header>
<main>
  <div>
    <div class="panel">
      <button class="primary" id="startBtn">Start / Resume</button>
      <button id="pauseBtn">Pause</button>
      <button id="stepBtn">Step</button>
      <button id="resetBtn">Reset</button>
      Speed:
      <select id="speed"><option value="0.5">0.5x</option><option value="1" selected>1x</option>
        <option value="2">2x</option><option value="4">4x</option></select>
      <span id="progress" style="margin-left:10px;font-size:13px;"></span>
    </div>
    <div class="panel">
      <h2>Shared resource</h2>
      <div id="resource"><div class="big" id="resStatus">FREE</div><div id="resInfo"></div></div>
    </div>
    <div class="panel"><h2>Readers</h2><div class="row" id="readers"></div></div>
    <div class="panel"><h2>Writers</h2><div class="row" id="writers"></div></div>
    <div class="panel">
      <h2>Checks</h2>
      <table class="meta">
        <tr><td>Python verification of this trace:</td><td id="pyCheck"></td></tr>
        <tr><td>Browser check while replaying:</td><td id="jsCheck"></td></tr>
        <tr><td>Max readers inside together (Python):</td><td id="maxReaders"></td></tr>
        <tr><td>Generated at:</td><td id="generated"></td></tr>
      </table>
    </div>
  </div>
  <div>
    <div class="panel"><h2>Event log (same # as the console output)</h2><div id="log"></div></div>
  </div>
</main>
<script>
const EVENTS = __EVENTS__;
const META = __META__;

const threads = {};      // name -> {role, state, rounds}
let readersInside = new Set(), writersInside = new Set();
let violations = 0, index = 0, playing = false, timer = null;

function resetState() {
  for (const k in threads) delete threads[k];
  EVENTS.forEach(e => { if (!threads[e.thread]) threads[e.thread] = {role: e.role, state: "idle", rounds: 0}; });
  readersInside = new Set(); writersInside = new Set(); violations = 0; index = 0;
  document.getElementById("log").innerHTML = "";
  render();
}

function applyEvent(e) {
  const t = threads[e.thread];
  if (e.action === "WAITING") t.state = "waiting";
  else if (e.action === "ENTER") {
    t.state = e.role === "reader" ? "reading" : "writing";
    if (e.role === "reader") { if (writersInside.size > 0) violations++; readersInside.add(e.thread); }
    else { if (readersInside.size > 0 || writersInside.size > 0) violations++; writersInside.add(e.thread); }
  } else if (e.action === "EXIT") {
    t.state = "idle"; t.rounds++;
    readersInside.delete(e.thread); writersInside.delete(e.thread);
  } else if (e.action === "DONE") t.state = "done";
  else if (e.action === "ERROR") t.state = "error";

  const line = document.createElement("div");
  line.className = e.action;
  line.textContent = "#" + e.seq + "  t=" + e.time.toFixed(3) + "s  " + e.thread + "  " + e.action + "  " + e.detail;
  const log = document.getElementById("log");
  log.appendChild(line); log.scrollTop = log.scrollHeight;
}

function render() {
  for (const role of ["reader", "writer"]) {
    const box = document.getElementById(role + "s");
    box.innerHTML = "";
    Object.keys(threads).filter(n => threads[n].role === role).forEach(n => {
      const t = threads[n];
      const d = document.createElement("div");
      d.className = "card " + (t.state === "idle" ? "" : t.state);
      d.innerHTML = '<div class="name">' + n + '</div><div class="state">' + t.state.toUpperCase() +
                    '</div><div class="count">completed: ' + t.rounds + '</div>';
      box.appendChild(d);
    });
  }
  const res = document.getElementById("resource");
  const status = document.getElementById("resStatus"), info = document.getElementById("resInfo");
  res.className = "";
  if (violations > 0) { res.className = "violation"; status.textContent = "RULE VIOLATED"; info.textContent = ""; }
  else if (writersInside.size > 0) { res.className = "writing"; status.textContent = "WRITING (exclusive)"; info.textContent = "writer: " + [...writersInside].join(", "); }
  else if (readersInside.size > 0) { res.className = "reading"; status.textContent = "READING (shared)"; info.textContent = readersInside.size + " reader(s): " + [...readersInside].join(", "); }
  else { status.textContent = "FREE"; info.textContent = "nobody inside the critical section"; }
  document.getElementById("progress").textContent = "event " + index + " / " + EVENTS.length;
  const js = document.getElementById("jsCheck");
  js.textContent = violations === 0 ? "no violations so far" : violations + " violation(s)";
  js.className = violations === 0 ? "ok" : "bad";
}

function playNext() {
  if (!playing) return;
  if (index >= EVENTS.length) { playing = false; return; }
  const e = EVENTS[index];
  applyEvent(e); index++; render();
  if (index < EVENTS.length) {
    const speed = parseFloat(document.getElementById("speed").value);
    const gap = Math.min((EVENTS[index].time - e.time) * 1000 / speed, 1500);
    timer = setTimeout(playNext, gap);
  } else playing = false;
}

document.getElementById("startBtn").onclick = () => {
  if (playing) return;
  if (index >= EVENTS.length) resetState();
  playing = true; playNext();
};
document.getElementById("pauseBtn").onclick = () => { playing = false; clearTimeout(timer); };
document.getElementById("stepBtn").onclick = () => {
  playing = false; clearTimeout(timer);
  if (index < EVENTS.length) { applyEvent(EVENTS[index]); index++; render(); }
};
document.getElementById("resetBtn").onclick = () => { playing = false; clearTimeout(timer); resetState(); };

const py = document.getElementById("pyCheck");
py.textContent = META.problems.length === 0 ? "PASSED" : "FAILED (" + META.problems.length + " problem(s))";
py.className = META.problems.length === 0 ? "ok" : "bad";
document.getElementById("maxReaders").textContent = META.max_readers_together;
document.getElementById("generated").textContent = META.generated;
resetState();
</script>
</body>
</html>
"""


def build_html(events, problems, stats):
    meta = {
        "problems": problems,
        "max_readers_together": stats["max_readers_together"],
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    title = "Readers-Writers (Monitor)"
    policy = "Writer priority using a lock and two condition variables"
    events_json = json.dumps(events).replace("</", "<\\/")
    meta_json = json.dumps(meta).replace("</", "<\\/")
    return (HTML_TEMPLATE.replace("__TITLE__", title)
            .replace("__POLICY__", policy)
            .replace("__COUNT__", str(len(events)))
            .replace("__EVENTS__", events_json)
            .replace("__META__", meta_json))


# ---------------------------------------------------------------------------
# Command line entry point
# ---------------------------------------------------------------------------
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_output = os.path.normpath(
        os.path.join(here, "..", "..", "generated_html", "readers_writers_monitor.html"))

    parser = argparse.ArgumentParser(description="Readers-Writers with a monitor")
    parser.add_argument("--runs", type=int, default=1, help="repeat the simulation N times")
    parser.add_argument("--fast", action="store_true", help="use short delays")
    parser.add_argument("--output", default=default_output, help="HTML file to write")
    args = parser.parse_args()

    scale = 0.15 if args.fast else 1.0
    all_ok = True
    events = []
    problems = []
    stats = {}

    for run_number in range(1, args.runs + 1):
        events, final_value, expected_writes = run_simulation(scale)
        problems, stats = verify_trace(events, final_value, expected_writes)
        verdict = "PASSED" if not problems else "FAILED"
        print(f"Run {run_number}/{args.runs}: {verdict}  events={stats['events']}  "
              f"max readers together={stats['max_readers_together']}  "
              f"final value={final_value}/{expected_writes}")
        for problem in problems:
            print("   problem:", problem)
        all_ok = all_ok and not problems

    if args.runs == 1:
        print("\nRecorded trace (compare these numbers with the HTML event log):")
        for e in events:
            print(f"  #{e['seq']:02d} t={e['time']:7.3f}s  {e['thread']}  {e['action']:<8} {e['detail']}")

    output_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(output_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as html_file:
        html_file.write(build_html(events, problems, stats))
    print(f"\nHTML written to: {args.output}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())