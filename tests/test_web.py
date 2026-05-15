import tempfile
import unittest
from pathlib import Path

from agentflow_studio.web import render_artifact_body


class WebRenderTests(unittest.TestCase):
    def test_html_artifact_renders_preview_iframe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            artifact = root / "docs/prd/09-review.html"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("<!doctype html><title>Review</title>", encoding="utf-8")
            state = {"artifacts": {"review_html": "docs/prd/09-review.html"}}

            html = render_artifact_body(root, state, "review_html")

            self.assertIn("HTML Preview", html)
            self.assertIn('class="html-preview"', html)
            self.assertIn('src="/raw/docs/prd/09-review.html"', html)
            self.assertNotIn("&lt;!doctype html&gt;", html)


if __name__ == "__main__":
    unittest.main()
