import io
import sys
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from novel_ingest import ingest, index_text


def test_gb18030_zip_preserves_order_and_flags_jump(tmp_path):
    archive = tmp_path / "source.zip"
    with ZipFile(archive, "w") as z:
        z.writestr("小说.txt", "第9章结尾\r\n甲\r\n第1章开篇\r\n乙".encode("gb18030"))
    result = ingest(archive, tmp_path / "out")
    assert result["chapter_count"] == 2
    assert result["sequence_jumps"] == [{"from": 9, "to": 1}]
    assert (tmp_path / "out" / "原始文本字节.txt").read_bytes().startswith(
        "第9章".encode("gb18030"))
    assert "第1章开篇\n乙" in (tmp_path / "out" / "阅读版-UTF8.txt").read_text()


def test_duplicate_body_is_detected_across_different_headings():
    body = "这是一段重复的剧情。" * 20
    result = index_text(f"第1章前篇\n{body}\n第2章后篇\n{body}\n")
    assert result["exact_body_duplicate_groups"] == [[1, 2]]
    assert result["near_duplicate_adjacent"][0]["jaccard_8gram"] == 1.0
