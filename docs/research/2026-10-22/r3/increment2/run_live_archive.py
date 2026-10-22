"""R3.2 live acceptance run: capture the two FOMC statement articles into the
dedicated article archive and extract statement bundles from the archive.

Run ONCE on amper (2026-10-22) by the stage architect. Parent feed lineage
references are copied verbatim from the accepted R3.1.9 driver
(revision9/acquisition/bounded_research_driver.py, OFFICIAL_TARGETS).
Outputs: article_archive_run.json next to this script; bundles in ./bundles/.
"""

from __future__ import annotations

import json
from pathlib import Path

from python.evaluation.article_acquisition import (
    DEFAULT_ARTICLE_SOURCES,
    archive_article,
    extract_archived_article,
)

ARCHIVE_DIR = Path("/home/pythia/pythia-evidence-articles")
ARCHIVE_NAME = "pythia-evidence-articles"
OUT_DIR = Path(__file__).resolve().parent

PARENT_REFS = {
    "fed_article_monetary20260916a": {
        "archive_name": "live-r2-revision3",
        "receipt_id": "39bf5ee5-5918-489f-b9e1-dfcfcfbce96b",
        "revision_id": "26b40558-1014-47f2-9b10-d7b8007a07d0",
        "seq": 2,
        "feed_fact_id": "fact-b7cdc057cc6ec28d",
        "feed_blob_sha256": "69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1",
        "feed_item_locator": {"type": "xml_xpath", "path": "/rss/channel/item[3]"},
        "feed_published_at": "2026-09-16T18:00:00+00:00",
        "feed_raw_pub_date": "Wed, 16 Sep 2026 18:00:00 GMT",
        "parent_bundle_digest": "cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde",
        "parent_bundle_as_of_seq": 8,
    },
    "fed_article_monetary20260729a": {
        "archive_name": "live-r2-revision3",
        "receipt_id": "39bf5ee5-5918-489f-b9e1-dfcfcfbce96b",
        "revision_id": "26b40558-1014-47f2-9b10-d7b8007a07d0",
        "seq": 2,
        "feed_fact_id": "fact-23bc4c0fc0243b9e",
        "feed_blob_sha256": "69b638039ee490f01aea90ac4623d67f9041273322daa48a262c50fed94ca2b1",
        "feed_item_locator": {"type": "xml_xpath", "path": "/rss/channel/item[7]"},
        "feed_published_at": "2026-07-29T18:00:00+00:00",
        "feed_raw_pub_date": "Wed, 29 Jul 2026 18:00:00 GMT",
        "parent_bundle_digest": "cfa3f1f595092ba236949974a66710c5962d5b9c9cf42515dba21d58f98a3cde",
        "parent_bundle_as_of_seq": 8,
    },
}


def main() -> None:
    results = {}
    for source_id in DEFAULT_ARTICLE_SOURCES:
        archived = archive_article(ARCHIVE_DIR, source_id)
        extracted = extract_archived_article(
            ARCHIVE_DIR,
            archived["receipt_id"],
            archived["seq"],
            OUT_DIR / "bundles",
            archive_name=ARCHIVE_NAME,
            parent_feed_reference=PARENT_REFS[source_id],
            capture_audit=archived["audit"],
        )
        results[source_id] = {"archived": archived, "extracted": extracted}
        print(source_id, "complete:", archived["complete"], "bundle:", extracted["bundle_digest"][:16])
    out = OUT_DIR / "article_archive_run.json"
    out.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
