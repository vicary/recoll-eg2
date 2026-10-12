from __future__ import annotations

import argparse
from pathlib import Path

from .config import Config
from .encoder import CommandEncoder, LiteRTEncoder
from .indexer import Indexer
from .recoll import Recoll
from .service import SearchService
from .store import Store


def build(config):
    config.state.mkdir(parents=True, exist_ok=True)
    store=Store(config.state/"vectors.sqlite3")
    recoll=Recoll(config.state,config.source,config.recoll_timeout)
    encoder=(CommandEncoder(config.encoder_command,config.model_path,config.encode_timeout)
             if config.encoder_command else LiteRTEncoder(config.model_path))
    return store,recoll,encoder,Indexer(config,store,recoll,encoder)

def main(argv=None):
    parser=argparse.ArgumentParser(prog="recoll-eg2")
    parser.add_argument("command",choices=("serve","scan","status"),nargs="?",default="serve")
    args=parser.parse_args(argv); config=Config.from_env()
    store,recoll,encoder,indexer=build(config)
    try:
        if args.command=="scan": print(indexer.scan())
        elif args.command=="status": print(store.status())
        else:
            token=""
            token_file=__import__('os').environ.get("AUTH_TOKEN_FILE","")
            if token_file: token=Path(token_file).read_text(encoding="utf-8").strip()
            SearchService(config,store,recoll,encoder,indexer,token).serve()
    finally:
        if hasattr(encoder,"close"): encoder.close()
        store.close()

if __name__ == "__main__": main()
