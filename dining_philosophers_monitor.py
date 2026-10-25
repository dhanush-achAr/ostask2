import threading, time, random, json, webbrowser
from pathlib import Path

N = 4
MEALS_EACH = 3
HTML_FILE = Path(__file__).with_name('dining_philosophers_monitor_animation.html')
THINKING, HUNGRY, EATING = 'thinking', 'hungry', 'eating'

events, event_lock = [], threading.Lock()
meals = [0] * N
start = time.perf_counter()
sequence = 0

def record(i, state, message):
    global sequence
    with event_lock:
        sequence += 1
        e = {
            'seq': sequence,
            'time': round(time.perf_counter()-start, 3),
            'philosopher': i,
            'state': state,
            'message': message,
            'meals': meals[i]
        }
        events.append(e)
        print(f"[{sequence:03} | {e['time']:6.3f}s] P{i+1}: {message}", flush=True)

class DiningMonitor:
    def __init__(self):
        self.states = [THINKING] * N
        self.lock = threading.Lock()
        self.conditions = [threading.Condition(self.lock) for _ in range(N)]

    def left(self, i):
        return (i-1) % N

    def right(self, i):
        return (i+1) % N

    def can_eat(self, i):
        return (
            self.states[self.left(i)] != EATING
            and self.states[self.right(i)] != EATING
        )

    def take_forks(self, i):
        with self.lock:
            self.states[i] = HUNGRY
            record(i, 'hungry', 'Hungry; checking neighbour states')

            while not self.can_eat(i):
                record(i, 'waiting', 'Waiting because a neighbour is eating')
                self.conditions[i].wait()

            self.states[i] = EATING
            record(i, 'eating', 'Both neighbours are not eating; started meal')

    def put_forks(self, i):
        with self.lock:
            self.states[i] = THINKING
            record(i, 'thinking', 'Finished meal; changed state to thinking')
            self.conditions[self.left(i)].notify()
            self.conditions[self.right(i)].notify()

def philosopher(i, monitor):
    for meal in range(1, MEALS_EACH+1):
        record(i, 'thinking', 'Thinking')
        time.sleep(random.uniform(.3, .7))

        monitor.take_forks(i)
        time.sleep(random.uniform(.35, .7))

        with event_lock:
            meals[i] += 1

        record(i, 'releasing', f'Finished meal {meal}; leaving eating state')
        monitor.put_forks(i)

    record(i, 'done', f'Finished all {MEALS_EACH} meals')

def save_html():
    data = json.dumps(events, ensure_ascii=False).replace('</', '<\\/')

    template = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Dining Philosophers — Monitor</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f2f5fa;color:#1e293b;font:15px system-ui,Arial}header{padding:18px 22px;background:#183b35;color:white}header h1{margin:0 0 4px}main{max-width:1050px;margin:18px auto;padding:0 14px}.panel{background:white;border:1px solid #dce3ed;border-radius:13px;padding:14px;margin-bottom:14px}.controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center}button,select{padding:8px 11px;border:1px solid #cbd5e1;border-radius:8px;background:white;font:inherit;cursor:pointer}button.primary{background:#18745b;color:white}#status{font-weight:bold;margin-left:auto}.stats{display:flex;gap:12px;flex-wrap:wrap}.stats span{background:#f3f6fb;border-radius:8px;padding:8px 12px}svg{width:100%;max-height:500px}.pname{font-size:16px;font-weight:bold;text-anchor:middle;fill:#152238}.pstate{font-size:12px;text-anchor:middle;fill:#152238}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:7px;border-bottom:1px solid #e1e7ef}th{background:#eef3f9;position:sticky;top:0}tr.active{background:#dff8e8}.log{max-height:330px;overflow:auto}.muted{color:#64748b;font-size:13px}.progress{height:7px;background:#e4eaf2;border-radius:10px;overflow:hidden;margin-top:10px}.progress div{height:100%;background:#18745b;width:0%}</style></head><body>
<header><h1>Dining Philosophers — Monitor Animation</h1><div>4 philosophers · state array + lock + condition variables</div></header><main><section class="panel controls"><button class="primary" id="play">▶ Play</button><button id="pause">Ⅱ Pause</button><button id="step">Step →</button><button id="reset">↺ Reset</button><label>Speed <select id="speed"><option value="1">1×</option><option value="2">2×</option><option value="4">4×</option><option value="0.5">0.5×</option></select></label><label>Filter <select id="filter"><option value="all">All</option><option value="0">P1</option><option value="1">P2</option><option value="2">P3</option><option value="3">P4</option></select></label><button id="csv">Download CSV</button><span id="status">Ready</span></section>
<section class="panel stats"><span>Events: <b id="count">0</b></span><span>Current: <b id="current">0</b></span><span>Meals completed: <b id="total">0</b></span><span>Playback: <b id="playing">Paused</b></span><div class="progress" style="width:100%"><div id="bar"></div></div></section>
<section class="panel"><h2>Table animation</h2><p class="muted">Green = eating, yellow = hungry/waiting, blue = thinking, grey = finished. The monitor allows a philosopher to eat only if neither neighbour is eating.</p><svg viewBox="0 0 700 510" aria-label="Four philosopher animation"><circle cx="350" cy="255" r="110" fill="#e9dfd0" stroke="#cdbda8" stroke-width="4"/><text x="350" y="250" text-anchor="middle" font-size="20" font-weight="bold" fill="#655647">MONITOR</text><text x="350" y="275" text-anchor="middle" font-size="20" font-weight="bold" fill="#655647">STATE</text><g id="forks"></g><g id="people"></g></svg></section><section class="panel"><h2>Recorded execution log</h2><div class="log"><table><thead><tr><th>#</th><th>Time</th><th>Philosopher</th><th>State</th><th>Action</th></tr></thead><tbody id="rows"></tbody></table></div></section></main>
<script>const events=__DATA__,n=4;let idx=0,timer=null,running=false;const states=Array.from({length:n},()=>({state:'thinking',meals:0}));const colors={thinking:'#4e83cc',hungry:'#f1c453',waiting:'#f1c453',eating:'#32a36b',releasing:'#a78bfa',done:'#8994a3'};const pos=[[350,70],[590,255],[350,440],[110,255]],fp=[[470,145],[470,365],[230,365],[230,145]],pg=document.getElementById('people'),fg=document.getElementById('forks');function el(tag,a,txt){let x=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(a||{}).forEach(([k,v])=>x.setAttribute(k,v));if(txt!==undefined)x.textContent=txt;return x}fp.forEach((p,i)=>{let g=el('g',{});g.append(el('rect',{x:p[0]-12,y:p[1]-18,width:24,height:36,rx:6,fill:'#b8c3d0',stroke:'#68778a','stroke-width':2}));g.append(el('text',{x:p[0],y:p[1]+4,'text-anchor':'middle','font-size':11,fill:'#253247'},'F'+(i+1)));fg.append(g)});pos.forEach((p,i)=>{let g=el('g',{});g.append(el('circle',{cx:p[0],cy:p[1],r:37,fill:colors.thinking,stroke:'white','stroke-width':4}));g.append(el('text',{x:p[0],y:p[1]-2,class:'pname'},'P'+(i+1)));g.append(el('text',{x:p[0],y:p[1]+16,class:'pstate'},'Thinking'));pg.append(g)});
function render(){let f=document.getElementById('filter').value;[...pg.children].forEach((g,i)=>{g.children[0].setAttribute('fill',colors[states[i].state]||'#4e83cc');g.children[2].textContent=states[i].state.replace('_',' ');g.setAttribute('opacity',f==='all'||+f===i?'1':'.25')});document.getElementById('count').textContent=events.length;document.getElementById('current').textContent=idx+' / '+events.length;document.getElementById('total').textContent=states.reduce((s,x)=>s+x.meals,0);document.getElementById('playing').textContent=running?'Playing':'Paused';document.getElementById('status').textContent=idx===0?'Ready':idx>=events.length?'Finished':'Event '+idx;document.getElementById('bar').style.width=(idx/events.length*100)+'%';let body=document.getElementById('rows');body.innerHTML='';let shown=events.map((e,j)=>({...e,j})).filter(e=>f==='all'||e.philosopher===+f),cur=shown.findIndex(e=>e.j===idx-1);shown.slice(Math.max(0,cur-5),cur+7).forEach(e=>{let tr=document.createElement('tr');if(e.j===idx-1)tr.className='active';[e.seq,e.time.toFixed(3)+'s','P'+(e.philosopher+1),e.state,e.message].forEach(v=>{let td=document.createElement('td');td.textContent=v;tr.append(td)});body.append(tr)})}function next(){if(idx>=events.length){stop();return}let e=events[idx];states[e.philosopher]={state:e.state,meals:e.meals};idx++;render();if(idx>=events.length)stop()}function stop(){running=false;if(timer)clearTimeout(timer);timer=null;render()}function tick(){if(!running)return;next();if(running)timer=setTimeout(tick,350/+document.getElementById('speed').value)}document.getElementById('play').onclick=()=>{if(idx>=events.length)idx=0;running=true;render();tick()};document.getElementById('pause').onclick=stop;document.getElementById('step').onclick=()=>{stop();next()};document.getElementById('reset').onclick=()=>{stop();idx=0;states.forEach(s=>{s.state='thinking';s.meals=0});render()};document.getElementById('speed').onchange=()=>{if(running){clearTimeout(timer);tick()}};document.getElementById('filter').onchange=render;document.getElementById('csv').onclick=()=>{let rows=[['seq','time','philosopher','state','message','meals'],...events.map(e=>[e.seq,e.time,'P'+(e.philosopher+1),e.state,e.message,e.meals])],csv=rows.map(r=>r.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n'),a=document.createElement('a');a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));a.download='monitor_events.csv';a.click();URL.revokeObjectURL(a.href)};render();</script></body></html>'''
    HTML_FILE.write_text(template.replace('__DATA__', data), encoding='utf-8')
    print(f'\nHTML animation saved: {HTML_FILE.resolve()}')
    try:
        webbrowser.open(HTML_FILE.resolve().as_uri())
    except Exception:
        pass

if __name__ == '__main__':
    print('Dining Philosophers monitor-style simulation')
    print(f'{N} philosophers, {N} forks, {MEALS_EACH} meals each\n')
    monitor = DiningMonitor()
    threads = [
        threading.Thread(target=philosopher, args=(i, monitor), name=f'P{i+1}')
        for i in range(N)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    save_html()
    print('\nFinal meal counts:')
    for i, c in enumerate(meals):
        print(f'  P{i+1}: {c}/{MEALS_EACH}')
    print(f'Total meals: {sum(meals)}/{N*MEALS_EACH}')