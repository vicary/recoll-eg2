"""Run inside the built container with real Recoll and full LiteRT model."""
import os, subprocess, tempfile, time, urllib.request, json
from pathlib import Path

with tempfile.TemporaryDirectory() as root:
    source=Path(root)/"source"; state=Path(root)/"state"; source.mkdir(); state.mkdir()
    (source/"note.txt").write_text("A kingfisher is a brightly colored river bird.")
    env={**os.environ,"SOURCE_ROOT":str(source),"STATE_ROOT":str(state),"MIN_FREE_BYTES":"1048576"}
    subprocess.run(["recoll-eg2","scan"],env=env,check=True,timeout=900)
    proc=subprocess.Popen(["recoll-eg2","serve"],env={**env,"PORT":"18787","SCAN_SECONDS":"3600"})
    try:
        for _ in range(120):
            try:
                data=json.load(urllib.request.urlopen("http://127.0.0.1:18787/search?q=river+bird",timeout=2))
                assert any(x["path"]=="note.txt" for x in data["results"]),data; break
            except Exception: time.sleep(2)
        else: raise AssertionError("service/model did not become ready")
    finally: proc.terminate(); proc.wait(timeout=20)
print("real Recoll + LiteRT EmbeddingGemma 2 smoke: PASS")
