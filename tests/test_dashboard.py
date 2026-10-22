import sqlite3
import tempfile
from pathlib import Path
import unittest
from dashboard import SCHEMA, ingest, summary, page


class DashboardTests(unittest.TestCase):
    def test_idempotent_import_and_recurring_failures(self):
        with tempfile.TemporaryDirectory() as directory, sqlite3.connect(":memory:") as db:
            db.executescript(SCHEMA)
            file=Path(directory)/"junit.xml"
            file.write_text("<testsuite><testcase classname='math' name='add' time='0.1'/><testcase classname='math' name='divide' time='1.2'><failure message='bad'/></testcase></testsuite>")
            for i in range(3): ingest(db,"project<1>",f"build{i}",file)
            ingest(db,"project<1>","build2",file)
            data=summary(db)["project<1>"]
            self.assertEqual(len(data["history"]),3)
            self.assertEqual(data["history"][-1]["pass_rate"],.5)
            self.assertEqual(data["recurring_failures"],[{"name":"math.divide","failed_builds":3}])
            self.assertTrue(data["red"])
            markup=page({"project<1>":data})
            self.assertIn("project&lt;1&gt;",markup)
            self.assertNotIn("project<1>",markup)


if __name__ == "__main__": unittest.main()

