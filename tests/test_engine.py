import shutil
import unittest
from pathlib import Path

from agentflow_studio.engine import create_run, resume_run, step_run


class EngineTests(unittest.TestCase):
    def test_cocos_workflow_reaches_human_gate(self) -> None:
        source_root = Path(__file__).resolve().parents[1]

        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            shutil.copytree(source_root / "configs", root / "configs")
            state_path = create_run(
                root=root,
                workflow_path=root / "configs/workflows/cocos-game-dev.zh.yaml",
                goal="做一个俯视角抢车位小游戏",
                run_id="test-run",
                game_name="parking-space-test",
            )

            state = step_run(root=root, state_path=state_path)
            self.assertEqual(state["phase"], "loop_boundary")

            state = step_run(root=root, state_path=state_path)
            self.assertEqual(state["phase"], "gameplay_rules")

            state = step_run(root=root, state_path=state_path)
            self.assertEqual(state["phase"], "flows_acceptance")

            state = step_run(root=root, state_path=state_path)
            self.assertEqual(state["phase"], "human_rules_review")

            state = step_run(root=root, state_path=state_path)
            self.assertEqual(state["status"], "paused")
            self.assertIn("gameplay_handoff", state["artifacts"])

            state = resume_run(
                root=root,
                state_path=state_path,
                decision="approve",
                note="Approved for implementation.",
            )
            self.assertEqual(state["phase"], "cocos_implementation")


if __name__ == "__main__":
    unittest.main()

