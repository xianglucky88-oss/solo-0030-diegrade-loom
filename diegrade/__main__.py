import argparse

from .database import Database
from .demo import install
from .httpd import make_server


def main():
    options = argparse.ArgumentParser(description='晶格分档台')
    options.add_argument('--port', type=int, default=8736)
    options.add_argument('--db', default='.diegrade-data/station.sqlite3')
    options.add_argument('--demo', action='store_true', help='装入演示晶圆')
    args = options.parse_args()
    database = Database(args.db)
    if args.demo:
        with database.transaction(write=True) as conn:
            install(conn)
    with make_server(database, args.port) as server:
        print(f'晶格分档台：http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
