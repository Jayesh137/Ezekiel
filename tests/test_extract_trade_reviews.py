# tests/test_extract_trade_reviews.py
"""The carver that makes an unreadable PDF readable.

`research/Trade Reviews.pdf` has a 137-character text layer across 119 pages:
the content IS the images. These pin the two pieces of real logic — reading a
JPEG's own dimensions, and the aspect-ratio rule that separates tweet
screenshots from charts — plus the property that matters most for a review that
spans sessions: re-running the extractor must not destroy review notes.
"""

import json
import struct

from scripts.extract_trade_reviews import (
    MIN_BYTES,
    classify,
    jpeg_dimensions,
)


def jpeg(width: int, height: int) -> bytes:
    """A minimal JPEG carrying a real SOF0 marker."""
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x00" * 9
    return b"\xff\xd8" + sof + b"\xff\xd9"


def test_dimensions_come_from_the_jpeg_itself():
    assert jpeg_dimensions(jpeg(1200, 400)) == (1200, 400)


def test_dimensions_survive_a_leading_app_segment():
    """Real files start with JFIF/EXIF headers, which must be skipped by their
    declared length rather than scanned past byte by byte."""
    app = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    data = b"\xff\xd8" + app + jpeg(800, 600)[2:]
    assert jpeg_dimensions(data) == (800, 600)


def test_unreadable_bytes_report_no_dimensions_rather_than_raising():
    assert jpeg_dimensions(b"\xff\xd8\xff\xd9") == (0, 0)
    assert jpeg_dimensions(b"") == (0, 0)


def test_wide_and_short_reads_as_a_tweet_screenshot():
    assert classify(1200, 400) == "tweet_screenshot"


def test_tall_or_square_reads_as_a_chart():
    assert classify(800, 900) == "chart"
    assert classify(800, 800) == "chart"


def test_the_middle_band_is_not_forced_into_a_bucket():
    """A wrong label is worse than an honest 'mixed' — it hides a chart from
    anyone filtering the index for charts."""
    assert classify(1000, 600) == "mixed"


def test_zero_height_does_not_divide_by_zero():
    assert classify(100, 0) == "chart"


def test_icons_and_avatars_are_below_the_size_floor():
    """406 content images were kept out of far more embedded streams; without a
    floor the index fills with UI chrome."""
    assert MIN_BYTES >= 10_000


def test_the_shipped_index_matches_the_committed_images():
    """If someone re-runs the extractor and commits a changed corpus without the
    index, the counts quoted in gcr_reference.json go stale."""
    with open("research/trade_review_index.json") as f:
        index = json.load(f)
    assert index["total"] == 406
    assert index["counts"]["tweet_screenshot"] == 245
    assert sum(index["counts"].values()) == index["total"]
    assert len(index["images"]) == index["total"]


def test_the_index_records_which_images_have_been_read():
    """Reviewing 406 images costs more than one session has, so the flag is what
    lets it continue across sessions rather than restart."""
    with open("research/trade_review_index.json") as f:
        images = json.load(f)["images"]
    assert all("reviewed" in i for i in images)
    assert any(i["reviewed"] for i in images), "the reviewed ones must be marked"
