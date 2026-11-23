#!/usr/bin/env python3
"""每週從 PubMed 抓取麻醉各次專科最新文獻，輸出 data/weeks/YYYY-MM-DD.json 與 data/index.json。

只用 Python 標準函式庫。可設環境變數：
  NCBI_API_KEY   NCBI API key（選填，可提高速率限制）
  NCBI_EMAIL     聯絡信箱（NCBI 建議提供）
  DAYS           抓取最近幾天（預設 7）
  END_DATE       結束日期 YYYY-MM-DD（預設昨天，台北時間）
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "subspecialties.json"
DATA = ROOT / "data"
WEEKS = DATA / "weeks"

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
API_KEY = os.environ.get("NCBI_API_KEY", "").strip()
EMAIL = os.environ.get("NCBI_EMAIL", "").strip()
DELAY = 0.12 if API_KEY else 0.4

GUIDELINE_TYPES = {"Guideline", "Practice Guideline", "Consensus Development Conference",
                   "Consensus Development Conference, NIH"}
META_TYPES = {"Meta-Analysis", "Systematic Review"}
REVIEW_TYPES = {"Review", "Scoping Review"}
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def eutils(endpoint, params, retries=4):
    params = dict(params, tool="anesth-digest")
    if API_KEY:
        params["api_key"] = API_KEY
    if EMAIL:
        params["email"] = EMAIL
    body = urllib.parse.urlencode(params).encode()
    for attempt in range(retries):
        try:
            time.sleep(DELAY)
            req = urllib.request.Request(EUTILS + endpoint, data=body,
                                         headers={"User-Agent": "anesth-digest/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            wait = 2 ** attempt * 2
            print(f"  ! {endpoint} 失敗（{e}），{wait}s 後重試", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"{endpoint} 多次重試仍失敗")


def search(term, mindate, maxdate, retmax):
    raw = eutils("esearch.fcgi", {
        "db": "pubmed", "term": term, "retmode": "json", "retmax": retmax,
        "datetype": "edat", "mindate": mindate, "maxdate": maxdate, "sort": "pub_date",
    })
    res = json.loads(raw)["esearchresult"]
    return res.get("idlist", []), int(res.get("count", 0))


def text_of(el):
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def parse_date(article):
    """回傳 (顯示用日期字串, 排序用 ISO 日期)。"""
    art = article.find("MedlineCitation/Article")
    ad = art.find("ArticleDate") if art is not None else None
    if ad is not None and ad.findtext("Year"):
        y, m, d = ad.findtext("Year"), ad.findtext("Month") or "1", ad.findtext("Day") or "1"
        iso = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
        return iso, iso
    pd = article.find("MedlineCitation/Article/Journal/JournalIssue/PubDate")
    if pd is not None:
        y = pd.findtext("Year")
        if y:
            mtxt = pd.findtext("Month") or ""
            m = MONTHS.get(mtxt[:3], int(mtxt) if mtxt.isdigit() else 1)
            d = int(pd.findtext("Day") or 1)
            disp = " ".join(x for x in [y, mtxt, pd.findtext("Day") or ""] if x)
            return disp, f"{int(y):04d}-{m:02d}-{d:02d}"
        md = pd.findtext("MedlineDate") or ""
        return md, (md[:4] + "-01-01") if md[:4].isdigit() else ""
    return "", ""


def entrez_date(article):
    for h in article.findall("PubmedData/History/PubMedPubDate"):
        if h.get("PubStatus") == "entrez":
            try:
                return f"{int(h.findtext('Year')):04d}-{int(h.findtext('Month')):02d}-{int(h.findtext('Day')):02d}"
            except (TypeError, ValueError):
                pass
    return ""


def classify(pubtypes):
    s = set(pubtypes)
    if s & GUIDELINE_TYPES:
        return "guideline"
    if s & META_TYPES:
        return "meta"
    if s & REVIEW_TYPES:
        return "review"
    return "original"


def parse_articles(xml_bytes):
    out = []
    root = ET.fromstring(xml_bytes)
    for a in root.findall("PubmedArticle"):
        mc = a.find("MedlineCitation")
        art = mc.find("Article")
        pmid = mc.findtext("PMID")
        abstract_parts = []
        for at in art.findall("Abstract/AbstractText"):
            label = at.get("Label")
            t = text_of(at)
            if t:
                abstract_parts.append(f"{label}: {t}" if label else t)
        authors = []
        for au in art.findall("AuthorList/Author"):
            if au.findtext("CollectiveName"):
                authors.append(au.findtext("CollectiveName"))
            elif au.findtext("LastName"):
                authors.append(f"{au.findtext('LastName')} {au.findtext('Initials') or ''}".strip())
        pubtypes = [pt.text for pt in art.findall("PublicationTypeList/PublicationType") if pt.text]
        doi = ""
        for aid in a.findall("PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi":
                doi = aid.text or ""
        pmc = ""
        for aid in a.findall("PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "pmc":
                pmc = aid.text or ""
        disp, iso = parse_date(a)
        out.append({
            "pmid": pmid,
            "title": text_of(art.find("ArticleTitle")),
            "abstract": "\n".join(abstract_parts),
            "authors": authors,
            "journal": art.findtext("Journal/ISOAbbreviation") or art.findtext("Journal/Title") or "",
            "journal_full": art.findtext("Journal/Title") or "",
            "issn": sorted({x.text.strip() for x in art.findall("Journal/ISSN") + mc.findall("MedlineJournalInfo/ISSNLinking") if x is not None and x.text}),
            "pubdate": disp,
            "sortdate": iso,
            "entrez": entrez_date(a),
            "doi": doi,
            "pmc": pmc,
            "pubtypes": pubtypes,
            "type": classify(pubtypes),
        })
    return out


def fetch_details(pmids):
    results = []
    for i in range(0, len(pmids), 200):
        batch = pmids[i:i + 200]
        raw = eutils("efetch.fcgi", {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"})
        results.extend(parse_articles(raw))
        print(f"  取得詳細資料 {min(i + 200, len(pmids))}/{len(pmids)}")
    return results


# ---------------- 影響係數 (Impact Factor) ----------------
OPENALEX = "https://api.openalex.org/sources"
OPENALEX_KEY = os.environ.get("OPENALEX_API_KEY", "").strip()
METRICS_FILE = DATA / "journal_metrics.json"
OVERRIDE_FILE = ROOT / "journal_if.json"
METRICS_MAX_AGE = 30  # 天；超過就重新向 OpenAlex 查詢


def openalex_lookup(issns):
    """回傳 {issn: {"if": 2年平均被引, "name": 期刊名}}；失敗時回傳已查到的部分，不讓整個流程中斷。"""
    found = {}
    issns = list(issns)
    for i in range(0, len(issns), 50):
        batch = issns[i:i + 50]
        params = {"filter": "issn:" + "|".join(batch), "per-page": 200,
                  "select": "display_name,issn,issn_l,summary_stats"}
        if EMAIL:
            params["mailto"] = EMAIL
        if OPENALEX_KEY:
            params["api_key"] = OPENALEX_KEY
        url = OPENALEX + "?" + urllib.parse.urlencode(params)
        for attempt in range(3):
            try:
                time.sleep(0.2)
                req = urllib.request.Request(url, headers={"User-Agent": "anesth-digest/1.0"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    res = json.loads(r.read())
                for src in res.get("results", []):
                    v = (src.get("summary_stats") or {}).get("2yr_mean_citedness")
                    for x in (src.get("issn") or []) + [src.get("issn_l")]:
                        if x and x in batch:
                            found[x] = {"if": v, "name": src.get("display_name")}
                break
            except Exception as e:  # noqa: BLE001
                print(f"  ! OpenAlex 查詢失敗（{e}）", file=sys.stderr)
                time.sleep(3 * (attempt + 1))
    return found


def load_overrides():
    """journal_if.json：手動填入的官方 JCR IF，以期刊縮寫（ISO abbreviation）或 ISSN 為鍵。"""
    if not OVERRIDE_FILE.exists():
        return {}, ""
    try:
        d = json.loads(OVERRIDE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  ! journal_if.json 格式錯誤：{e}", file=sys.stderr)
        return {}, ""
    vals = {k.strip().lower(): v for k, v in (d.get("journals") or {}).items() if isinstance(v, (int, float))}
    return vals, d.get("label", "JCR")


def enrich_all_weeks():
    """替所有週次檔補上 ISSN 與 IF（舊資料沒有 ISSN 時，依 PMID 重新向 PubMed 取得）。"""
    files = sorted(WEEKS.glob("*.json"))
    weeks = {f: json.loads(f.read_text(encoding="utf-8")) for f in files}

    missing = [a["pmid"] for d in weeks.values() for a in d["articles"] if "issn" not in a]
    if missing:
        print(f"補抓舊資料的 ISSN：{len(missing)} 篇")
        got = {a["pmid"]: a for a in fetch_details(missing)}
        for d in weeks.values():
            for a in d["articles"]:
                if "issn" not in a and a["pmid"] in got:
                    a["issn"] = got[a["pmid"]]["issn"]
                    a["journal_full"] = got[a["pmid"]]["journal_full"]

    cache = json.loads(METRICS_FILE.read_text(encoding="utf-8")) if METRICS_FILE.exists() else {}
    today = date.today()
    all_issn = {x for d in weeks.values() for a in d["articles"] for x in a.get("issn", [])}
    stale = [x for x in all_issn
             if x not in cache or (today - date.fromisoformat(cache[x].get("checked", "2000-01-01"))).days > METRICS_MAX_AGE]
    if stale:
        print(f"查詢 OpenAlex 期刊指標：{len(stale)} 個 ISSN")
        found = openalex_lookup(stale)
        for x in stale:
            if x in found:
                cache[x] = {**found[x], "checked": today.isoformat()}
            elif x not in cache:
                cache[x] = {"if": None, "name": None, "checked": today.isoformat()}
    METRICS_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")

    overrides, label = load_overrides()
    for f, d in weeks.items():
        for a in d["articles"]:
            a.pop("if", None); a.pop("if_src", None)
            keys = [a.get("journal", "").lower()] + [x.lower() for x in a.get("issn", [])]
            ov = next((overrides[k] for k in keys if k in overrides), None)
            if ov is not None:
                a["if"], a["if_src"] = round(float(ov), 1), label
                continue
            vals = [cache[x]["if"] for x in a.get("issn", []) if cache.get(x, {}).get("if") is not None]
            if vals:
                a["if"], a["if_src"] = round(max(vals), 1), "OA"
        f.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    if "--enrich-only" in sys.argv:
        # 不抓新文獻，只替既有資料補上／更新 IF
        if WEEKS.exists():
            enrich_all_weeks()
            print("已更新既有資料的 IF")
        return
    if "--auto" in sys.argv:
        # 自動補齊：若「最近一個已結束的週（到上週日）」還沒抓，就抓；否則只更新 IF。
        # 排程延遲、被取消或失敗時，下一次任何執行都會自動補上。
        today = datetime.now(timezone(timedelta(hours=8))).date()
        expected_end = today - timedelta(days=today.weekday() + 1)   # 最近的週日
        ends = []
        for f in WEEKS.glob("*.json") if WEEKS.exists() else []:
            try:
                ends.append(date.fromisoformat(f.stem))
            except ValueError:
                pass
        latest = max(ends) if ends else None
        if latest and latest >= expected_end:
            print(f"本週資料已存在（至 {latest}），只更新 IF")
            enrich_all_weeks()
            return
        gap = (expected_end - latest).days if latest else 28
        os.environ["END_DATE"] = expected_end.isoformat()
        os.environ["DAYS"] = str(min(28, max(7, gap)))
        print(f"缺少至 {expected_end} 的資料，開始抓取（{os.environ['DAYS']} 天）")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    days = int(os.environ.get("DAYS") or 7)
    tpe = timezone(timedelta(hours=8))
    end = (date.fromisoformat(os.environ["END_DATE"]) if os.environ.get("END_DATE")
           else datetime.now(tpe).date() - timedelta(days=1))
    start = end - timedelta(days=days - 1)
    mindate, maxdate = start.strftime("%Y/%m/%d"), end.strftime("%Y/%m/%d")
    print(f"檢索期間（PubMed 收錄日）：{mindate} – {maxdate}")

    cat_of = {}
    counts = {}
    for sp in cfg["subspecialties"]:
        q = sp["query"].replace("{ANES}", cfg["anes_terms"])
        term = f"({q}) AND {cfg['pubtype_filter']}"
        if cfg.get("language_filter"):
            term += f" AND {cfg['language_filter']}"
        ids, total = search(term, mindate, maxdate, cfg.get("max_per_category", 300))
        counts[sp["id"]] = total
        print(f"- {sp['en']}: {total} 篇（取 {len(ids)}）")
        for pid in ids:
            cat_of.setdefault(pid, []).append(sp["id"])

    articles = fetch_details(list(cat_of)) if cat_of else []
    for a in articles:
        a["categories"] = cat_of.get(a["pmid"], [])
    articles.sort(key=lambda a: (a["entrez"], a["sortdate"]), reverse=True)

    week_id = end.isoformat()
    payload = {
        "week": week_id,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "generated": datetime.now(tpe).isoformat(timespec="seconds"),
        "counts": counts,
        "articles": articles,
    }
    WEEKS.mkdir(parents=True, exist_ok=True)
    (WEEKS / f"{week_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 只保留最近 N 週
    files = sorted(WEEKS.glob("*.json"), reverse=True)
    for old in files[cfg.get("keep_weeks", 26):]:
        old.unlink()
    files = sorted(WEEKS.glob("*.json"), reverse=True)

    try:
        enrich_all_weeks()
    except Exception as e:  # noqa: BLE001  IF 只是加值資訊，失敗不影響主要更新
        print(f"  ! 加入 IF 失敗，略過：{e}", file=sys.stderr)

    weeks = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        weeks.append({"week": d["week"], "from": d["from"], "to": d["to"], "total": len(d["articles"])})
    index = {
        "updated": payload["generated"],
        "subspecialties": [{k: sp[k] for k in ("id", "en", "zh")} for sp in cfg["subspecialties"]],
        "weeks": weeks,
    }
    (DATA / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成：{len(articles)} 篇不重複文獻 → data/weeks/{week_id}.json")


if __name__ == "__main__":
    main()
