import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from recoll_eg2.config import Config
from recoll_eg2.indexer import IncompleteEnumeration, enumerate_source


class SourceBoundaryTests(unittest.TestCase):
    def test_sqlite_probe_stops_after_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'renamed.txt').write_bytes(b'SQLite format 3\x00'+b'x'*100000)
            calls=[]
            read=os.read
            def checked(fd,n):
                calls.append(n)
                return read(fd,n)
            with patch('recoll_eg2.indexer.os.read',side_effect=checked):
                candidates,_=enumerate_source(Config(source=root))
            self.assertEqual(candidates,{})
            self.assertEqual(calls,[16])

    def test_fifo_replacement_fails_without_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'a.txt'; source.write_text('ordinary')
            opened=os.open
            def race(name,flags,*args,**kwargs):
                if name=='a.txt' and kwargs.get('dir_fd') is not None:
                    source.unlink(); os.mkfifo(source)
                    self.assertTrue(flags & os.O_NONBLOCK)
                return opened(name,flags,*args,**kwargs)
            with patch('recoll_eg2.indexer.os.open',side_effect=race):
                with self.assertRaises(IncompleteEnumeration):
                    enumerate_source(Config(source=root))
