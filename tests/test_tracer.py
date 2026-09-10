

def test_tracer_queries_do_not_cap_the_block_range():
    """With sort=desc a literal endblock does not just truncate the tail — it
    returns the newest rows BELOW that block. On Arbitrum (past 501,000,000)
    that asked for recent activity and got years-old history, with no error."""
    from pathlib import Path
    source = Path("src/tracer.py").read_text(encoding="utf-8")
    assert '"endblock": 99999999' not in source
    assert source.count('"endblock": "latest"') >= 2
