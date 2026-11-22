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


def main():
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
