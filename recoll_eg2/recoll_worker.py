"""Isolated Recoll bindings worker; parent enforces time/output/process bounds."""
import json
import os
import resource
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


def main():
    request = json.load(sys.stdin)
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (request["cpu_seconds"], request["cpu_seconds"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (request["max_output"], request["max_output"]))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if request["action"] == "index":
        os.execvp("recollindex", ["recollindex", "-c", request["conf"]])
    from recoll import recoll, rclextract
    db = recoll.connect(confdir=request["conf"])
    query = db.query()
    query.execute(request["query"])
    if request["action"] == "extract":
        try:
            doc = query.fetchone()
        except StopIteration:
            doc = None
        if doc is None:
            raise RuntimeError("staged document missing from Recoll index")
        actual = Path(unquote(urlparse(doc.url).path)).name
        if actual != request["name"]:
            raise RuntimeError("Recoll returned a different staged document")
        extracted = rclextract.Extractor(doc).textextract(doc.ipath)
        result = {"text": (extracted.text or "")[:request["max_chars"]]}
    else:
        result = []
        for _ in range(request["limit"]):
            try:
                doc = query.fetchone()
            except StopIteration:
                break
            if doc is None:
                break
            result.append({"url": doc.url, "title": (getattr(doc, "title", "") or "")[:500],
                           "snippet": (getattr(doc, "abstract", "") or "")[:500]})
    json.dump(result, sys.stdout, ensure_ascii=True)


if __name__ == "__main__":
    main()
