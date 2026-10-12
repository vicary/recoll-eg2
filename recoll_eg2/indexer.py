from __future__ import annotations

import hashlib
import os
import shutil
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .policy import MARKER, denied_name, eligible_relative, forbidden_magic, supported_name

class IncompleteEnumeration(RuntimeError): pass

@dataclass(frozen=True)
class Candidate:
    path: str
    digest: str
    staged_name: str

def _open_root(root: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try: return os.open(root, flags)
    except OSError as exc: raise IncompleteEnumeration(f"source root unavailable: {exc}") from exc

def _open_relative(rootfd: int, rel: str) -> int:
    if not eligible_relative(rel): raise OSError("ineligible path")
    try: os.stat(MARKER, dir_fd=rootfd, follow_symlinks=False)
    except FileNotFoundError: pass
    else: raise OSError("excluded root")
    current = os.dup(rootfd)
    try:
        parts = rel.split("/")
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
            os.close(current); current = nxt
            try: os.stat(MARKER, dir_fd=current, follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise OSError("excluded directory")
        return os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), dir_fd=current)
    finally: os.close(current)

def hash_file(root: Path, rel: str, max_bytes: int) -> str:
    rootfd = _open_root(root)
    try: fd = _open_relative(rootfd, rel)
    finally: os.close(rootfd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode): raise OSError("not a regular file")
        head = os.read(fd, 16)
        if forbidden_magic(head): raise OSError("excluded content type")
        h=hashlib.sha256(head); total=len(head)
        if total > max_bytes: raise ValueError("file exceeds MAX_FILE_BYTES")
        while True:
            block=os.read(fd, 128*1024)
            if not block: break
            total += len(block)
            if total > max_bytes: raise ValueError("file exceeds MAX_FILE_BYTES")
            h.update(block)
        after=os.fstat(fd)
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
            raise OSError("file changed while reading")
        return h.hexdigest()
    finally: os.close(fd)

def enumerate_source(config: Config, stage: Path | None = None) -> tuple[dict[str,Candidate], tuple[int,int]]:
    """Copy approved bytes to safe names. No parser ever receives a source path."""
    rootfd=_open_root(config.source)
    identity=(os.fstat(rootfd).st_dev,os.fstat(rootfd).st_ino)
    output=stage
    if output: output.mkdir(parents=True, exist_ok=False)
    candidates: dict[str,Candidate]={}
    def walk(dirfd: int, prefix: str, depth: int):
        try: os.stat(MARKER, dir_fd=dirfd, follow_symlinks=False)
        except FileNotFoundError: pass
        else: return
        try: entries=sorted(os.scandir(dirfd), key=lambda e:e.name)
        except OSError as exc: raise IncompleteEnumeration(f"directory enumeration failed: {prefix or '.'}") from exc
        for entry in entries:
            name=entry.name
            if denied_name(name): continue
            rel=f"{prefix}/{name}" if prefix else name
            try:
                st=entry.stat(follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    if depth >= config.max_depth: raise IncompleteEnumeration("MAX_DEPTH exceeded")
                    child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|getattr(os,"O_NOFOLLOW",0),dir_fd=dirfd)
                    try: walk(child,rel,depth+1)
                    finally: os.close(child)
                elif stat.S_ISREG(st.st_mode) and supported_name(name):
                    fd=os.open(name,os.O_RDONLY|os.O_NONBLOCK|getattr(os,"O_NOFOLLOW",0),dir_fd=dirfd)
                    try:
                        before=os.fstat(fd)
                        if not stat.S_ISREG(before.st_mode):
                            raise IncompleteEnumeration(f"opened source is not a regular file: {rel}")
                        head=os.read(fd,16)
                        if forbidden_magic(head): continue
                        h=hashlib.sha256(head); total=len(head); blocks=[head]
                        while True:
                            block=os.read(fd,128*1024)
                            if not block: break
                            total+=len(block)
                            if total>config.max_file_bytes: raise IncompleteEnumeration(f"file too large: {rel}")
                            blocks.append(block); h.update(block)
                        after=os.fstat(fd)
                        sig=lambda x:(x.st_dev,x.st_ino,x.st_size,x.st_mtime_ns,x.st_ctime_ns)
                        if sig(before)!=sig(after): raise IncompleteEnumeration(f"file changed while reading: {rel}")
                        data=b"".join(blocks)
                        if forbidden_magic(data[:16]): continue
                        digest=h.hexdigest(); suffix=Path(name).suffix.casefold()
                        safe=f"{len(candidates):08x}-{digest}{suffix}"
                        if output:
                            stagefd=os.open(output,os.O_RDONLY|os.O_DIRECTORY)
                            try: outfd=os.open(safe,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600,dir_fd=stagefd)
                            finally: os.close(stagefd)
                            try:
                                view=memoryview(data)
                                while view: view=view[os.write(outfd,view):]
                            finally: os.close(outfd)
                        candidates[rel]=Candidate(rel,digest,safe)
                        if len(candidates)>config.max_files: raise IncompleteEnumeration("MAX_FILES exceeded")
                    finally: os.close(fd)
            except IncompleteEnumeration: raise
            except OSError as exc: raise IncompleteEnumeration(f"unsafe source entry: {rel}: {exc}") from exc
    try: walk(rootfd,"",0)
    finally: os.close(rootfd)
    return candidates,identity

def chunks(text,size,overlap):
    step=max(1,size-overlap)
    return [text[i:i+size] for i in range(0,len(text),step) if text[i:i+size].strip()]

class Indexer:
    def __init__(self,config,store,recoll,encoder): self.config,self.store,self.recoll,self.encoder=config,store,recoll,encoder
    def scan(self):
        self.store.recover(); stages=self.config.state/"stages"; stages.mkdir(exist_ok=True)
        nextstage=stages/("scan-"+uuid.uuid4().hex)
        try: current,identity=enumerate_source(self.config,nextstage)
        except Exception as exc:
            shutil.rmtree(nextstage,ignore_errors=True); self.store.set_meta("scope_status","unavailable"); self.store.set_meta("last_error",str(exc)); raise
        expected=self.store.meta("source_identity")
        actual=f"{identity[0]}:{identity[1]}"
        if expected and expected!=actual:
            shutil.rmtree(nextstage); self.store.set_meta("scope_status","unavailable"); self.store.set_meta("last_error","source root identity changed"); raise IncompleteEnumeration("source root identity changed; state reset or explicit migration required")
        if not current and not self.config.allow_empty_purge:
            shutil.rmtree(nextstage); self.store.set_meta("scope_status","unavailable"); self.store.set_meta("last_error","empty source rejected"); raise IncompleteEnumeration("empty source rejected; set ALLOW_EMPTY_PURGE=1 explicitly")
        if not expected: self.store.set_meta("source_identity",actual)
        known=self.store.known(); now=time.time()
        changed=[c for p,c in current.items() if p not in known or known[p]["content_hash"]!=c.digest or (not known[p]["visible"] and known[p]["status"]!="quarantine" and known[p]["retry_at"]<=now)]
        removed=set(known)-set(current)
        for c in changed: self.store.invalidate(c.path,c.digest)
        for path in removed: self.store.purge(path)
        try:
            self.recoll.configure(nextstage,{c.staged_name:c.path for c in current.values()})
            self.recoll.index()
            degraded=False; low_space=False
            for c in changed:
                try:
                    if shutil.disk_usage(self.config.state).free<self.config.min_free_bytes:
                        low_space=True
                        raise OSError("insufficient free state storage")
                    text=self.recoll.extract_text(c.staged_name,self.config.max_text_chars)
                    pieces=chunks(text,self.config.chunk_chars,self.config.chunk_overlap)
                    if len(pieces)>self.config.max_chunks_per_file: raise ValueError("file exceeds MAX_CHUNKS_PER_FILE")
                    vectors=self.encoder.encode(pieces) if pieces else []
                    if len(vectors)!=len(pieces): raise ValueError("encoder result count mismatch")
                    for vector in vectors:
                        if not vector or not all(isinstance(x,(int,float)) and __import__('math').isfinite(x) for x in vector): raise ValueError("invalid embedding vector")
                    if vectors and any(len(v)!=len(vectors[0]) for v in vectors): raise ValueError("embedding dimension mismatch")
                    published=self.store.publish(c.path,c.digest,list(zip(pieces,vectors)),lambda p=c.path:hash_file(self.config.source,p,self.config.max_file_bytes))
                    if not published: raise RuntimeError("source changed before publication")
                except Exception as exc:
                    degraded=True; self.store.fail(c.path,f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            self.store.set_meta("scope_status","unavailable"); self.store.set_meta("last_error",f"{type(exc).__name__}: {exc}"); raise
        finally: self.recoll.release_stage(nextstage)
        self.store.set_meta("scope_status","low_space" if low_space else ("degraded" if degraded else "ready"))
        self.store.set_meta("last_scan",str(int(time.time())))
        if not degraded: self.store.set_meta("last_error","")
        return {"eligible":len(current),"changed":len(changed),"removed":len(removed)}
