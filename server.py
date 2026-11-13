"""FocusLog - dependency-free local/LAN scheduling and activity journal.

Run: python server.py                 # desktop only
     python server.py --host 0.0.0.0  # desktop + phone on trusted Wi-Fi
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent
# exe化(PyInstaller)時は、exeと同じ場所の data/ にDBを保存する
_DEFAULT_DATA = (Path(sys.executable).resolve().parent / "data") if getattr(sys, "frozen", False) else BASE / "data"
DATA_DIR = Path(os.environ.get("FOCUSLOG_DATA_DIR", str(_DEFAULT_DATA)))
DB_PATH = DATA_DIR / "focuslog.db"
STATIC_DIR = BASE / "static"
DB_LOCK = threading.RLock()
CATEGORIES = ("research", "philosophy", "llm")
# モデル予定の種類。種類によって「活動」段か「場所・移動」段かが決まる。
ACTIVITY_PLAN_TYPES = ("research", "philosophy", "llm", "break", "fixed", "margin")
PLACE_KINDS = ("home", "lab", "travel", "other")  # 自宅 / 研究室 / 移動中 / その他
PLAN_TYPES = ACTIVITY_PLAN_TYPES + PLACE_KINDS
SCHEMA_VERSION = 2
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
DURATION_SQL = """((CAST(SUBSTR(end,1,2) AS INTEGER)*60+CAST(SUBSTR(end,4,2) AS INTEGER))-
                   (CAST(SUBSTR(start,1,2) AS INTEGER)*60+CAST(SUBSTR(start,4,2) AS INTEGER)))"""


def lane_of(kind):
    return "place" if kind in PLACE_KINDS else "activity"


class BadRequest(Exception):
    pass


def iso_date(v):
    try:
        if not isinstance(v, str) or len(v) != 10:
            raise ValueError()
        d = date.fromisoformat(v)
        if d.isoformat() != v:
            raise ValueError()
        return v
    except (ValueError, TypeError):
        raise BadRequest("日付は YYYY-MM-DD 形式で指定してください。")


def minute(v):
    if not isinstance(v, str) or not TIME_RE.fullmatch(v):
        raise BadRequest("時刻は HH:MM 形式で指定してください。")
    return int(v[:2]) * 60 + int(v[3:])


def duration(start, end):
    a, b = minute(start), minute(end)
    if a >= b:
        raise BadRequest("終了時刻は開始時刻より後にしてください（翌日へのまたぎは未対応）。")
    return b - a


def short_text(value, label, limit=300, required=True):
    if not isinstance(value, str):
        raise BadRequest(f"{label}が不正です。")
    value = value.strip()
    if required and not value:
        raise BadRequest(f"{label}を入力してください。")
    if len(value) > limit:
        raise BadRequest(f"{label}は{limit}文字以内にしてください。")
    return value


@contextmanager
def db():
    con = sqlite3.connect(str(DB_PATH), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def schema_version(con):
    """0 = 空のDB, 1 = 場所・移動に対応する前のDB, 2以降 = preferences に記録された版"""
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "weekly_plans" not in tables:
        return 0
    if "preferences" not in tables:
        return 1
    row = con.execute("SELECT value FROM preferences WHERE key='schema_version'").fetchone()
    return int(row[0]) if row else 1


def backup_db(tag):
    """SQLiteのオンラインバックアップで data/backups/ に複製を作る。"""
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = backup_dir / f"focuslog_{datetime.now():%Y%m%d_%H%M%S_%f}_{tag}.db"
    src = sqlite3.connect(str(DB_PATH))
    out = sqlite3.connect(str(dst))
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    return dst


def init_db():
    """DBの作成・移行。何度実行してもデータは重複・破損しない。

    移行（v1→v2）の内容:
      - 変更前に data/backups/ へバックアップを作成
      - place_logs（場所・移動の実績）テーブルを追加
      - 既存の予定は書き換えない。kind=travel は「場所・移動」段として表示され、goal_minutes も維持される
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with DB_LOCK:
        with db() as con:
            version = schema_version(con)
        backup = backup_db(f"before_v{SCHEMA_VERSION}") if 0 < version < SCHEMA_VERSION else None
        if backup:
            print(f"データベースを移行します。バックアップ: {backup}")
        _create_and_seed(fresh=(version == 0))
    return backup


def _create_and_seed(fresh):
    with db() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS weekly_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                label TEXT NOT NULL,
                kind TEXT NOT NULL,
                goal_minutes INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS day_overrides (
                day TEXT PRIMARY KEY,
                slots_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS themes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,
                UNIQUE(category, name)
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                category TEXT NOT NULL,
                theme_id INTEGER REFERENCES themes(id) ON DELETE SET NULL,
                content TEXT NOT NULL,
                memo TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_logs_day ON logs(day, start);
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS place_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                kind TEXT NOT NULL,
                memo TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_place_logs_day ON place_logs(day, start);
        """)
        if con.execute("SELECT COUNT(*) FROM weekly_plans").fetchone()[0] == 0:
            con.executemany("INSERT INTO weekly_plans(weekday,start,end,label,kind,goal_minutes) VALUES (?,?,?,?,?,?)", default_weekly())
        if con.execute("SELECT COUNT(*) FROM themes").fetchone()[0] == 0:
            samples = [("research", x) for x in ["DoNet", "Mask R-CNN", "修論執筆", "実画像評価"]]
            samples += [("philosophy", x) for x in ["第二の性", "存在と無", "プラグマティズム"]]
            samples += [("llm", x) for x in ["Transformer", "TinyStories", "論文輪読"]]
            con.executemany("INSERT INTO themes(category,name) VALUES (?,?)", samples)
        con.execute("INSERT OR IGNORE INTO preferences(key,value) VALUES ('timeline_start','07:00')")
        con.execute("INSERT OR IGNORE INTO preferences(key,value) VALUES ('timeline_end','23:00')")
        # 2026/11/17 は輪読なし（初回作成時のみ登録。ユーザーが解除した例外を再起動で復活させない）
        if fresh and con.execute("SELECT COUNT(*) FROM day_overrides WHERE day='2026-11-17'").fetchone()[0] == 0:
            monday_tuesday = weekly_for_day(con, "2026-11-17")
            for s in monday_tuesday:
                s.pop("lane", None)
                if s["start"] == "10:00" and s["end"] == "12:00" and s["kind"] == "fixed":
                    s["label"], s["kind"], s["goal_minutes"] = "研究・修論（輪読なし）", "research", 120
            con.execute("INSERT INTO day_overrides(day,slots_json) VALUES (?,?)", ("2026-11-17", json.dumps(monday_tuesday, ensure_ascii=False)))
        rename_legacy_labels(con)
        con.execute("INSERT INTO preferences(key,value) VALUES ('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(SCHEMA_VERSION),))


LEGACY_LABELS = {"通学（研究可能：最大1.5h）": "通学"}  # 研究目標は goal_minutes で表示するため名前から外した


def rename_legacy_labels(con):
    """初期データの旧名称を新名称に置き換える（毎週のモデル・日付ごとの例外の両方。何度実行しても同じ結果）。"""
    for old, new in LEGACY_LABELS.items():
        con.execute("UPDATE weekly_plans SET label=? WHERE label=?", (new, old))
    for row in con.execute("SELECT day,slots_json FROM day_overrides").fetchall():
        slots = json.loads(row[1])
        changed = False
        for s in slots:
            if s.get("label") in LEGACY_LABELS:
                s["label"] = LEGACY_LABELS[s["label"]]
                changed = True
        if changed:
            con.execute("UPDATE day_overrides SET slots_json=? WHERE day=?", (json.dumps(slots, ensure_ascii=False), row[0]))


def default_weekly():
    rows=[]
    def add(w, start, end, label, kind, goal=0):
        rows.append((w, start, end, label, kind, goal))
    for w in (0,2):
        add(w,"07:00","09:30","研究・修論","research",150)
        add(w,"09:30","10:00","休憩","break")
        add(w,"10:00","12:00","研究・修論","research",120)
        add(w,"12:00","13:00","昼食","break")
        add(w,"13:00","18:00","オンラインバイト","fixed")
        add(w,"18:00","21:00","マージン（哲学・LLM・休息）","margin")
    add(0,"21:00","23:00","講師MTG","fixed")
    for a,b,label,kind,goal in [
        ("08:00","10:00","通学","travel",90),
        ("10:00","12:00","LLM輪読","fixed",0),
        ("12:00","14:00","昼食会","break",0),
        ("14:00","16:00","哲学（任意）","philosophy",0),
        ("16:00","18:00","帰宅","travel",0),
    ]: add(1,a,b,label,kind,goal)
    add(3,"07:00","11:00","研究・修論","research",240)
    add(3,"11:00","15:00","昼食・移動・余裕時間","break")
    add(3,"15:00","23:00","バイト","fixed")
    for a,b,label,kind,goal in [
        ("07:00","08:00","研究・修論","research",60),
        ("08:00","10:00","通学","travel",90),
        ("10:00","13:30","研究・修論","research",210),
        ("13:30","14:30","昼食","break",0),
        ("14:30","16:30","哲学（任意）","philosophy",0),
        ("16:30","17:00","マージン","margin",0),
        ("17:00","20:00","ゼミ","fixed",0),
        ("20:00","22:00","帰宅","travel",0),
    ]: add(4,a,b,label,kind,goal)
    for w in (5,6):
        add(w,"07:00","09:30","研究・修論","research",150)
        add(w,"09:30","10:00","休憩","break")
        add(w,"10:00","12:30","研究・修論","research",150)
        add(w,"12:30","13:30","昼食","break")
        add(w,"13:30","14:30","研究・修論","research",60)
    add(5,"14:30","18:00","哲学（任意）","philosophy")
    add(5,"18:00","18:30","休憩","break")
    add(5,"18:30","21:00","LLM（任意）","llm")
    add(6,"14:30","17:00","哲学（任意）","philosophy")
    add(6,"17:00","19:00","LLM（任意）","llm")
    add(6,"19:00","21:00","休息・翌週の準備","margin")
    return rows


def weekly_for_day(con, day):
    w=date.fromisoformat(day).weekday()
    result=[]
    for s in con.execute("SELECT * FROM weekly_plans WHERE weekday=? ORDER BY start,end,id",(w,)):
        result.append({"id":f"w{s['id']}","weekly_id":s["id"],"start":s["start"],"end":s["end"],"label":s["label"],"kind":s["kind"],
                       "goal_minutes":s["goal_minutes"],"lane":lane_of(s["kind"])})
    return result


def day_slots(con, day):
    row=con.execute("SELECT slots_json FROM day_overrides WHERE day=?",(day,)).fetchone()
    if row:
        slots=json.loads(row[0])
        for s in slots:
            s["lane"]=lane_of(s.get("kind"))
        return sorted(slots, key=lambda s:(s["start"],s["end"],s["id"])), "override"
    return weekly_for_day(con,day),"weekly"


def query_place_logs(con, day):
    rows=con.execute("SELECT id,day,start,end,kind,memo FROM place_logs WHERE day=? ORDER BY start,end,id",(day,)).fetchall()
    return [dict(x) for x in rows]


def query_logs(con, day):
    rows=con.execute("""SELECT logs.id,logs.day,logs.start,logs.end,logs.category,logs.theme_id,logs.content,logs.memo,
            themes.name AS theme FROM logs LEFT JOIN themes ON logs.theme_id=themes.id
            WHERE logs.day=? ORDER BY logs.start,logs.end,logs.id""",(day,)).fetchall()
    return [dict(x) for x in rows]


def prefs(con):
    return {x["key"]:x["value"] for x in con.execute("SELECT key,value FROM preferences")}


def get_day(day):
    iso_date(day)
    with db() as con:
        slots,source=day_slots(con,day)
        logs=query_logs(con,day)
        place_logs=query_place_logs(con,day)
        p=prefs(con)
    return {"date":day,"weekday":date.fromisoformat(day).weekday(),"plan_source":source,
            "plan":slots,"logs":logs,"place_logs":place_logs,"preferences":p}


def validate_slot(obj):
    if not isinstance(obj,dict):
        raise BadRequest("予定の形式が不正です。")
    start=short_text(obj.get("start"),"開始時刻",5)
    end=short_text(obj.get("end"),"終了時刻",5)
    d=duration(start,end)
    kind=obj.get("kind")
    if kind not in PLAN_TYPES:
        raise BadRequest("予定の種類が不正です。")
    # 場所・移動の予定は名前（メモ）を省略可。省略時は画面で種類名を表示する。
    label=short_text(obj.get("label",""),"予定名",100,required=kind not in PLACE_KINDS)
    goal=obj.get("goal_minutes", d if kind=="research" else 0)
    if type(goal) is not int or goal<0 or goal>d:
        raise BadRequest("研究予定時間は予定枠の長さ以内にしてください。")
    if kind=="research":
        goal=d
    if kind not in ("research","travel"):
        goal=0
    return dict(start=start,end=end,label=label,kind=kind,goal_minutes=goal)


def mutate_plan(payload, method):
    day=iso_date(payload.get("date"))
    scope=payload.get("scope")
    if scope not in ("date","weekly"):
        raise BadRequest("変更範囲は『この日だけ』『毎週』から選択してください。")
    with DB_LOCK, db() as con:
        if scope=="date":
            slots,_=day_slots(con,day)
            item_id=payload.get("id")
            if method=="POST":
                slot=validate_slot(payload.get("slot"))
                slot.update(id="d"+uuid.uuid4().hex[:12],weekly_id=None)
                slots.append(slot)
            else:
                match=next((i for i,s in enumerate(slots) if s["id"]==item_id),None)
                if match is None:
                    raise BadRequest("対象の予定が見つかりません。日付を再読み込みしてください。")
                if method=="DELETE":
                    slots.pop(match)
                else:
                    old=slots[match]
                    slots[match]={**old,**validate_slot(payload.get("slot"))}
            slots.sort(key=lambda s:(s["start"],s["end"]))
            stored=[{k:v for k,v in s.items() if k!="lane"} for s in slots]  # lane は kind から算出するので保存しない
            con.execute("INSERT INTO day_overrides(day,slots_json) VALUES (?,?) ON CONFLICT(day) DO UPDATE SET slots_json=excluded.slots_json",
                        (day,json.dumps(stored,ensure_ascii=False)))
        else:
            weekday=date.fromisoformat(day).weekday()
            if method=="POST":
                s=validate_slot(payload.get("slot"))
                con.execute("INSERT INTO weekly_plans(weekday,start,end,label,kind,goal_minutes) VALUES (?,?,?,?,?,?)",
                            (weekday,s["start"],s["end"],s["label"],s["kind"],s["goal_minutes"]))
            else:
                item_id=payload.get("id")
                try:
                    weekly_id=int(str(item_id).removeprefix("w"))
                except (ValueError,TypeError):
                    raise BadRequest("個別に追加した予定は毎週編集できません。")
                old=con.execute("SELECT * FROM weekly_plans WHERE id=? AND weekday=?",(weekly_id,weekday)).fetchone()
                if not old:
                    raise BadRequest("毎週の元の予定がありません。")
                if method=="DELETE":
                    con.execute("DELETE FROM weekly_plans WHERE id=?",(weekly_id,))
                else:
                    s=validate_slot(payload.get("slot"))
                    con.execute("UPDATE weekly_plans SET start=?,end=?,label=?,kind=?,goal_minutes=? WHERE id=?",
                                (s["start"],s["end"],s["label"],s["kind"],s["goal_minutes"],weekly_id))
    return get_day(day)


def reset_day(day):
    iso_date(day)
    with DB_LOCK,db() as con:
        con.execute("DELETE FROM day_overrides WHERE day=?",(day,))
    return get_day(day)


def theme_by_name(con, category, name):
    if not name: return None
    existing=con.execute("SELECT id FROM themes WHERE category=? AND name=?",(category,name)).fetchone()
    if existing: return existing[0]
    cur=con.execute("INSERT INTO themes(category,name,archived) VALUES (?,?,0)",(category,name))
    return cur.lastrowid


def validate_log(payload):
    day=iso_date(payload.get("day"))
    start=short_text(payload.get("start"),"開始時刻",5)
    end=short_text(payload.get("end"),"終了時刻",5)
    duration(start,end)
    category=payload.get("category")
    if category not in CATEGORIES:
        raise BadRequest("実績カテゴリは研究・哲学・LLMのいずれかにしてください。")
    theme=short_text(payload.get("theme", ""),"テーマ",100,False)
    content=short_text(payload.get("content"),"作業内容",500)
    memo=short_text(payload.get("memo", ""),"メモ",2000,False)
    return day,start,end,category,theme,content,memo


def save_log(payload, item_id=None):
    day,start,end,category,theme,content,memo=validate_log(payload)
    with DB_LOCK,db() as con:
        tid=theme_by_name(con,category,theme)
        if item_id is None:
            cur=con.execute("INSERT INTO logs(day,start,end,category,theme_id,content,memo) VALUES (?,?,?,?,?,?,?)",
                            (day,start,end,category,tid,content,memo))
            item_id=cur.lastrowid
        else:
            cur=con.execute("UPDATE logs SET day=?,start=?,end=?,category=?,theme_id=?,content=?,memo=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (day,start,end,category,tid,content,memo,item_id))
            if cur.rowcount==0:raise BadRequest("編集対象の実績が見つかりません。")
        overlap=con.execute("""SELECT COUNT(*) FROM logs WHERE day=? AND id!=? AND start<? AND end>?""",
                            (day,item_id,end,start)).fetchone()[0]
    return {"day":get_day(day),"overlap_count":overlap,"saved_id":item_id}


def remove_log(item_id):
    with DB_LOCK,db() as con:
        row=con.execute("SELECT day FROM logs WHERE id=?",(item_id,)).fetchone()
        if not row:raise BadRequest("削除対象の記録が見つかりません。")
        con.execute("DELETE FROM logs WHERE id=?",(item_id,))
    return get_day(row["day"])


def validate_place_log(payload):
    day=iso_date(payload.get("day"))
    start=short_text(payload.get("start"),"開始時刻",5)
    end=short_text(payload.get("end"),"終了時刻",5)
    duration(start,end)
    kind=payload.get("kind")
    if kind not in PLACE_KINDS:
        raise BadRequest("場所・移動の種類（自宅・研究室・移動中・その他）を選択してください。")
    memo=short_text(payload.get("memo",""),"メモ",1000,False)
    return day,start,end,kind,memo


def save_place_log(payload, item_id=None):
    """場所・移動の実績。活動実績とは独立しており、時間が重なっても警告しない。"""
    day,start,end,kind,memo=validate_place_log(payload)
    with DB_LOCK,db() as con:
        if item_id is None:
            item_id=con.execute("INSERT INTO place_logs(day,start,end,kind,memo) VALUES (?,?,?,?,?)",
                                (day,start,end,kind,memo)).lastrowid
        else:
            cur=con.execute("UPDATE place_logs SET day=?,start=?,end=?,kind=?,memo=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (day,start,end,kind,memo,item_id))
            if cur.rowcount==0:raise BadRequest("編集対象の場所・移動の記録が見つかりません。")
    return {"day":get_day(day),"saved_id":item_id}


def remove_place_log(item_id):
    with DB_LOCK,db() as con:
        row=con.execute("SELECT day FROM place_logs WHERE id=?",(item_id,)).fetchone()
        if not row:raise BadRequest("削除対象の場所・移動の記録が見つかりません。")
        con.execute("DELETE FROM place_logs WHERE id=?",(item_id,))
    return get_day(row["day"])


def get_themes():
    with db() as con:
        return [dict(x) for x in con.execute("SELECT id,category,name,archived FROM themes ORDER BY category,archived,name")]


def manage_theme(method,payload,item_id=None):
    with DB_LOCK,db() as con:
        if method=="POST":
            category=payload.get("category")
            if category not in CATEGORIES:raise BadRequest("テーマのカテゴリが不正です。")
            name=short_text(payload.get("name"),"テーマ名",100)
            tid=theme_by_name(con,category,name)
            con.execute("UPDATE themes SET archived=0 WHERE id=?",(tid,))
        else:
            row=con.execute("SELECT * FROM themes WHERE id=?",(item_id,)).fetchone()
            if not row:raise BadRequest("テーマが見つかりません。")
            if "name" in payload:
                name=short_text(payload["name"],"テーマ名",100)
                try: con.execute("UPDATE themes SET name=? WHERE id=?",(name,item_id))
                except sqlite3.IntegrityError:raise BadRequest("同じカテゴリに同名のテーマが既にあります。")
            if "archived" in payload:
                if type(payload["archived"]) is not bool:raise BadRequest("アーカイブ設定が不正です。")
                con.execute("UPDATE themes SET archived=? WHERE id=?",(int(payload["archived"]),item_id))
    return get_themes()


def summary(start,end):
    start=iso_date(start);end=iso_date(end)
    a,b=date.fromisoformat(start),date.fromisoformat(end)
    if b<a or (b-a).days>365:raise BadRequest("集計期間は1日以上366日以内で指定してください。")
    with db() as con:
        # 活動時間は活動実績（logs）からのみ集計する。場所・移動の時間は加算しない。
        rows=con.execute(f"SELECT day,category,SUM({DURATION_SQL}) AS duration FROM logs WHERE day BETWEEN ? AND ? GROUP BY day,category",
                         (start,end)).fetchall()
        totals={}
        for r in rows:totals.setdefault(r["day"],{})[r["category"]]=r["duration"]
        prow=con.execute(f"SELECT day,kind,SUM({DURATION_SQL}) AS duration FROM place_logs WHERE day BETWEEN ? AND ? GROUP BY day,kind",
                         (start,end)).fetchall()
        place_totals={}
        for r in prow:place_totals.setdefault(r["day"],{})[r["kind"]]=r["duration"]
        data=[]
        current=a
        while current<=b:
            day=current.isoformat()
            slots,_=day_slots(con,day)
            actual=totals.get(day,{})
            places=place_totals.get(day,{})
            data.append({"day":day,"weekday":current.weekday(),
                         "research_goal":sum(s.get("goal_minutes",0) for s in slots),
                         "research":actual.get("research",0),"philosophy":actual.get("philosophy",0),"llm":actual.get("llm",0),
                         "places":{k:places.get(k,0) for k in PLACE_KINDS}})
            current+=timedelta(days=1)
    return {"start":start,"end":end,"days":data,
            "total":{"research_goal":sum(x["research_goal"] for x in data),
                     **{k:sum(x[k] for x in data) for k in CATEGORIES},
                     "places":{k:sum(x["places"][k] for x in data) for k in PLACE_KINDS}}}


def export_data(fmt):
    with db() as con:
        logs=[dict(x) for x in con.execute("SELECT logs.id,logs.day,logs.start,logs.end,logs.category,logs.theme_id,themes.name AS theme,logs.content,logs.memo,logs.created_at,logs.updated_at FROM logs LEFT JOIN themes ON logs.theme_id=themes.id ORDER BY logs.day,logs.start,logs.id")]
        if fmt=="csv":
            out=io.StringIO()
            fieldnames=["id","day","start","end","category","theme","content","memo","created_at","updated_at"]
            writer=csv.DictWriter(out,fieldnames=fieldnames,extrasaction="ignore")
            writer.writeheader();writer.writerows(logs)
            return "\ufeff"+out.getvalue(),"text/csv; charset=utf-8","focuslog_logs.csv"
        place_logs=[dict(x) for x in con.execute("SELECT * FROM place_logs ORDER BY day,start,id")]
        if fmt=="places_csv":
            out=io.StringIO()
            writer=csv.DictWriter(out,fieldnames=["id","day","start","end","kind","memo","created_at","updated_at"],extrasaction="ignore")
            writer.writeheader();writer.writerows(place_logs)
            return "﻿"+out.getvalue(),"text/csv; charset=utf-8","focuslog_places.csv"
        if fmt!="json":raise BadRequest("出力形式は csv / places_csv / json を指定してください。")
        all_weekly=[dict(x) for x in con.execute("SELECT * FROM weekly_plans ORDER BY weekday,start")]
        all_overrides=[dict(x) for x in con.execute("SELECT * FROM day_overrides ORDER BY day")]
        all_themes=[dict(x) for x in con.execute("SELECT * FROM themes")]
        data={"schema":SCHEMA_VERSION,"exported_at":datetime.now().isoformat(timespec="seconds"),"logs":logs,"place_logs":place_logs,
              "weekly_plans":all_weekly,"day_overrides":all_overrides,"themes":all_themes,"preferences":prefs(con)}
        return json.dumps(data,ensure_ascii=False,indent=2),"application/json; charset=utf-8","focuslog_backup.json"


class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(STATIC_DIR),**kwargs)

    def end_headers(self):
        # 更新後に古い app.js / styles.css がキャッシュから使われないよう、静的ファイルは毎回検証させる
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control","no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(),fmt%args))

    def reply_json(self,obj,status=200):
        body=json.dumps(obj,ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers();self.wfile.write(body)

    def send_file_bytes(self,body,content_type,filename):
        payload=body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",content_type)
        self.send_header("Content-Disposition",f'attachment; filename="{filename}"')
        self.send_header("Content-Length",str(len(payload)))
        self.send_header("Cache-Control","no-store")
        self.end_headers();self.wfile.write(payload)

    def parsed(self):
        p=urlparse(self.path)
        return p.path,parse_qs(p.query)

    def read_json(self):
        if self.headers.get("Content-Type","").split(";")[0].strip()!="application/json":
            raise BadRequest("application/json で送信してください。")
        try:
            n=int(self.headers.get("Content-Length","0"))
            if n<2 or n>131072:raise ValueError()
            p=json.loads(self.rfile.read(n))
            if not isinstance(p,dict):raise ValueError()
            return p
        except (ValueError,json.JSONDecodeError):
            raise BadRequest("JSONリクエストが不正です。")

    def handle_error(self,e):
        if isinstance(e,BadRequest):self.reply_json({"error":str(e)},400)
        elif isinstance(e,sqlite3.IntegrityError):self.reply_json({"error":"データの整合性エラーが発生しました。"},409)
        else:
            print("API error:",repr(e))
            self.reply_json({"error":"サーバー側の処理でエラーが発生しました。"},500)

    def do_GET(self):
        path,q=self.parsed()
        if not path.startswith("/api/"):
            return super().do_GET()
        try:
            if path=="/api/health":return self.reply_json({"ok":True,"app":"FocusLog","version":"0.2.0"})
            if path=="/api/day":return self.reply_json(get_day(q.get("date",[date.today().isoformat()])[0]))
            if path=="/api/themes":return self.reply_json(get_themes())
            if path=="/api/summary":
                today=date.today();week=today-timedelta(days=today.weekday())
                return self.reply_json(summary(q.get("start",[week.isoformat()])[0],q.get("end",[today.isoformat()])[0]))
            if path=="/api/export":
                data,ct,fn=export_data(q.get("format",["json"])[0])
                return self.send_file_bytes(data,ct,fn)
            self.reply_json({"error":"APIが見つかりません。"},404)
        except Exception as e:self.handle_error(e)

    def do_POST(self):self.do_mutation("POST")
    def do_PATCH(self):self.do_mutation("PATCH")
    def do_DELETE(self):self.do_mutation("DELETE")

    def do_mutation(self,method):
        path,q=self.parsed()
        try:
            if path=="/api/logs" and method=="POST":return self.reply_json(save_log(self.read_json()),201)
            if path.startswith("/api/logs/") and method in ("PATCH","DELETE"):
                item_id=self.numeric_id(path,"/api/logs/")
                if method=="PATCH":return self.reply_json(save_log(self.read_json(),item_id))
                return self.reply_json(remove_log(item_id))
            if path=="/api/place-logs" and method=="POST":return self.reply_json(save_place_log(self.read_json()),201)
            if path.startswith("/api/place-logs/") and method in ("PATCH","DELETE"):
                item_id=self.numeric_id(path,"/api/place-logs/")
                if method=="PATCH":return self.reply_json(save_place_log(self.read_json(),item_id))
                return self.reply_json(remove_place_log(item_id))
            if path=="/api/plans" and method in ("POST","PATCH","DELETE"):
                return self.reply_json(mutate_plan(self.read_json(),method),201 if method=="POST" else 200)
            if path=="/api/day-override" and method=="DELETE":
                return self.reply_json(reset_day(self.read_json().get("date")))
            if path=="/api/themes" and method=="POST":return self.reply_json(manage_theme(method,self.read_json()),201)
            if path.startswith("/api/themes/") and method=="PATCH":
                return self.reply_json(manage_theme(method,self.read_json(),self.numeric_id(path,"/api/themes/")))
            if path=="/api/preferences" and method=="PATCH":
                payload=self.read_json()
                start=short_text(payload.get("timeline_start"),"開始時刻",5)
                end=short_text(payload.get("timeline_end"),"終了時刻",5)
                if duration(start,end)<60:raise BadRequest("表示範囲は1時間以上にしてください。")
                with DB_LOCK,db() as con:
                    con.execute("UPDATE preferences SET value=? WHERE key='timeline_start'",(start,))
                    con.execute("UPDATE preferences SET value=? WHERE key='timeline_end'",(end,))
                return self.reply_json({"timeline_start":start,"timeline_end":end})
            self.reply_json({"error":"APIが見つかりません。"},404)
        except Exception as e:self.handle_error(e)

    @staticmethod
    def numeric_id(path,prefix):
        target=path[len(prefix):]
        if not target.isdecimal():raise BadRequest("IDが不正です。")
        return int(target)


def main():
    parser=argparse.ArgumentParser(description="FocusLog local/LAN application")
    parser.add_argument("--host",default="127.0.0.1",help="Use 0.0.0.0 for phone access on same LAN")
    parser.add_argument("--port",type=int,default=8765)
    args=parser.parse_args()
    init_db()
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    print("\nFocusLog が起動しました。")
    print(f"  PC: http://127.0.0.1:{args.port}")
    if args.host=="0.0.0.0":
        print(f"  スマホ: http://<PCのローカルIP>:{args.port}  （同じWi-Fiで接続）")
        print("  注意: 認証機能はありません。信頼できるLAN以外では公開しないでください。")
    print("  停止: Ctrl+C\n")
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=="__main__":main()
