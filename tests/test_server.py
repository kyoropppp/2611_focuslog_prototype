import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH=Path(__file__).resolve().parents[1]/"server.py"
spec=importlib.util.spec_from_file_location("focuslog_server",MODULE_PATH)
app=importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        app.DATA_DIR=Path(cls.tmp.name)
        app.DB_PATH=app.DATA_DIR/"sample.db"
        app.init_db()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_seeded_weekly_schedule_and_cancelled_reading(self):
        mon=app.get_day("2026-11-16")
        self.assertEqual(mon["plan_source"],"weekly")
        self.assertEqual(sum(s["goal_minutes"] for s in mon["plan"]),270)
        self.assertTrue(any(s["start"]=="13:00" and s["end"]=="18:00" and s["label"]=="オンラインバイト" for s in mon["plan"]))
        tue=app.get_day("2026-11-24")
        self.assertTrue(all(s["start"]!="07:00" for s in tue["plan"]))
        self.assertEqual(sum(s["goal_minutes"] for s in tue["plan"]),90)
        nov17=app.get_day("2026-11-17")
        self.assertEqual(nov17["plan_source"],"override")
        self.assertEqual(sum(s["goal_minutes"] for s in nov17["plan"]),210)
        self.assertTrue(any(s["start"]=="10:00" and s["kind"]=="research" for s in nov17["plan"]))

    def test_log_crud_and_theme_management(self):
        p={"day":"2026-11-16","start":"07:10","end":"08:45","category":"research","theme":"DoNet","content":"損失を調べた","memo":"確認済み"}
        result=app.save_log(p)
        self.assertEqual(result["overlap_count"],0)
        item=result["saved_id"]
        logs=app.get_day("2026-11-16")["logs"]
        self.assertEqual(len(logs),1)
        self.assertEqual(logs[0]["theme"],"DoNet")
        p2={**p,"start":"08:00","end":"09:30","theme":"自作テーマ","content":"評価をした"}
        other=app.save_log(p2)
        self.assertEqual(other["overlap_count"],1)
        self.assertTrue(any(x["name"]=="自作テーマ" for x in app.get_themes()))
        p["end"]="08:30";p["content"]="更新"
        new=app.save_log(p,item)
        self.assertEqual(new["saved_id"],item)
        self.assertEqual(next(x["content"] for x in new["day"]["logs"] if x["id"]==item),"更新")
        app.remove_log(item)
        app.remove_log(other["saved_id"])
        self.assertEqual(app.get_day("2026-11-16")["logs"],[])

    def test_override_edit_does_not_modify_recurring(self):
        date_str="2026-11-18"
        before=app.get_day(date_str)
        old=next(x for x in before["plan"] if x["start"]=="07:00")
        changed={"start":"07:15","end":"09:30","kind":"research","label":"研究（変更）","goal_minutes":135}
        new=app.mutate_plan({"date":date_str,"scope":"date","id":old["id"],"slot":changed},"PATCH")
        self.assertEqual(new["plan_source"],"override")
        self.assertTrue(any(x["start"]=="07:15" for x in new["plan"]))
        other_day=app.get_day("2026-11-25")
        self.assertTrue(any(x["start"]=="07:00" for x in other_day["plan"]))
        reset=app.reset_day(date_str)
        self.assertEqual(reset["plan_source"],"weekly")
        self.assertTrue(any(x["start"]=="07:00" for x in reset["plan"]))

    def test_recurring_schedule_changes_future_same_weekday(self):
        day="2026-11-20"
        before=app.get_day(day)
        item=next(x for x in before["plan"] if x["label"]=="ゼミ")
        new=app.mutate_plan({"date":day,"scope":"weekly","id":item["id"],"slot":{
            "start":"17:30","end":"20:00","kind":"fixed","label":"ゼミ（変更）","goal_minutes":0}},"PATCH")
        self.assertTrue(any(x["start"]=="17:30" for x in new["plan"]))
        self.assertTrue(any(x["start"]=="17:30" for x in app.get_day("2026-11-27")["plan"]))

    def test_summary_and_validation(self):
        s=app.summary("2026-11-16","2026-11-22")
        self.assertEqual(len(s["days"]),7)
        self.assertEqual(s["total"]["research_goal"],32.5*60+2*60) # 11/17 override adds 2h
        with self.assertRaises(app.BadRequest):app.validate_log({"day":"2026-11-16","start":"12:00","end":"10:00","category":"research","content":"test"})
        with self.assertRaises(app.BadRequest):app.validate_log({"day":"2026-11-16","start":"07:00","end":"08:00","category":"work","content":"test"})
        with self.assertRaises(app.BadRequest):app.summary("2026-01-01","2028-01-01")

    def test_exports(self):
        s,ct,fn=app.export_data("json")
        out=json.loads(s)
        self.assertIn("weekly_plans",out)
        self.assertIn("day_overrides",out)
        s,ct,fn=app.export_data("csv")
        self.assertTrue(s.startswith("\ufeff"))
        self.assertIn("category",s)

    # ---- v0.2: 場所・移動と活動の独立 ----
    def test_travel_is_place_lane_and_goal_is_not_a_timed_research_slot(self):
        tue=app.get_day("2026-11-24")
        travel=next(s for s in tue["plan"] if s["start"]=="08:00" and s["kind"]=="travel")
        self.assertEqual(travel["lane"],"place")
        self.assertEqual(travel["goal_minutes"],90)
        # 研究目標90分は、活動段に「08:00〜09:30 研究」のような確定枠として存在しない
        acts=[s for s in tue["plan"] if s["lane"]=="activity"]
        self.assertFalse(any(s["kind"]=="research" and s["start"]<"10:00" and s["end"]>"08:00" for s in acts))
        self.assertTrue(all(s["lane"]=="activity" for s in tue["plan"] if s["kind"] in ("fixed","break","margin","research")))
        self.assertEqual(app.summary("2026-11-24","2026-11-24")["total"]["research_goal"],90)

    def test_research_and_philosophy_while_travelling(self):
        day="2026-12-01"
        place=app.save_place_log({"day":day,"start":"08:00","end":"10:00","kind":"travel","memo":"通学"})
        r=app.save_log({"day":day,"start":"08:20","end":"09:20","category":"research","theme":"DoNet","content":"論文調査"})
        p=app.save_log({"day":day,"start":"09:25","end":"09:55","category":"philosophy","theme":"第二の性","content":"読書"})
        # 場所と活動の重なりは重複扱いしない
        self.assertEqual(r["overlap_count"],0)
        self.assertEqual(p["overlap_count"],0)
        d=app.get_day(day)
        self.assertEqual(len(d["place_logs"]),1)
        self.assertEqual(len(d["logs"]),2)
        t=app.summary(day,day)["total"]
        self.assertEqual(t["research"],60)      # 移動120分は加算されない（二重計上しない）
        self.assertEqual(t["philosophy"],30)
        self.assertEqual(t["places"]["travel"],120)
        app.remove_log(r["saved_id"]);app.remove_log(p["saved_id"]);app.remove_place_log(place["saved_id"])

    def test_place_log_crud_and_validation(self):
        day="2026-12-02"
        res=app.save_place_log({"day":day,"start":"10:00","end":"12:00","kind":"lab"})
        pid=res["saved_id"]
        self.assertEqual(res["day"]["place_logs"][0]["kind"],"lab")
        upd=app.save_place_log({"day":day,"start":"10:30","end":"12:00","kind":"home","memo":"在宅に変更"},pid)
        self.assertEqual(upd["day"]["place_logs"][0]["start"],"10:30")
        self.assertEqual(upd["day"]["place_logs"][0]["memo"],"在宅に変更")
        self.assertEqual(app.remove_place_log(pid)["place_logs"],[])
        with self.assertRaises(app.BadRequest):app.save_place_log({"day":day,"start":"10:00","end":"11:00","kind":"research"})
        with self.assertRaises(app.BadRequest):app.save_place_log({"day":day,"start":"11:00","end":"10:00","kind":"home"})
        with self.assertRaises(app.BadRequest):app.remove_place_log(pid)

    def test_place_plans_date_and_weekly(self):
        day="2026-12-03"  # 木曜
        d=app.mutate_plan({"date":day,"scope":"date","slot":{"start":"07:00","end":"11:00","kind":"home","label":""}},"POST")
        self.assertTrue(any(s["kind"]=="home" and s["lane"]=="place" and s["label"]=="" for s in d["plan"]))
        self.assertEqual(d["plan_source"],"override")
        self.assertEqual(app.summary(day,day)["total"]["research_goal"],240)  # 場所の予定は研究目標を変えない
        app.reset_day(day)
        w=app.mutate_plan({"date":day,"scope":"weekly","slot":{"start":"07:00","end":"11:00","kind":"lab"}},"POST")
        slot=next(s for s in w["plan"] if s["kind"]=="lab")
        self.assertTrue(any(s["kind"]=="lab" for s in app.get_day("2026-12-10")["plan"]))
        app.mutate_plan({"date":day,"scope":"weekly","id":slot["id"]},"DELETE")
        with self.assertRaises(app.BadRequest):  # 活動の予定は名前が必須
            app.mutate_plan({"date":day,"scope":"date","slot":{"start":"07:00","end":"08:00","kind":"research","label":""}},"POST")

    # ---- v0.3: 曜日ごとの別パターン ----
    def test_tuesday_pattern_is_applied_on_nov17(self):
        nov17=app.get_day("2026-11-17")
        self.assertEqual(nov17["applied_pattern"],"輪読なし")
        self.assertEqual([p["name"] for p in nov17["patterns"]],["輪読なし"])
        self.assertIsNone(app.get_day("2026-11-24")["applied_pattern"])

    def test_friday_no_seminar_pattern_apply_and_release(self):
        day="2026-12-11"  # 金曜
        before=app.get_day(day)
        self.assertEqual([p["name"] for p in before["patterns"]],["ゼミなし"])
        self.assertTrue(any(s["label"]=="ゼミ" for s in before["plan"]))
        pid=before["patterns"][0]["id"]
        d=app.apply_pattern({"date":day,"pattern_id":pid})
        self.assertEqual(d["plan_source"],"override")
        self.assertEqual(d["applied_pattern"],"ゼミなし")
        self.assertFalse(any(s["label"]=="ゼミ" or s["kind"]=="travel" for s in d["plan"]))  # 通学・ゼミなし（土曜型）
        self.assertEqual(app.summary(day,day)["total"]["research_goal"],360)
        # パターン適用後に「この日だけ」編集してもパターン名は残る
        slot=d["plan"][0]
        e=app.mutate_plan({"date":day,"scope":"date","id":slot["id"],"slot":{**slot,"start":"07:30","goal_minutes":120}},"PATCH")
        self.assertEqual(e["applied_pattern"],"ゼミなし")
        # 他の金曜には影響しない
        self.assertTrue(any(s["label"]=="ゼミ" for s in app.get_day("2026-12-18")["plan"]))
        # 解除で通常に戻る
        r=app.reset_day(day)
        self.assertEqual((r["plan_source"],r["applied_pattern"]),("weekly",None))
        self.assertTrue(any(s["label"]=="ゼミ" for s in r["plan"]))
        with self.assertRaises(app.BadRequest):app.apply_pattern({"date":"2026-12-10","pattern_id":pid})  # 木曜には適用不可
        with self.assertRaises(app.BadRequest):app.apply_pattern({"date":day,"pattern_id":9999})

    # ---- 実績の活動に固定予定・休憩 ----
    def test_fixed_and_break_logs(self):
        day="2027-01-11"
        themes_before=len(app.get_themes())
        f=app.save_log({"day":day,"start":"13:00","end":"18:00","category":"fixed","theme":"無視される","content":"オンラインバイト"})
        b=app.save_log({"day":day,"start":"12:00","end":"13:00","category":"break","content":""})  # 作業内容は任意
        r=app.save_log({"day":day,"start":"07:00","end":"09:00","category":"research","content":"実装"})
        logs={x["category"]:x for x in app.get_day(day)["logs"]}
        self.assertIsNone(logs["fixed"]["theme"])
        self.assertEqual(logs["break"]["content"],"")
        self.assertEqual(len(app.get_themes()),themes_before)  # テーマは作られない
        t=app.summary(day,day)["total"]
        self.assertEqual((t["research"],t["philosophy"],t["llm"]),(120,0,0))  # 研究・哲学・LLMには加算しない
        self.assertEqual((t["fixed"],t["break"]),(300,60))
        with self.assertRaises(app.BadRequest):  # 研究は作業内容が必須のまま
            app.save_log({"day":day,"start":"09:00","end":"10:00","category":"research","content":""})
        with self.assertRaises(app.BadRequest):
            app.save_log({"day":day,"start":"09:00","end":"10:00","category":"margin","content":"x"})
        for x in (f,b,r):app.remove_log(x["saved_id"])

    # ---- 編集モード：まとめて保存 ----
    def test_bulk_replace_date_scope(self):
        day="2027-01-06"  # 水曜
        slots=app.get_day(day)["plan"]
        slots[0]={**slots[0],"end":"09:00","goal_minutes":120}                 # 研究 7:00-9:30 → 7:00-9:00
        slots=[s for s in slots if s["label"]!="休憩"]                         # 削除
        slots.append({"start":"21:00","end":"22:00","label":"","kind":"home","goal_minutes":0})  # 追加（場所）
        d=app.replace_plans({"date":day,"scope":"date","slots":slots})
        self.assertEqual(d["plan_source"],"override")
        self.assertTrue(any(s["start"]=="07:00" and s["end"]=="09:00" and s["goal_minutes"]==120 for s in d["plan"]))
        self.assertFalse(any(s["label"]=="休憩" for s in d["plan"]))
        self.assertTrue(any(s["kind"]=="home" and s["lane"]=="place" for s in d["plan"]))
        self.assertEqual(len({s["id"] for s in d["plan"]}),len(d["plan"]))
        self.assertTrue(any(s["label"]=="休憩" for s in app.get_day("2027-01-13")["plan"]))  # 他の水曜は不変
        with self.assertRaises(app.BadRequest):  # 毎週としては保存できない（この日は変更済み）
            app.replace_plans({"date":day,"scope":"weekly","slots":slots})
        app.reset_day(day)

    def test_bulk_replace_weekly_scope_and_validation(self):
        day="2027-01-02"  # 土曜
        before=app.get_day(day)["plan"]
        n=len(before)
        slots=[{**s,"end":"10:00","goal_minutes":180} if s["start"]=="07:00" else s for s in before if s["label"]!="個人開発（任意）"]
        slots.append({"start":"21:00","end":"22:00","label":"読書","kind":"philosophy","goal_minutes":0})
        d=app.replace_plans({"date":day,"scope":"weekly","slots":slots})
        self.assertEqual(d["plan_source"],"weekly")
        other=app.get_day("2027-01-09")["plan"]  # 次の土曜にも反映
        self.assertTrue(any(s["start"]=="07:00" and s["end"]=="10:00" for s in other))
        self.assertFalse(any(s["label"]=="個人開発（任意）" for s in other))
        self.assertTrue(any(s["label"]=="読書" for s in other))
        self.assertEqual(len(other),n)
        # 不正な予定が1つでもあれば何も書き込まない
        bad=other+[{"start":"12:00","end":"11:00","label":"x","kind":"research"}]
        with self.assertRaises(app.BadRequest):app.replace_plans({"date":day,"scope":"weekly","slots":bad})
        self.assertEqual(len(app.get_day(day)["plan"]),n)
        with self.assertRaises(app.BadRequest):app.replace_plans({"date":day,"scope":"month","slots":[]})
        app.replace_plans({"date":day,"scope":"weekly","slots":before})  # 元に戻す

    def test_bulk_keeps_pattern_name(self):
        day="2027-01-08"  # 金曜
        pid=app.get_day(day)["patterns"][0]["id"]
        d=app.apply_pattern({"date":day,"pattern_id":pid})
        slots=[{**s,"end":"09:00","goal_minutes":120} if s["start"]=="07:00" else s for s in d["plan"]]
        d=app.replace_plans({"date":day,"scope":"date","slots":slots})
        self.assertEqual(d["applied_pattern"],"ゼミなし")
        app.reset_day(day)

    def test_http_place_logs_and_exports(self):
        import threading, urllib.request
        from http.server import ThreadingHTTPServer
        httpd=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        threading.Thread(target=httpd.serve_forever,daemon=True).start()
        base=f"http://127.0.0.1:{httpd.server_address[1]}"
        def call(path,method="GET",body=None):
            req=urllib.request.Request(base+path,method=method,data=None if body is None else json.dumps(body).encode(),
                                       headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req) as r:return r.read().decode("utf-8")
        try:
            out=json.loads(call("/api/place-logs","POST",{"day":"2026-12-04","start":"08:00","end":"09:00","kind":"travel"}))
            pid=out["saved_id"]
            self.assertEqual(json.loads(call("/api/day?date=2026-12-04"))["place_logs"][0]["kind"],"travel")
            self.assertIn("travel",call("/api/export?format=places_csv"))
            self.assertEqual(json.loads(call("/api/export?format=json"))["place_logs"][-1]["id"],pid)
            self.assertIn("category",call("/api/export?format=csv"))
            self.assertIn("day_patterns",json.loads(call("/api/export?format=json")))
            fri=json.loads(call("/api/day?date=2026-12-25"))
            applied=json.loads(call("/api/day-pattern","POST",{"date":"2026-12-25","pattern_id":fri["patterns"][0]["id"]}))
            self.assertEqual(applied["applied_pattern"],"ゼミなし")
            self.assertEqual(json.loads(call("/api/day-override","DELETE",{"date":"2026-12-25"}))["plan_source"],"weekly")
            call(f"/api/place-logs/{pid}","DELETE",{})
        finally:
            httpd.shutdown();httpd.server_close()


class MigrationTests(unittest.TestCase):
    """v0.1 形式のDB（place_logs なし、schema_version なし）を移行する。"""
    V1_SCHEMA="""
        CREATE TABLE weekly_plans (id INTEGER PRIMARY KEY AUTOINCREMENT, weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
            start TEXT NOT NULL, end TEXT NOT NULL, label TEXT NOT NULL, kind TEXT NOT NULL, goal_minutes INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE day_overrides (day TEXT PRIMARY KEY, slots_json TEXT NOT NULL);
        CREATE TABLE themes (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, name TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0, UNIQUE(category, name));
        CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
            category TEXT NOT NULL, theme_id INTEGER REFERENCES themes(id) ON DELETE SET NULL, content TEXT NOT NULL,
            memo TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """

    def setUp(self):
        import sqlite3
        self.tmp=tempfile.TemporaryDirectory()
        self.saved=(app.DATA_DIR,app.DB_PATH)
        app.DATA_DIR=Path(self.tmp.name);app.DB_PATH=app.DATA_DIR/"focuslog.db"
        con=sqlite3.connect(str(app.DB_PATH))
        con.executescript(self.V1_SCHEMA)
        con.executemany("INSERT INTO weekly_plans(weekday,start,end,label,kind,goal_minutes) VALUES (?,?,?,?,?,?)",app.default_weekly())
        con.execute("UPDATE weekly_plans SET label='通学（研究可能：最大1.5h）' WHERE kind='travel' AND goal_minutes=90")  # v0.1 の初期名称
        con.execute("INSERT INTO themes(category,name) VALUES ('research','DoNet'),('philosophy','第二の性'),('llm','自作テーマ')")
        con.execute("INSERT INTO logs(day,start,end,category,theme_id,content) VALUES ('2026-11-10','08:20','09:20','research',1,'旧データ')")
        slots=[{"id":"w99","weekly_id":99,"start":"10:00","end":"12:00","label":"研究・修論（輪読なし）","kind":"research","goal_minutes":120},
               {"id":"w98","weekly_id":98,"start":"08:00","end":"10:00","label":"通学（研究可能：最大1.5h）","kind":"travel","goal_minutes":90}]
        con.execute("INSERT INTO day_overrides(day,slots_json) VALUES ('2026-11-17',?)",(json.dumps(slots,ensure_ascii=False),))
        con.execute("INSERT INTO preferences VALUES ('timeline_start','06:00'),('timeline_end','23:00')")
        con.commit();con.close()

    def tearDown(self):
        app.DATA_DIR,app.DB_PATH=self.saved
        self.tmp.cleanup()

    def counts(self):
        with app.db() as con:
            return {t:con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("weekly_plans","day_overrides","themes","logs","place_logs")}

    def test_migrate_v1_database(self):
        backup=app.init_db()
        self.assertIsNotNone(backup)
        self.assertTrue(Path(backup).exists())
        import sqlite3
        b=sqlite3.connect(str(backup))  # バックアップは移行前の状態
        self.assertIsNone(b.execute("SELECT 1 FROM sqlite_master WHERE name='place_logs'").fetchone())
        self.assertEqual(b.execute("SELECT COUNT(*) FROM logs").fetchone()[0],1);b.close()
        c=self.counts()
        self.assertEqual(c["weekly_plans"],len(app.default_weekly()))
        self.assertEqual(c["themes"],8)      # サンプルで上書きしない（v4 で 哲学・LLM・通常研究・修論・論文調査 を追加）
        with app.db() as con:
            active={r[0] for r in con.execute("SELECT name FROM themes WHERE category='research' AND archived=0")}
            archived={r[0] for r in con.execute("SELECT name FROM themes WHERE category='research' AND archived=1")}
        self.assertEqual(active,{"通常研究","修論","論文調査"})
        self.assertEqual(archived,{"DoNet"})  # 削除せずアーカイブ（過去の記録のテーマ名は残る）
        self.assertEqual(c["logs"],1)
        self.assertEqual(c["day_overrides"],1)
        self.assertEqual(c["place_logs"],0)  # 場所を推測して登録しない
        old=app.get_day("2026-11-10")["logs"][0]
        self.assertEqual((old["content"],old["theme"],old["start"]),("旧データ","DoNet","08:20"))
        nov17=app.get_day("2026-11-17")
        self.assertEqual(nov17["plan_source"],"override")
        self.assertEqual({s["kind"]:s["lane"] for s in nov17["plan"]},{"research":"activity","travel":"place"})
        self.assertEqual(sum(s["goal_minutes"] for s in nov17["plan"]),210)
        # 旧名称「通学（研究可能：最大1.5h）」は「通学」に置き換わる（毎週・例外日とも。研究目標90分は維持）
        self.assertTrue(any(s["label"]=="通学" and s["goal_minutes"]==90 for s in nov17["plan"]))
        tue=app.get_day("2026-11-24")["plan"]
        self.assertTrue(any(s["label"]=="通学" and s["goal_minutes"]==90 for s in tue))
        self.assertFalse(any("研究可能" in s["label"] for s in tue+nov17["plan"]))
        # v3: パターンの登録と、既存の 11/17 をパターン適用日として扱う
        self.assertEqual(nov17["applied_pattern"],"輪読なし")
        self.assertEqual([p["name"] for p in app.get_day("2026-12-11")["patterns"]],["ゼミなし"])
        with app.db() as con:
            self.assertEqual(con.execute("SELECT value FROM preferences WHERE key='schema_version'").fetchone()[0],"4")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM day_patterns").fetchone()[0],2)
        self.assertEqual(app.get_day("2026-11-17")["preferences"]["timeline_start"],"06:00")
        # 再実行してもバックアップ・重複は発生しない
        self.assertIsNone(app.init_db())
        self.assertEqual(self.counts(),c)
        self.assertEqual(len(list((app.DATA_DIR/"backups").iterdir())),1)

    def test_migrate_v2_to_v3(self):
        app.init_db()  # v1→v3
        app.save_place_log({"day":"2026-11-13","start":"08:00","end":"10:00","kind":"travel"})
        with app.db() as con:  # v2 の状態を再現（パターン機能なし）
            con.execute("DROP TABLE day_patterns")
            con.execute("UPDATE day_overrides SET pattern_name=NULL")
            con.execute("UPDATE preferences SET value='2' WHERE key='schema_version'")
        before=self.counts()
        backup=app.init_db()
        self.assertIsNotNone(backup)
        self.assertEqual(self.counts(),before)
        self.assertEqual(app.get_day("2026-11-17")["applied_pattern"],"輪読なし")
        self.assertEqual(len(app.get_day("2026-11-20")["patterns"]),1)
        self.assertIsNone(app.init_db())

    def test_migrate_v3_to_v4_renames_reading_and_dev(self):
        app.init_db()
        with app.db() as con:  # v3 の状態を再現（旧名称・テーマなし）
            con.execute("UPDATE weekly_plans SET label='哲学（任意）' WHERE label='読書（任意）'")
            con.execute("UPDATE weekly_plans SET label='LLM（任意）' WHERE label='個人開発（任意）'")
            con.execute("UPDATE weekly_plans SET label='マージン（哲学・LLM・休息）' WHERE label LIKE 'マージン（読書%'")
            for t in ("day_patterns","day_overrides"):
                key="id" if t=="day_patterns" else "day"
                for k,js in con.execute(f"SELECT {key},slots_json FROM {t}").fetchall():
                    con.execute(f"UPDATE {t} SET slots_json=? WHERE {key}=?",(js.replace("読書（任意）","哲学（任意）").replace("個人開発（任意）","LLM（任意）"),k))
            con.execute("DELETE FROM themes WHERE name IN ('哲学','LLM')")
            con.execute("UPDATE preferences SET value='3' WHERE key='schema_version'")
        self.assertIsNotNone(app.init_db())  # バックアップあり
        with app.db() as con:
            dump=" ".join(r[0] for r in con.execute("SELECT label FROM weekly_plans"))
            dump+=" ".join(r[0] for r in con.execute("SELECT slots_json FROM day_patterns"))
            dump+=" ".join(r[0] for r in con.execute("SELECT slots_json FROM day_overrides"))
            themes={(r[0],r[1]) for r in con.execute("SELECT category,name FROM themes")}
        self.assertNotIn("哲学（任意）",dump);self.assertNotIn("LLM（任意）",dump);self.assertNotIn("マージン（哲学",dump)
        self.assertIn("読書（任意）",dump);self.assertIn("個人開発（任意）",dump)
        self.assertIn(("philosophy","哲学"),themes);self.assertIn(("llm","LLM"),themes)
        self.assertTrue({("research","通常研究"),("research","修論"),("research","論文調査")}<=themes)
        # 移行後にユーザーが付けた旧名称は再起動で書き換えない
        with app.db() as con:con.execute("UPDATE weekly_plans SET label='哲学（任意）' WHERE label='読書（任意）'")
        self.assertIsNone(app.init_db())
        with app.db() as con:
            self.assertTrue(con.execute("SELECT 1 FROM weekly_plans WHERE label='哲学（任意）'").fetchone())

    def test_reset_exception_is_not_resurrected_on_restart(self):
        app.init_db()
        app.reset_day("2026-11-17")
        app.init_db()
        self.assertEqual(app.get_day("2026-11-17")["plan_source"],"weekly")


if __name__=="__main__":unittest.main()
