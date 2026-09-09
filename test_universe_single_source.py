"""유니버스 조회는 universe.py 단일 출처여야 한다.

backtest_v4.py가 옛 위키 URL을 물고 있는 사본을 들고 있었고, 실패 시 raise가 아니라
[]를 반환해서 B 슬리브(NDX100)가 두 달간 통째로 비활성이었다 (2026-07-10 ~ 09-10).
백테스트는 조용히 CAGR 22% → 9%로 떨어진 채 계속 "성공"했다.

사본이 다시 생기는 걸 막는다. 네트워크는 안 탄다 — 소스만 읽는다.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).parent
FUNCS = {"get_sp500_tickers", "get_nasdaq100_tickers"}


def _live_py_files():
    """archive/는 죽은 코드라 뺀다."""
    return [
        p for p in ROOT.rglob("*.py")
        if "archive" not in p.parts and "venv" not in p.parts and p.name != "universe.py"
    ]


def test_no_ticker_scraper_copies():
    offenders = []
    for path in _live_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in FUNCS:
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.name}")
    assert not offenders, (
        "유니버스 조회 사본 발견 — universe.py에서 import해라:\n  " + "\n  ".join(offenders)
    )


def test_universe_raises_on_failure():
    """조용한 []가 이 버그를 두 달 숨겼다. 실패는 시끄러워야 한다."""
    src = (ROOT / "universe.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in FUNCS:
            returns_empty = any(
                isinstance(n, ast.Return)
                and isinstance(n.value, ast.List)
                and not n.value.elts
                for n in ast.walk(node)
            )
            raises = any(isinstance(n, ast.Raise) for n in ast.walk(node))
            assert raises, f"{node.name}: 실패 시 raise가 없다"
            assert not returns_empty, f"{node.name}: 빈 리스트를 반환한다 (조용한 실패)"
