"""R4 corpus capture: bounded-capture target documents into the frozen corpus
archive (/home/pythia/pythia-evidence-corpus) and maintain corpus_manifest.json.

Idempotent: a target whose source_id already has a receipt in the corpus
archive is skipped. Each run may add a family batch. Labels start as
skeletons (expected_facts filled in the labeling step).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from python.evaluation.article_acquisition import archive_article

ARCHIVE_DIR = Path("/home/pythia/pythia-evidence-corpus")
OUT_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = OUT_DIR / "corpus_manifest.json"

# doc: (source_id, url, family, language, origin_entity)
TARGETS: List[Dict[str, str]] = [
    # FOMC statements across years (deterministic Fed URLs)
    *[
        {
            "source_id": f"fed_stmt_{d}",
            "url": f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{d}a.htm",
            "family": "fomc_statement",
            "language": "en",
            "origin_entity": "Federal Reserve Board",
        }
        for d in (
            "20230201", "20230726", "20231213",
            "20240612", "20240918", "20241218",
            "20250319", "20250618", "20251029",
            "20260128", "20260429", "20260617",
        )
    ],
    # Feeds and data endpoints
    {"source_id": "nyfed_effr25", "url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/25.json", "family": "reference_rates", "language": "en", "origin_entity": "Federal Reserve Bank of New York"},
    {"source_id": "nyfed_sofr", "url": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.json", "family": "reference_rates", "language": "en", "origin_entity": "Federal Reserve Bank of New York"},
    {"source_id": "fed_rss_monetary", "url": "https://www.federalreserve.gov/feeds/press_monetary.xml", "family": "rss_feed", "language": "en", "origin_entity": "Federal Reserve Board"},
    {"source_id": "fed_rss_speeches", "url": "https://www.federalreserve.gov/feeds/speeches.xml", "family": "rss_feed", "language": "en", "origin_entity": "Federal Reserve Board"},
    {"source_id": "fed_rss_all", "url": "https://www.federalreserve.gov/feeds/press_all.xml", "family": "rss_feed", "language": "en", "origin_entity": "Federal Reserve Board"},
    {"source_id": "ecb_rss_press", "url": "https://www.ecb.europa.eu/rss/press.xml", "family": "rss_feed", "language": "en", "origin_entity": "European Central Bank"},
    {"source_id": "gdpnow_page", "url": "https://www.atlantafed.org/research-and-data/data/gdpnow", "family": "data_page", "language": "en", "origin_entity": "Federal Reserve Bank of Atlanta"},
    {"source_id": "hf_deepseek", "url": "https://huggingface.co/api/models?author=deepseek-ai&sort=lastModified&direction=-1&limit=3", "family": "ai_models_api", "language": "en", "origin_entity": "Hugging Face"},
    {"source_id": "hf_qwen", "url": "https://huggingface.co/api/models?author=Qwen&sort=lastModified&direction=-1&limit=3", "family": "ai_models_api", "language": "en", "origin_entity": "Hugging Face"},
    {"source_id": "github_deepseek_v3", "url": "https://api.github.com/repos/deepseek-ai/DeepSeek-V3/releases?per_page=2", "family": "ai_releases_api", "language": "en", "origin_entity": "GitHub"},
    {"source_id": "github_llama_cpp", "url": "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=2", "family": "ai_releases_api", "language": "en", "origin_entity": "GitHub"},
    {"source_id": "github_transformers", "url": "https://api.github.com/repos/huggingface/transformers/releases?per_page=2", "family": "ai_releases_api", "language": "en", "origin_entity": "GitHub"},
    {"source_id": "cbr_rss", "url": "https://www.cbr.ru/rss/RssNews", "family": "rss_feed", "language": "ru", "origin_entity": "Bank of Russia"},
    # Fed speeches (from the speeches RSS)
    *[
        {"source_id": f"fed_speech_{s}", "url": f"https://www.federalreserve.gov/newsevents/speech/{s}.htm", "family": "fed_speech", "language": "en", "origin_entity": "Federal Reserve Board"}
        for s in ("bowman20261014a", "waller20261008a", "bowman20261006a", "bowman20261001a", "jefferson20261001a", "waller20261001a", "cook20260930a", "waller20260928a")
    ],
    # ECB articles (from the ECB press RSS)
    *[
        {"source_id": f"ecb_art_{s}", "url": u, "family": "ecb_article", "language": "en", "origin_entity": "European Central Bank"}
        for s, u in (
            ("sp261021", "https://www.ecb.europa.eu/press/key/date/2026/html/ecb.sp261021~401d6c4b70.en.html"),
            ("sp261019", "https://www.ecb.europa.eu/press/key/date/2026/html/ecb.sp261019~f0a3e053de.en.html"),
            ("sp261017", "https://www.ecb.europa.eu/press/key/date/2026/html/ecb.sp261017~7015ea3099.en.html"),
            ("sp261013", "https://www.ecb.europa.eu/press/key/date/2026/html/ecb.sp261013~9db9bf4fd6.en.html"),
            ("mg261008", "https://www.ecb.europa.eu/press/accounts/2026/html/ecb.mg261008~a10153d090.en.html"),
            ("pr261007", "https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.pr261007~6447350434.en.html"),
        )
    ],
    # PBOC news releases (from the official listing)
    *[
        {"source_id": f"pboc_{p}", "url": f"https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/{p}/index.html", "family": "pboc_release", "language": "zh", "origin_entity": "People's Bank of China"}
        for p in ("2026101418371517078", "2026101510063698366", "2026101517581023878", "2026101614165534664", "2026101618323611588", "2026101622061793966", "2026101712191431436", "2026101715340431532", "2026101718262172570", "2026101916082228353")
    ],
    # NBS latest releases
    *[
        {"source_id": f"nbs_{p}", "url": f"https://www.stats.gov.cn/sj/zxfb/{u}", "family": "nbs_release", "language": "zh", "origin_entity": "National Bureau of Statistics of China"}
        for p, u in (
            ("1965449", "202609/t20260930_1965449.html"), ("1965465", "202610/t20261008_1965465.html"),
            ("1965476", "202610/t20261014_1965476.html"), ("1965477", "202610/t20261014_1965477.html"),
            ("1965512", "202610/t20261016_1965512.html"), ("1965609", "202610/t20261019_1965609.html"),
            ("1965611", "202610/t20261019_1965611.html"), ("1965612", "202610/t20261019_1965612.html"),
            ("1965613", "202610/t20261019_1965613.html"), ("1965614", "202610/t20261019_1965614.html"),
        )
    ],
    # Bank of Russia server-rendered data pages + the JS-shell press release
    *[
        {"source_id": f"cbr_{s}", "url": u, "family": "cbr_data_page", "language": "ru", "origin_entity": "Bank of Russia"}
        for s, u in (
            ("ruonia", "https://www.cbr.ru/hd_base/ruonia/"),
            ("mrrf", "https://www.cbr.ru/hd_base/mrrf/mrrf_7d/"),
            ("mkr", "https://www.cbr.ru/hd_base/mkr/mkr_base/"),
            ("impliedcny", "https://www.cbr.ru/hd_base/impliedratecny/"),
            ("reserves", "https://www.cbr.ru/statistics/macro_itm/external_sector/ir/"),
            ("swap", "https://www.cbr.ru/hd_base/swap_info/sell/"),
        )
    ],
    {"source_id": "cbr_press_shell", "url": "https://www.cbr.ru/press/pr/?file=12092025_1330_key.htm", "family": "cbr_press_release", "language": "ru", "origin_entity": "Bank of Russia"},
    # Batch 3: more statements, other Fed releases, minutes, model pages,
    # more GitHub repos, NBS English versions, CBR English pairs, PBOC English
    *[
        {"source_id": f"fed_stmt_{d}", "url": f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{d}a.htm", "family": "fomc_statement", "language": "en", "origin_entity": "Federal Reserve Board"}
        for d in ("20230322", "20231101", "20240131", "20240731", "20250129", "20250730")
    ],
    *[
        {"source_id": f"fed_other_{s}", "url": f"https://www.federalreserve.gov/newsevents/pressreleases/{s}.htm", "family": "fed_other_release", "language": "en", "origin_entity": "Federal Reserve Board"}
        for s in ("enforcement20261016a", "other20261009a", "enforcement20261008a", "orders20261005a", "bcreg20261002a", "bcreg20260930a")
    ],
    *[
        {"source_id": f"fomc_minutes_{d}", "url": f"https://www.federalreserve.gov/monetarypolicy/fomcminutes{d}.htm", "family": "fomc_minutes", "language": "en", "origin_entity": "Federal Reserve Board"}
        for d in ("20260128", "20251210")
    ],
    {"source_id": "hf_model_deepseek_v32", "url": "https://huggingface.co/deepseek-ai/DeepSeek-V3.2", "family": "ai_model_page", "language": "en", "origin_entity": "Hugging Face"},
    {"source_id": "hf_model_qwen3", "url": "https://huggingface.co/Qwen/Qwen3-235B-A22B", "family": "ai_model_page", "language": "en", "origin_entity": "Hugging Face"},
    {"source_id": "hf_model_deepseek_r1", "url": "https://huggingface.co/deepseek-ai/DeepSeek-R1", "family": "ai_model_page", "language": "en", "origin_entity": "Hugging Face"},
    {"source_id": "github_openai_python", "url": "https://api.github.com/repos/openai/openai-python/releases?per_page=2", "family": "ai_releases_api", "language": "en", "origin_entity": "GitHub"},
    {"source_id": "github_vllm", "url": "https://api.github.com/repos/vllm-project/vllm/releases?per_page=2", "family": "ai_releases_api", "language": "en", "origin_entity": "GitHub"},
    *[
        {"source_id": f"nbs_en_{p}", "url": f"https://www.stats.gov.cn/english/PressRelease/{u}", "family": "nbs_release_en", "language": "en", "origin_entity": "National Bureau of Statistics of China"}
        for p, u in (
            ("1965466", "202610/t20261008_1965466.html"), ("1965467", "202610/t20261008_1965467.html"),
            ("1965499", "202610/t20261015_1965499.html"), ("1965619", "202610/t20261019_1965619.html"),
        )
    ],
    {"source_id": "cbr_en_ruonia", "url": "https://www.cbr.ru/eng/hd_base/ruonia/", "family": "cbr_data_page_en", "language": "en", "origin_entity": "Bank of Russia"},
    {"source_id": "cbr_en_mrrf", "url": "https://www.cbr.ru/eng/hd_base/mrrf/mrrf_7d/", "family": "cbr_data_page_en", "language": "en", "origin_entity": "Bank of Russia"},
    *[
        {"source_id": f"pboc_en_{p}", "url": f"https://www.pbc.gov.cn/english/130721/{p}/index.html", "family": "pboc_release_en", "language": "en", "origin_entity": "People's Bank of China"}
        for p in ("2025080815070711218", "2025080815070716580", "2025080815070718301")
    ],
    # Batch 4: fill to the corpus target size
    *[
        {"source_id": f"fed_stmt_{d}", "url": f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{d}a.htm", "family": "fomc_statement", "language": "en", "origin_entity": "Federal Reserve Board"}
        for d in ("20220615", "20220921", "20221214", "20240501", "20251210", "20260318")
    ],
    *[
        {"source_id": f"fed_speech_{s}", "url": f"https://www.federalreserve.gov/newsevents/speech/{s}.htm", "family": "fed_speech", "language": "en", "origin_entity": "Federal Reserve Board"}
        for s in ("barr20260929a", "bowman20260929a")
    ],
    *[
        {"source_id": f"ecb_art_{s}", "url": u, "family": "ecb_article", "language": "en", "origin_entity": "European Central Bank"}
        for s, u in (
            ("in261020", "https://www.ecb.europa.eu/press/inter/date/2026/html/ecb.in261020~97e2e4bc38.en.html"),
            ("in261008", "https://www.ecb.europa.eu/press/inter/date/2026/html/ecb.in261008~3184e7d0d0.en.html"),
            ("sp261014", "https://www.ecb.europa.eu/press/key/date/2026/html/ecb.sp261014~e4ca33588e.en.html"),
            ("sp261006", "https://www.ecb.europa.eu/press/key/date/2026/html/ecb.sp261006~0da978f159.en.html"),
        )
    ],
    *[
        {"source_id": f"nbs_{p}", "url": f"https://www.stats.gov.cn/sj/zxfb/{u}", "family": "nbs_release", "language": "zh", "origin_entity": "National Bureau of Statistics of China"}
        for p, u in (("1965615", "202610/t20261019_1965615.html"), ("1965616", "202610/t20261019_1965616.html"))
    ],
    {"source_id": "hf_meta_llama", "url": "https://huggingface.co/api/models?author=meta-llama&sort=lastModified&direction=-1&limit=3", "family": "ai_models_api", "language": "en", "origin_entity": "Hugging Face"},
    {"source_id": "hf_mistral", "url": "https://huggingface.co/api/models?author=mistralai&sort=lastModified&direction=-1&limit=3", "family": "ai_models_api", "language": "en", "origin_entity": "Hugging Face"},
    {"source_id": "github_anthropic_sdk", "url": "https://api.github.com/repos/anthropics/anthropic-sdk-python/releases?per_page=2", "family": "ai_releases_api", "language": "en", "origin_entity": "GitHub"},
    {"source_id": "github_langchain", "url": "https://api.github.com/repos/langchain-ai/langchain/releases?per_page=2", "family": "ai_releases_api", "language": "en", "origin_entity": "GitHub"},
    {"source_id": "nyfed_effr5", "url": "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/5.json", "family": "reference_rates", "language": "en", "origin_entity": "Federal Reserve Bank of New York"},
    {"source_id": "nyfed_sofr25", "url": "https://markets.newyorkfed.org/api/rates/secured/sofr/last/25.json", "family": "reference_rates", "language": "en", "origin_entity": "Federal Reserve Bank of New York"},
    # Real blocked targets (negative controls, captured as failure receipts)
    {"source_id": "neg_bls_cpi_page", "url": "https://www.bls.gov/news.release/cpi.htm", "family": "negative_blocked", "language": "en", "origin_entity": "U.S. Bureau of Labor Statistics"},
    {"source_id": "neg_bls_cpi_rss", "url": "https://www.bls.gov/feed/cpi.rss", "family": "negative_blocked", "language": "en", "origin_entity": "U.S. Bureau of Labor Statistics"},
    {"source_id": "neg_arena", "url": "https://arena.ai/leaderboard/text/overall-no-style-control", "family": "negative_blocked", "language": "en", "origin_entity": "Arena"},
    {"source_id": "neg_fed_404", "url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20990101a.htm", "family": "negative_blocked", "language": "en", "origin_entity": "Federal Reserve Board"},
    # Batch 5: older statements (incl. the March 2020 emergency format) + 2 releases
    *[
        {"source_id": f"fed_stmt_{d}", "url": f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{d}a.htm", "family": "fomc_statement", "language": "en", "origin_entity": "Federal Reserve Board"}
        for d in ("20210428", "20210922", "20211215", "20220126", "20200303")
    ],
    *[
        {"source_id": f"fed_other_{s}", "url": f"https://www.federalreserve.gov/newsevents/pressreleases/{s}.htm", "family": "fed_other_release", "language": "en", "origin_entity": "Federal Reserve Board"}
        for s in ("bcreg20260929a", "orders20260925a")
    ],
    # Batch 6: the two statements present in the corpus monetary RSS (lineage)
    *[
        {"source_id": f"fed_stmt_{d}", "url": f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{d}a.htm", "family": "fomc_statement", "language": "en", "origin_entity": "Federal Reserve Board"}
        for d in ("20260916", "20260729")
    ],
]


def existing_source_ids() -> set:
    """Source ids with at least one COMPLETE receipt (failed attempts retry)."""
    db = ARCHIVE_DIR / "archive.db"
    if not db.exists():
        return set()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT DISTINCT source_id FROM receipts WHERE complete = 1").fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def main() -> None:
    registry = {t["source_id"]: t["url"] for t in TARGETS}
    have = existing_source_ids()
    manifest: Dict[str, Any] = {"documents": {}}
    if MANIFEST_PATH.exists():
        manifest = json.loads(MANIFEST_PATH.read_text())
    for t in TARGETS:
        sid = t["source_id"]
        if sid in have:
            print("skip (already captured):", sid)
            continue
        if t["family"] == "negative_blocked" and sid in manifest["documents"]:
            print("skip (one-shot negative evidence):", sid)
            continue
        result = archive_article(ARCHIVE_DIR, sid, registry, worker_timeout=30.0)
        manifest["documents"][sid] = {
            **t,
            "receipt_id": result["receipt_id"],
            "revision_id": result["revision_id"],
            "seq": result["seq"],
            "complete": result["complete"],
            "blob_sha256": result["blob_sha256"],
            "label": (manifest["documents"].get(sid) or {}).get("label"),
        }
        print("captured:", sid, "complete:", result["complete"])
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print("manifest documents:", len(manifest["documents"]))


if __name__ == "__main__":
    main()
