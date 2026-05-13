import unittest

from agentflow_studio.markdown import render_markdown


class MarkdownTests(unittest.TestCase):
    def test_renders_chinese_markdown(self) -> None:
        html = render_markdown("# 标题\n\n- 玩法规则\n")

        self.assertIn("标题", html)
        self.assertIn("玩法规则", html)
        self.assertIn("<h1>", html)
        self.assertIn("<li>", html)


if __name__ == "__main__":
    unittest.main()

