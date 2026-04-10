import importlib.util
import sys
import unittest
from pathlib import Path


class RunMultistepAssessmentSeriesImportTests(unittest.TestCase):
    def test_script_loads_when_invoked_by_path(self):
        repo_root = Path(__file__).resolve().parents[3]
        script_path = repo_root / "tools" / "scripts" / "run_multistep_assessment_series.py"
        project_root = str(script_path.resolve().parents[2])
        spec = importlib.util.spec_from_file_location("run_multistep_assessment_series_test_module", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module

        old_sys_path = list(sys.path)
        try:
            sys.path = [entry for entry in sys.path if Path(entry or ".").resolve() != Path(project_root).resolve()]
            spec.loader.exec_module(module)
        finally:
            sys.path = old_sys_path

        self.assertEqual(Path(project_root), module.PROJECT_ROOT)


if __name__ == "__main__":
    unittest.main()
