from __future__ import annotations

from html import escape


def render_markdown(markdown: str) -> str:
    lines = markdown.splitlines()
    html: list[str] = []
    paragraph: list[str] = []
    list_open = False
    table_open = False
    code_open = False

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            html.append(f"<p>{escape(' '.join(paragraph))}</p>")
            paragraph = []

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            html.append("</ul>")
            list_open = False

    def close_table() -> None:
        nonlocal table_open
        if table_open:
            html.append("</tbody></table>")
            table_open = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            close_list()
            close_table()
            if code_open:
                html.append("</code></pre>")
                code_open = False
            else:
                html.append("<pre><code>")
                code_open = True
            continue

        if code_open:
            html.append(escape(line))
            continue

        if not stripped:
            flush_paragraph()
            close_list()
            close_table()
            continue

        if stripped.startswith("#"):
            flush_paragraph()
            close_list()
            close_table()
            level = min(len(stripped) - len(stripped.lstrip("#")), 6)
            text = stripped[level:].strip()
            html.append(f"<h{level}>{escape(text)}</h{level}>")
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            close_list()
            close_table()
            html.append(f"<blockquote>{escape(stripped[1:].strip())}</blockquote>")
            continue

        if is_table_row(stripped):
            flush_paragraph()
            close_list()
            cells = split_table_cells(stripped)
            if all(set(cell) <= {"-", " "} for cell in cells):
                continue
            if not table_open:
                html.append("<table><tbody>")
                table_open = True
            html.append("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in cells) + "</tr>")
            continue

        close_table()

        if stripped.startswith("- "):
            flush_paragraph()
            if not list_open:
                html.append("<ul>")
                list_open = True
            html.append(f"<li>{escape(stripped[2:].strip())}</li>")
            continue

        close_list()
        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    close_table()
    if code_open:
        html.append("</code></pre>")
    return "\n".join(html)


def is_table_row(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and line.count("|") >= 2


def split_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip("|").split("|")]

