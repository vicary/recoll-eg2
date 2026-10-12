from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from recoll_eg2.config import Config
from recoll_eg2.encoder import DeterministicTestEncoder
from recoll_eg2.indexer import IncompleteEnumeration, Indexer, enumerate_source, hash_file
from recoll_eg2.policy import denied_name
from recoll_eg2.service import SearchService
from recoll_eg2.store import Store


class FakeRecoll:
    def __init__(self, source): self.source=source; self.names={}; self.stage=None; self.fail_index=False; self.seen=[]
    def configure(self, stage, names): self.stage=stage; self.names=dict(names); self.seen=list(stage.iterdir())
    def index(self):
        if self.fail_index: raise RuntimeError("partial scan")
    def extract_text(self,path,max_chars): return (self.stage/path).read_text()[:max_chars]
    def search(self,q,limit):
        return [{"path":p.relative_to(self.source).as_posix(),"snippet":p.read_text()[:500],"title":p.name}
                for p in self.source.rglob("*.txt") if q in p.read_text()][:limit]
    def release_stage(self,stage):
        import shutil; shutil.rmtree(stage,ignore_errors=True)


class FailingEncoder(DeterministicTestEncoder):
    def encode(self,texts): raise RuntimeError("synthetic encoder failure")


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); base=Path(self.tmp.name)
        self.source=base/"source"; self.state=base/"state"; self.source.mkdir(); self.state.mkdir()
        self.config=Config(source=self.source,state=self.state,min_free_bytes=1,chunk_chars=128,chunk_overlap=0,allow_empty_purge=True)
        self.store=Store(self.state/"vectors.sqlite3"); self.recoll=FakeRecoll(self.source)
    def tearDown(self): self.store.close(); self.tmp.cleanup()
    def indexer(self,encoder=None): return Indexer(self.config,self.store,self.recoll,encoder or DeterministicTestEncoder())
    def write(self,name,text):
        path=self.source/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text); return path

    def test_exclusions_and_sqlite_magic_before_recoll(self):
        denied=[".hidden", ".env", "prod.env", "x.env.local", "a.pem", "id_rsa2", "credentials-old",
                "secrets.txt", "tokens.json", "x.db", "x.sqlite3-wal", "a.zip"]
        for name in denied: self.write(name,"SHOULD_NOT_LEAK")
        self.write("misleading.txt", "SQLite format 3\x00SECRET")
        self.write("ok.txt","public")
        self.write("blocked/.recoll-noindex",""); self.write("blocked/note.txt","SECRET")
        result=self.indexer().scan()
        self.assertEqual(result["eligible"],1)
        self.assertEqual(len(self.recoll.seen),1)
        self.assertNotIn("ok.txt",self.recoll.seen[0].name)
        self.assertEqual(set(self.store.known()),{"ok.txt"})

    def test_all_required_name_patterns(self):
        for name in ("foo.p12","foo.pfx","id_ed25519_work","credentials","secrets.yaml","tokens-backup","x.sqlite-shm"):
            self.assertTrue(denied_name(name),name)

    def test_same_size_update_invalidates_then_republishes(self):
        p=self.write("a.txt","alpha")
        self.indexer().scan(); first=self.store.known()["a.txt"]["generation"]
        p.write_text("bravo"); self.indexer().scan(); row=self.store.known()["a.txt"]
        self.assertNotEqual(first,row["generation"]); self.assertTrue(row["visible"])

    def test_shortened_file_purges_leftover_chunks(self):
        p=self.write("a.txt","x"*400); self.indexer().scan()
        self.assertGreater(self.store.db.execute("select count(*) from chunks").fetchone()[0],2)
        p.write_text("short"); self.indexer().scan()
        self.assertEqual(self.store.db.execute("select count(*) from chunks").fetchone()[0],1)

    def test_delete_rename_and_now_excluded_purge(self):
        p=self.write("old.txt","hello"); self.indexer().scan()
        p.rename(self.source/"new.txt"); self.indexer().scan()
        self.assertEqual(set(self.store.known()),{"new.txt"})
        (self.source/"new.txt").rename(self.source/"secrets.txt"); self.indexer().scan()
        self.assertEqual(self.store.known(),{}); self.assertEqual(self.store.db.execute("select count(*) from chunks").fetchone()[0],0)

    def test_failed_reembedding_never_resurrects_old(self):
        p=self.write("a.txt","first"); self.indexer().scan(); p.write_text("other")
        self.indexer(FailingEncoder()).scan(); row=self.store.known()["a.txt"]
        self.assertFalse(row["visible"]); self.assertEqual(row["status"],"retry")
        self.assertEqual(self.store.semantic([1]*16,10),[])

    def test_edit_during_encoding_does_not_publish(self):
        p=self.write("a.txt","first")
        class Racing(DeterministicTestEncoder):
            def encode(inner,texts): p.write_text("later"); return super().encode(texts)
        self.indexer(Racing()).scan()
        self.assertFalse(self.store.known()["a.txt"]["visible"])
        self.assertEqual(self.store.meta("scope_status"), "degraded")
        self.assertEqual(self.store.known()["a.txt"]["status"], "retry")

    def test_restart_removes_interrupted_staging(self):
        self.store.db.execute("insert into generations values('bad','a','h','staging',0)")
        self.store.db.execute("insert into chunks values('bad',0,'SECRET','[1]')"); self.store.db.commit()
        self.store.recover()
        self.assertEqual(self.store.db.execute("select count(*) from generations").fetchone()[0],0)

    def test_missing_root_and_partial_enumeration_never_delete(self):
        self.write("a.txt","first"); self.indexer().scan()
        missing=replace(self.config,source=self.source/"missing")
        with self.assertRaises(IncompleteEnumeration): Indexer(missing,self.store,self.recoll,DeterministicTestEncoder()).scan()
        self.assertIn("a.txt",self.store.known())
        self.recoll.fail_index=True
        (self.source/"a.txt").unlink()
        with self.assertRaises(RuntimeError): self.indexer().scan()
        self.assertNotIn("a.txt",self.store.known())

    def test_empty_source_requires_explicit_purge_authorization(self):
        self.write("a.txt","first"); self.indexer().scan(); (self.source/"a.txt").unlink()
        guarded=replace(self.config,allow_empty_purge=False)
        with self.assertRaises(IncompleteEnumeration): Indexer(guarded,self.store,self.recoll,DeterministicTestEncoder()).scan()
        self.assertIn("a.txt",self.store.known())

    def test_root_marker_and_casefold_sqlite_patterns(self):
        self.assertTrue(denied_name("DATA.SQLITE-WAL")); self.write("a.txt","secret")
        self.write(".recoll-noindex","")
        guarded=replace(self.config,allow_empty_purge=False)
        with self.assertRaises(IncompleteEnumeration): Indexer(guarded,self.store,self.recoll,DeterministicTestEncoder()).scan()

    def test_source_identity_replacement_does_not_purge(self):
        self.write("a.txt","first"); self.indexer().scan()
        old=self.source.rename(self.source.with_name("old-source")); self.source.mkdir(); self.write("b.txt","second")
        with self.assertRaises(IncompleteEnumeration): self.indexer().scan()
        self.assertIn("a.txt",self.store.known())

    def test_enumeration_limits_fail_closed(self):
        self.write("a/b/c.txt","deep")
        with self.assertRaises(IncompleteEnumeration): enumerate_source(replace(self.config,max_depth=1))
        with self.assertRaises(IncompleteEnumeration): enumerate_source(replace(self.config,max_files=0))

    def test_input_limits_and_content_hash(self):
        p=self.write("a.txt","12345")
        with self.assertRaises(ValueError): hash_file(self.source,"a.txt",4)
        one=hash_file(self.source,"a.txt",10); p.write_text("54321")
        self.assertNotEqual(one,hash_file(self.source,"a.txt",10))

    def test_lexical_and_semantic_generation_filtering(self):
        p=self.write("a.txt","needle"); ix=self.indexer(); ix.scan()
        service=SearchService(self.config,self.store,self.recoll,DeterministicTestEncoder(),ix)
        self.assertTrue(service.search("needle",10)["results"])
        self.write(".recoll-noindex","")
        self.assertEqual(service.search("needle",10)["results"],[])
        (self.source/".recoll-noindex").unlink()
        self.store.invalidate("a.txt","new")
        self.assertEqual(service.search("needle",10)["results"],[])
        self.store.set_meta("scope_status","unavailable")
        self.assertEqual(service.search("needle",10)["results"],[])

if __name__ == "__main__": unittest.main()
