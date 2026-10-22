"""JUnit XML ingestion, SQLite summaries, and a local HTTP dashboard."""
from __future__ import annotations

import argparse
from contextlib import closing
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS builds(id INTEGER PRIMARY KEY, project TEXT NOT NULL, build_key TEXT NOT NULL, imported_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(project,build_key));
CREATE TABLE IF NOT EXISTS cases(build_id INTEGER NOT NULL REFERENCES builds(id) ON DELETE CASCADE, name TEXT NOT NULL, outcome TEXT NOT NULL, duration REAL NOT NULL, message TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS cases_by_build ON cases(build_id);
"""


def parse(path: Path) -> list[tuple[str,str,float,str]]:
    root = ET.parse(path).getroot()
    if root.tag not in {"testsuite","testsuites"}:
        raise ValueError("Expected JUnit testsuite XML")
    result = []
    for node in root.iter("testcase"):
        name = f"{node.get('classname','')}.{node.get('name','')}".strip(".")
        outcome = "skipped" if node.find("skipped") is not None else "failed" if node.find("failure") is not None or node.find("error") is not None else "passed"
        error = node.find("failure") if node.find("failure") is not None else node.find("error")
        duration = float(node.get("time","0"))
        if duration < 0: raise ValueError("Negative test duration")
        result.append((name,outcome,duration,(error.get("message","") if error is not None else "")[:1000]))
    return result


def ingest(db: sqlite3.Connection, project: str, build_key: str, xml: Path) -> int:
    cases = parse(xml)
    with db:
        db.execute("INSERT INTO builds(project,build_key) VALUES(?,?) ON CONFLICT(project,build_key) DO NOTHING",(project,build_key))
        build_id = db.execute("SELECT id FROM builds WHERE project=? AND build_key=?",(project,build_key)).fetchone()[0]
        db.execute("DELETE FROM cases WHERE build_id=?",(build_id,))
        db.executemany("INSERT INTO cases(build_id,name,outcome,duration,message) VALUES(?,?,?,?,?)",((build_id,*row) for row in cases))
    return len(cases)


def summary(db: sqlite3.Connection, project: str | None = None) -> dict:
    projects = [project] if project else [row[0] for row in db.execute("SELECT DISTINCT project FROM builds ORDER BY project")]
    output = {}
    for name in projects:
        builds = db.execute("SELECT id,build_key FROM builds WHERE project=? ORDER BY id DESC LIMIT 30",(name,)).fetchall()
        history=[]
        for build_id,key in reversed(builds):
            rows=db.execute("SELECT outcome,count(*) FROM cases WHERE build_id=? GROUP BY outcome",(build_id,)).fetchall()
            counts=dict(rows)
            total=sum(counts.values())
            history.append({"build":key,"total":total,"passed":counts.get("passed",0),"failed":counts.get("failed",0),
                            "pass_rate":round(counts.get("passed",0)/total,4) if total else 0})
        recent_ids=[row[0] for row in builds[:10]]
        if recent_ids:
            marks=",".join("?" for _ in recent_ids)
            slow=db.execute(f"SELECT name,max(duration) FROM cases WHERE build_id IN ({marks}) GROUP BY name ORDER BY max(duration) DESC LIMIT 10",recent_ids).fetchall()
            recurring=db.execute(f"SELECT name,count(DISTINCT build_id) FROM cases WHERE build_id IN ({marks}) AND outcome='failed' GROUP BY name HAVING count(DISTINCT build_id)>=3 ORDER BY count(DISTINCT build_id) DESC",recent_ids).fetchall()
        else: slow=recurring=[]
        output[name]={"history":history,"red":len(history)>=2 and all(row["failed"]>0 for row in history[-2:]),
                      "slowest":[{"name":test,"seconds":round(seconds,3)} for test,seconds in slow],
                      "recurring_failures":[{"name":test,"failed_builds":count} for test,count in recurring]}
    return output


def page(data: dict) -> str:
    blocks=[]
    for project,values in data.items():
        history=values["history"]
        coordinates=" ".join(f"{20+i*max(1,600/max(1,len(history)-1)):.1f},{130-row['pass_rate']*110:.1f}" for i,row in enumerate(history))
        chart=f"<svg viewBox='0 0 650 150' role='img' aria-label='Pass rate over builds'><polyline points='{coordinates}' fill='none' stroke='#2563eb' stroke-width='3'/></svg>"
        build_rows="".join(f"<tr><td>{html.escape(row['build'])}</td><td>{row['passed']}/{row['total']}</td><td>{row['pass_rate']:.0%}</td></tr>" for row in history)
        slow="".join(f"<li>{html.escape(item['name'])}: {item['seconds']}s</li>" for item in values["slowest"])
        recurring="".join(f"<li>{html.escape(item['name'])}: {item['failed_builds']} builds</li>" for item in values["recurring_failures"])
        blocks.append(f"<section><h2>{html.escape(project)} <span class='status'>{'RED' if values['red'] else 'GREEN'}</span></h2>{chart}<table><tr><th>Build</th><th>Passed</th><th>Rate</th></tr>{build_rows}</table><h3>Slowest tests</h3><ol>{slow}</ol><h3>Recurring failures</h3><ul>{recurring}</ul></section>")
    return "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Release pipeline</title><style>body{font:16px system-ui;max-width:1000px;margin:2rem auto;padding:0 1rem;background:#f8fafc;color:#152238}section{background:white;padding:1rem;margin:1rem 0;border-radius:10px;box-shadow:0 1px 8px #ddd}svg{max-width:100%;background:#eff6ff}table{border-collapse:collapse}td,th{padding:.4rem 1rem;border-bottom:1px solid #ddd}.status{font-size:.8rem}</style><h1>Release pipeline dashboard</h1>"+"".join(blocks)+"</html>"


def serve(path: Path, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with closing(sqlite3.connect(path)) as db:
                db.executescript(SCHEMA)
                data=summary(db)
            if self.path == "/api/builds":
                body=json.dumps(data).encode()
                content_type="application/json"
            elif self.path == "/":
                body=page(data).encode()
                content_type="text/html; charset=utf-8"
            else:
                self.send_error(404); return
            self.send_response(200)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    with ThreadingHTTPServer(("127.0.0.1",port),Handler) as server:
        print(f"http://127.0.0.1:{server.server_port}")
        server.serve_forever()


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest="action",required=True)
    importing=actions.add_parser("import")
    importing.add_argument("database",type=Path)
    importing.add_argument("project")
    importing.add_argument("build")
    importing.add_argument("xml",type=Path)
    serving=actions.add_parser("serve")
    serving.add_argument("database",type=Path)
    serving.add_argument("--port",type=int,default=8080)
    args=parser.parse_args()
    if args.action=="import":
        with closing(sqlite3.connect(args.database)) as db:
            db.executescript(SCHEMA)
            print(f"Imported {ingest(db,args.project,args.build,args.xml)} test cases")
    else: serve(args.database,args.port)

