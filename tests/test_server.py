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

if __name__=="__main__":unittest.main()
