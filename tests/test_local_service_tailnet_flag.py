"""local-service.py passes JASMINE_TAILNET_CLIENTS to Core only when runtime.json opts in."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deployment" / "local-service.py"
COMMIT = "a" * 40


class LocalServiceTailnetFlagTests(unittest.TestCase):
    def core_env(self, **runtime):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "releases" / COMMIT / "scripts" / "deployment").mkdir(parents=True)
            (base / "data").mkdir()
            dump = base / "env.json"
            stub = base / "python-stub"
            stub.write_text("#!%s\nimport json,os\njson.dump(dict(os.environ),open(%r,'w'))\n" % (sys.executable, str(dump)))
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
            config = {"commit": COMMIT, "release": str(base / "releases" / COMMIT), "database": str(base / "data" / "core.db"),
                      "python": str(stub), "workspace": tmp, "producer_config": tmp, "extra_paths": [],
                      "tokenizer_cache": tmp, "validation_root": tmp, **runtime}
            (base / "config").mkdir()
            (base / "config" / "runtime.json").write_text(json.dumps(config))
            subprocess.run([sys.executable, str(SCRIPT), "--base", str(base), "core"], check=True, timeout=30)
            return json.loads(dump.read_text())

    def test_flag_is_set_when_runtime_opts_in(self):
        self.assertEqual(self.core_env(tailnet_clients=True).get("JASMINE_TAILNET_CLIENTS"), "1")

    def test_flag_is_absent_by_default_and_for_non_true_values(self):
        self.assertNotIn("JASMINE_TAILNET_CLIENTS", self.core_env())
        self.assertNotIn("JASMINE_TAILNET_CLIENTS", self.core_env(tailnet_clients="yes"))


if __name__ == "__main__":
    unittest.main()
