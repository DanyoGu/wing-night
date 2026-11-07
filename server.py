#!/usr/bin/env python3
"""Wing Night: dependency-free HTTP server with durable shared event state."""
import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
import secrets
import signal
import sqlite3
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
PROJECTIONS = [(8.42,8.74),(8.86,9.12),(9.03,8.65),(8.78,9.36),(9.14,8.92),(8.67,9.08),(9.21,8.46),(8.54,8.83),(8.96,9.17),(9.08,9.42)]

def release_info():
    path = ROOT / 'release.json'
    if not path.is_file():
        return {'version': 'v0.0', 'revision': 'development', 'commit_count': 0}
    release = json.loads(path.read_text())
    count, revision = release['commit_count'], release['revision']
    if (type(count) is not int or count < 0 or
            not isinstance(revision, str) or
            (revision != 'development' and not re.fullmatch('[0-9a-f]{40}', revision)) or
            release['version'] != f'v0.{count}'):
        raise ValueError('Invalid build release metadata')
    return release

class APIError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()

class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS rooms(code TEXT PRIMARY KEY, phase TEXT NOT NULL DEFAULT 'waiting', round INTEGER NOT NULL DEFAULT 0, voting INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY, room TEXT NOT NULL REFERENCES rooms(code), token TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL, name_key TEXT NOT NULL, UNIQUE(room,name_key));
            CREATE TABLE IF NOT EXISTS roster_changes(id INTEGER PRIMARY KEY, room TEXT NOT NULL REFERENCES rooms(code), member TEXT NOT NULL REFERENCES members(id), actor TEXT NOT NULL REFERENCES members(id), action TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS votes(member TEXT NOT NULL REFERENCES members(id), round INTEGER NOT NULL, grades TEXT NOT NULL, PRIMARY KEY(member,round));
            ''')
            if 'generation' not in {column['name'] for column in db.execute('PRAGMA table_info(rooms)')}:
                db.execute('ALTER TABLE rooms ADD COLUMN generation INTEGER NOT NULL DEFAULT 0')
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db
    def member(self, db, token):
        return db.execute('SELECT * FROM members WHERE token=?', (digest(token),)).fetchone() if token else None
    def perform(self, action, data, token=''):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            me = self.member(db, token)
            issued = None
            if action == 'create':
                if me:
                    return {'room': me['room']}, None
                code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))
                db.execute('INSERT INTO rooms(code) VALUES(?)', (code,))
                issued = secrets.token_urlsafe(32)
                db.execute('INSERT INTO members(id,room,token,name,role,name_key) VALUES(?,?,?,?,?,?)', (secrets.token_hex(16),code,digest(issued),'Daniel','host','__host__'))
                return {'room': code}, issued
            code = str(data.get('room', '')).strip().upper()
            room = db.execute('SELECT * FROM rooms WHERE code=?',(code,)).fetchone()
            if not room:
                raise APIError('Room not found. Check the room code.',404)
            if action == 'join':
                if me:
                    if me['room'] != code:
                        raise APIError('This browser is already registered in another room.',409)
                    if me['role']=='host':
                        raise APIError('This browser is the host. Open the invite in a different browser or private window to join as a judge.',409)
                    return {'room': code}, None
                name = ' '.join(str(data.get('name','')).split())
                if not 1 <= len(name) <= 32:
                    raise APIError('Enter a name between 1 and 32 characters.')
                if db.execute('SELECT 1 FROM members WHERE room=? AND name_key=?',(code,name.casefold())).fetchone():
                    raise APIError('That name is taken. Add an initial to distinguish yourself.',409)
                issued = secrets.token_urlsafe(32)
                role = 'judge' if room['phase']=='waiting' and name.casefold() not in ('daniel','joicee') else 'spectator'
                db.execute('INSERT INTO members(id,room,token,name,role,name_key) VALUES(?,?,?,?,?,?)',(secrets.token_hex(16),code,digest(issued),name,role,name.casefold()))
                return {'room':code}, issued
            if not me or me['room'] != code:
                raise APIError('Join this room first.',401)
            if action == 'vote':
                if me['role'] != 'judge':
                    raise APIError('Only active judges can grade the players.',403)
                grades = data.get('grades')
                if not isinstance(grades,list) or len(grades)!=4 or any(type(v) is not int or not 1<=v<=5 for v in grades):
                    raise APIError('Select all four grades, each from 1 to 5.')
                if type(data.get('round')) is not int or data['round'] != room['round'] or room['phase']!='started' or not room['voting']:
                    raise APIError('Voting is not open for this round.',409)
                db.execute('INSERT INTO votes VALUES(?,?,?) ON CONFLICT(member,round) DO UPDATE SET grades=excluded.grades',(me['id'],room['round'],json.dumps(grades)))
            elif action == 'reset':
                if me['role']!='host':
                    raise APIError('Only the host can reset this event.',403)
                if data.get('confirmed') is not True:
                    raise APIError('Confirm that all event votes and judges will be cleared.')
                if type(data.get('generation')) is not int or data['generation']!=room['generation']:
                    raise APIError('This event has already changed. Refresh before resetting.',409)
                db.execute('DELETE FROM votes WHERE member IN (SELECT id FROM members WHERE room=?)',(code,))
                db.execute('DELETE FROM roster_changes WHERE room=?',(code,))
                db.execute('DELETE FROM members WHERE room=? AND id<>?',(code,me['id']))
                db.execute("UPDATE rooms SET phase='waiting',round=0,voting=0,generation=generation+1 WHERE code=?",(code,))
            elif action in ('remove-judge','withdraw-judge'):
                if me['role']!='host':
                    raise APIError('Only Daniel can manage the judging roster.',403)
                if data.get('confirmed') is not True:
                    raise APIError('Confirm the judge removal and its scoring effect.',400)
                expected='waiting' if action=='remove-judge' else 'started'
                if room['phase']!=expected:
                    raise APIError('The event phase has changed. Refresh before changing the roster.',409)
                target=db.execute("SELECT * FROM members WHERE id=? AND room=? AND role='judge'",(str(data.get('member','')),code)).fetchone()
                if not target:
                    raise APIError('This person is no longer an active judge.',409)
                n=db.execute("SELECT COUNT(*) FROM members WHERE room=? AND role='judge'",(code,)).fetchone()[0]
                if action=='withdraw-judge' and n<=1:
                    raise APIError('At least one judge must remain. Wait for the final judge to reconnect.',409)
                db.execute('DELETE FROM votes WHERE member=?',(target['id'],))
                db.execute('UPDATE members SET role=? WHERE id=?',('removed' if action=='remove-judge' else 'withdrawn',target['id']))
                db.execute('INSERT INTO roster_changes(room,member,actor,action) VALUES(?,?,?,?)',(code,target['id'],me['id'],action))
            elif action in ('start','close','next'):
                if me['role'] != 'host':
                    raise APIError('Only Daniel can control the event.',403)
                n=db.execute("SELECT COUNT(*) FROM members WHERE room=? AND role='judge'",(code,)).fetchone()[0]
                if action == 'start':
                    if room['phase']!='waiting' or not n:
                        raise APIError('At least one judge must join before starting.',409)
                    db.execute("UPDATE rooms SET phase='started', voting=1 WHERE code=?",(code,))
                elif action == 'close':
                    submitted=db.execute('SELECT COUNT(*) FROM votes v JOIN members m ON m.id=v.member WHERE m.room=? AND v.round=?',(code,room['round'])).fetchone()[0]
                    if room['phase']!='started' or not room['voting'] or submitted!=n:
                        raise APIError('Every judge must submit before this round can close.',409)
                    db.execute('UPDATE rooms SET voting=0 WHERE code=?',(code,))
                elif room['phase']!='started' or room['voting']:
                    raise APIError('Close the current round before advancing.',409)
                elif room['round']==9:
                    db.execute("UPDATE rooms SET phase='finished' WHERE code=?",(code,))
                else:
                    db.execute('UPDATE rooms SET round=round+1,voting=1 WHERE code=?',(code,))
            else:
                raise APIError('Unknown action.',404)
            return {'ok':True}, issued
    def state(self, code, token=''):
        with self.connect() as db:
            db.execute('BEGIN')
            me=self.member(db,token)
            if not code and me:
                code=me['room']
            room=db.execute('SELECT * FROM rooms WHERE code=?',(code,)).fetchone()
            if not room:
                raise APIError('Room not found.',404)
            judges=[dict(r) for r in db.execute("SELECT id,name FROM members WHERE room=? AND role='judge' ORDER BY rowid",(code,))]
            n=len(judges)
            # Public pre-join responses contain no roster names or vote details.
            if not me or me['room']!=code:
                return {'room':code,'phase':room['phase'],'generation':room['generation'],'judgeCount':n,'me':None}
            rounds=[{'points':[0,0],'submitted':0,'submittedIds':[],'projection':[0,0]} for _ in range(10)]
            own={}
            for vote in db.execute("SELECT v.* FROM votes v JOIN members m ON m.id=v.member WHERE m.room=? AND m.role='judge'",(code,)):
                grades=json.loads(vote['grades']);r=rounds[vote['round']]
                r['points'][0]+=grades[0]+grades[1];r['points'][1]+=grades[2]+grades[3];r['submitted']+=1;r['submittedIds'].append(vote['member'])
                if vote['member']==me['id']:
                    own[str(vote['round'])]=grades
            for i,r in enumerate(rounds):
                r['projection']=[round(r['points'][p]+PROJECTIONS[i][p]*(n-r['submitted']),2) for p in range(2)]
            total=[sum(r['points'][p] for r in rounds) for p in range(2)]
            projection=[round(sum(round(r['projection'][p]*100) for r in rounds)/100,2) for p in range(2)]
            remaining=sum(n-r['submitted'] for r in rounds)
            chance=50 if not n else (50 if total[0]==total[1] else 100 if total[0]>total[1] else 0) if not remaining else max(1,min(99,round(100/(1+math.exp(max(-700,min(700,-(projection[0]-projection[1])/(2.4*math.sqrt(remaining)*math.sqrt(n)))))))))
            changes=[dict(r) for r in db.execute('SELECT c.action,c.created_at,m.name FROM roster_changes c JOIN members m ON m.id=c.member WHERE c.room=? ORDER BY c.id',(code,))]
            return {'room':code,'phase':room['phase'],'generation':room['generation'],'activeRound':room['round'],'votingOpen':bool(room['voting']),'judgeCount':n,'judges':judges,'rosterChanges':changes,'maxPoints':n*100,'rounds':rounds,'totals':total,'projections':projection,'chances':[chance,100-chance],'ownVotes':own,'me':{'id':me['id'],'name':me['name'],'role':me['role']}}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(ROOT/'dist'),**kwargs)
    def end_headers(self):
        if not urlsplit(self.path).path.startswith('/api/'):
            self.send_header('Cache-Control','no-cache')
        super().end_headers()
    def token(self):
        cookie=SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except Exception: return ''
        return cookie['wing_session'].value if 'wing_session' in cookie else ''
    def reply(self,data,status=200,token=None):
        body=json.dumps(data).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
        if token:
            secure='; Secure' if self.server.secure_cookies else ''
            self.send_header('Set-Cookie',f'wing_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=2592000{secure}')
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        parsed=urlsplit(self.path)
        if parsed.path=='/api/version':
            self.reply(self.server.release);return
        if parsed.path=='/api/state':
            try:self.reply(self.server.store.state(parse_qs(parsed.query).get('room',[''])[0].upper(),self.token()))
            except APIError as e:self.reply({'error':e.message},e.status)
            return
        if parsed.path.startswith('/api/'):
            self.reply({'error':'Not found'},404);return
        if parsed.path not in ('/','/index.html','/lobby.css','/lobby.js','/shared.js','/celebration.css','/celebration.js','/version.js') and not parsed.path.startswith('/assets/'):
            self.send_error(404);return
        super().do_GET()
    def do_POST(self):
        origin=self.headers.get('Origin')
        if (origin and urlsplit(origin).netloc!=self.headers.get('Host')) or self.headers.get('Sec-Fetch-Site')=='cross-site':
            self.reply({'error':'Cross-origin requests are not allowed.'},403);return
        if self.headers.get_content_type()!='application/json':
            self.reply({'error':'Use JSON requests.'},415);return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>4096:raise APIError('Invalid request size.',413)
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise APIError('Invalid request.')
            action=urlsplit(self.path).path.removeprefix('/api/')
            result,token=self.server.store.perform(action,data,self.token());self.reply(result,token=token)
        except APIError as e:self.reply({'error':e.message},e.status)
        except (ValueError,TypeError):self.reply({'error':'Invalid JSON request.'},400)
        except sqlite3.Error:self.reply({'error':'Database unavailable. Please retry.'},503)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=int(os.environ.get('PORT','8040')));parser.add_argument('--host',default='0.0.0.0');parser.add_argument('--db',default=os.environ.get('WING_DB',str(ROOT/'data'/'wing-night.sqlite')));parser.add_argument('--secure-cookies',action='store_true',default=os.environ.get('WING_SECURE_COOKIES')=='1');args=parser.parse_args()
    Path(args.db).parent.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer((args.host,args.port),Handler);server.store=Store(args.db);server.secure_cookies=args.secure_cookies;server.release=release_info()
    # shutdown must run outside the serve_forever thread; server_close drains workers.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f'Wing Night running at http://localhost:{args.port} (database: {args.db})',flush=True)
    print(f"Build {server.release['version']} revision {server.release['revision']}",flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()
